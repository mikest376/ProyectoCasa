"""Destinos de los lotes: Delta Lake en la capa Bronze."""

import logging
import time
from collections import Counter
from collections.abc import Callable, Collection, Sequence
from datetime import UTC, datetime
from typing import Protocol

import polars as pl
from deltalake import DeltaTable, WriterProperties, write_deltalake

from medallion_worker.batching import TopicBatch
from medallion_worker.events import BronzeEvent, RejectedEvent, table_name
from medallion_worker.storage import Columns, delta_schema

logger = logging.getLogger(__name__)

# Un topic nunca produce una tabla con este nombre: Odoo no crea tablas con guion bajo inicial.
REJECTED_TABLE = "_rejected"
PARTITION = "_ingested_date"

EVENT_COLUMNS: dict[str, pl.DataType] = {
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
REJECTED_COLUMNS: dict[str, pl.DataType] = {
    "_topic": pl.String(),
    "_partition": pl.Int32(),
    "_offset": pl.Int64(),
    "_ingested_at": pl.Datetime("us", "UTC"),
    "reason": pl.String(),
    "key": pl.Binary(),
    "value": pl.Binary(),
}
REQUIRED = frozenset({"_topic", "_partition", "_offset", "_ingested_at", "op", "reason"})

TABLE_PROPERTIES = {
    # Regla 4 de CLAUDE.md: Bronze no se modifica. El protocolo Delta rechaza UPDATE y DELETE.
    "delta.appendOnly": "true",
    # El usuario de MinIO del worker no puede borrar en Bronze (TD-014). De la limpieza se
    # encarga bronze-maintenance, con su propio usuario (TD-007).
    "delta.enableExpiredLogCleanup": "false",
}


class BronzeSink(Protocol):
    def write(self, batch: TopicBatch) -> None:
        """Al volver, el lote está a salvo: el consumidor puede confirmar sus offsets."""


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


def with_partition(frame: pl.DataFrame) -> pl.DataFrame:
    return frame.with_columns(pl.col("_ingested_at").dt.date().alias(PARTITION))


def _columns(types: dict[str, pl.DataType]) -> Columns:
    return [(name, dtype, "") for name, dtype in types.items()]


class DeltaSink:
    """Una tabla Delta por topic, en modo append-only: <base>/<tabla>.

    Las tablas de mayor volumen se crean particionadas por fecha de ingesta (TD-007). En una
    tabla que ya existe manda su partición real, no la configuración.
    """

    def __init__(
        self,
        base_uri: str,
        options: dict[str, str] | None = None,
        now: Callable[[], datetime] = lambda: datetime.now(tz=UTC),
        partitioned: Collection[str] = (),
    ) -> None:
        self._base_uri = base_uri.rstrip("/")
        self._options = options
        self._now = now
        self._partitioned = frozenset(partitioned)
        # Tabla -> si está particionada.
        self._known: dict[str, bool] = {}

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

    def _open(self, table: str, uri: str, columns: dict[str, pl.DataType], origin: str) -> bool:
        partition = table in self._partitioned
        types = columns | {PARTITION: pl.Date()} if partition else columns
        DeltaTable.create(
            uri,
            schema=delta_schema(_columns(types), REQUIRED),
            mode="ignore",
            partition_by=[PARTITION] if partition else None,
            name=table,
            description=f"Bronze: eventos CDC crudos de {origin}",
            configuration=TABLE_PROPERTIES,
            storage_options=self._options,
        )
        existing = DeltaTable(uri, storage_options=self._options)
        return existing.metadata().partition_columns == [PARTITION]

    def _append(
        self, table: str, columns: dict[str, pl.DataType], frame: pl.DataFrame, origin: str
    ) -> None:
        uri = f"{self._base_uri}/{table}"
        if table not in self._known:
            self._known[table] = self._open(table, uri, columns, origin)
        write_deltalake(
            uri,
            with_partition(frame) if self._known[table] else frame,
            mode="append",
            storage_options=self._options,
            writer_properties=WriterProperties(compression="ZSTD"),
        )
