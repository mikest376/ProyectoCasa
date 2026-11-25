"""Destinos de los lotes: Delta Lake en la capa Bronze."""

import logging
import time
from collections import Counter
from collections.abc import Callable, Sequence
from datetime import UTC, datetime
from typing import Protocol

import polars as pl
from deltalake import DeltaTable, WriterProperties, write_deltalake
from deltalake.schema import Field, PrimitiveType, Schema

from medallion_worker.batching import TopicBatch
from medallion_worker.config import Settings
from medallion_worker.events import BronzeEvent, RejectedEvent, table_name

logger = logging.getLogger(__name__)

# Un topic nunca produce una tabla con este nombre: Odoo no crea tablas con guion bajo inicial.
REJECTED_TABLE = "_rejected"

EVENT_COLUMNS = {
    "_topic": pl.String(),
    "_partition": pl.Int32(),
    "_offset": pl.Int64(),
    "_ingested_at": pl.Datetime("us", "UTC"),
    "op": pl.String(),
    "source_lsn": pl.Int64(),
    "source_ts_ms": pl.Int64(),
    "key": pl.String(),
    "before": pl.String(),
    "after": pl.String(),
}
REJECTED_COLUMNS = {
    "_topic": pl.String(),
    "_partition": pl.Int32(),
    "_offset": pl.Int64(),
    "_ingested_at": pl.Datetime("us", "UTC"),
    "reason": pl.String(),
    "key": pl.Binary(),
    "value": pl.Binary(),
}
DELTA_TYPES = {
    pl.String(): "string",
    pl.Int32(): "integer",
    pl.Int64(): "long",
    pl.Binary(): "binary",
    pl.Datetime("us", "UTC"): "timestamp",
}
REQUIRED = frozenset({"_topic", "_partition", "_offset", "_ingested_at", "op", "reason"})

TABLE_PROPERTIES = {
    # Regla 4 de CLAUDE.md: Bronze no se modifica. El protocolo Delta rechaza UPDATE y DELETE.
    "delta.appendOnly": "true",
    # El usuario de MinIO del worker no puede borrar en Bronze (TD-014). La limpieza del
    # registro y la compactación llegan en la Fase 3 (TD-007).
    "delta.enableExpiredLogCleanup": "false",
}


class BronzeSink(Protocol):
    def write(self, batch: TopicBatch) -> None:
        """Al volver, el lote está a salvo: el consumidor puede confirmar sus offsets."""


def storage_options(settings: Settings) -> dict[str, str]:
    return {
        "AWS_ENDPOINT_URL": settings.s3_endpoint_url,
        "AWS_REGION": settings.s3_region,
        "AWS_ACCESS_KEY_ID": settings.s3_access_key_id.get_secret_value(),
        "AWS_SECRET_ACCESS_KEY": settings.s3_secret_access_key.get_secret_value(),
        # Dentro de la red de Docker no hay TLS (TD-014).
        "AWS_ALLOW_HTTP": str(settings.s3_endpoint_url.startswith("http://")).lower(),
        "AWS_VIRTUAL_HOSTED_STYLE_REQUEST": "false",
        # Escritura condicional: dos commits con el mismo número de versión no se pisan.
        "aws_conditional_put": "etag",
    }


def event_frame(events: Sequence[BronzeEvent], ingested_at: datetime) -> pl.DataFrame:
    return pl.DataFrame(
        {
            "_topic": [event.topic for event in events],
            "_partition": [event.partition for event in events],
            "_offset": [event.offset for event in events],
            "_ingested_at": [ingested_at] * len(events),
            "op": [event.op for event in events],
            "source_lsn": [event.source_lsn for event in events],
            "source_ts_ms": [event.source_ts_ms for event in events],
            "key": [event.key for event in events],
            "before": [event.before for event in events],
            "after": [event.after for event in events],
        },
        schema=EVENT_COLUMNS,
    )


def rejected_frame(events: Sequence[RejectedEvent], ingested_at: datetime) -> pl.DataFrame:
    return pl.DataFrame(
        {
            "_topic": [event.topic for event in events],
            "_partition": [event.partition for event in events],
            "_offset": [event.offset for event in events],
            "_ingested_at": [ingested_at] * len(events),
            "reason": [event.reason for event in events],
            "key": [event.key for event in events],
            "value": [event.value for event in events],
        },
        schema=REJECTED_COLUMNS,
    )


def delta_schema(columns: dict[str, pl.DataType]) -> Schema:
    return Schema(
        [
            Field(name, PrimitiveType(DELTA_TYPES[dtype]), nullable=name not in REQUIRED)
            for name, dtype in columns.items()
        ]
    )


class DeltaSink:
    """Una tabla Delta por topic, en modo append-only: <base>/<tabla>."""

    def __init__(
        self,
        base_uri: str,
        options: dict[str, str] | None = None,
        now: Callable[[], datetime] = lambda: datetime.now(tz=UTC),
    ) -> None:
        self._base_uri = base_uri.rstrip("/")
        self._options = options
        self._now = now
        self._known: set[str] = set()

    def write(self, batch: TopicBatch) -> None:
        started = time.monotonic()
        ingested_at = self._now()
        table = table_name(batch.topic)
        if batch.events:
            self._append(table, EVENT_COLUMNS, event_frame(batch.events, ingested_at), batch.topic)
        if batch.rejected:
            self._append(
                REJECTED_TABLE,
                REJECTED_COLUMNS,
                rejected_frame(batch.rejected, ingested_at),
                "mensajes no interpretables",
            )
        logger.info(
            "lote escrito",
            extra={
                "fields": {
                    "table": table,
                    "events": len(batch.events),
                    "rejected": len(batch.rejected),
                    "operations": dict(Counter(event.op for event in batch.events)),
                    "seconds": round(time.monotonic() - started, 3),
                }
            },
        )

    def _append(
        self, table: str, columns: dict[str, pl.DataType], frame: pl.DataFrame, origin: str
    ) -> None:
        uri = f"{self._base_uri}/{table}"
        if table not in self._known:
            DeltaTable.create(
                uri,
                schema=delta_schema(columns),
                mode="ignore",
                name=table,
                description=f"Bronze: eventos CDC crudos de {origin}",
                configuration=TABLE_PROPERTIES,
                storage_options=self._options,
            )
            self._known.add(table)
        write_deltalake(
            uri,
            frame,
            mode="append",
            storage_options=self._options,
            writer_properties=WriterProperties(compression="ZSTD"),
        )
