"""Bronze -> Silver: estado actual, tipado y sin duplicados, de las tablas core.

Incremental e idempotente. La marca de agua es el mayor _ingested_at aplicado y se guarda en la
propia tabla Delta (transacción de aplicación), en el mismo commit que los datos.
"""

import json
import logging
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

import polars as pl
from deltalake import CommitProperties, DeltaTable, Transaction

from medallion_worker.contracts import METADATA, Contract
from medallion_worker.decoding import ContractError, Decoder
from medallion_worker.storage import Lake, delta_schema

logger = logging.getLogger(__name__)

# Los relojes pueden retroceder un poco: se vuelve a leer un margen y el LSN descarta lo repetido.
OVERLAP = timedelta(minutes=10)
WATERMARK = "bronze_ingested_at"


@dataclass(frozen=True, slots=True)
class SilverResult:
    table: str
    read: int
    applied: int


def silver_columns(contract: Contract) -> list[tuple[str, pl.DataType, str]]:
    return [
        *((c.name, contract.schema[c.name], c.description) for c in contract.columns),
        *METADATA,
    ]


def silver_schema(contract: Contract) -> dict[str, pl.DataType]:
    return {name: dtype for name, dtype, _ in silver_columns(contract)}


def _position(row: dict[str, Any]) -> tuple[int, int]:
    return row["_source_lsn"], row["_bronze_offset"]


def apply_events(
    contract: Contract,
    decoder: Decoder,
    events: pl.DataFrame,
    current: dict[int, dict[str, Any]],
    now: datetime,
) -> dict[int, dict[str, Any]]:
    """Aplica los eventos en orden sobre el estado actual. Devuelve solo las filas que cambian.

    El orden lo da el LSN, no la llegada. El offset desempata, y las coordenadas de Kafka no
    bastan para identificar un evento: si se recrea el clúster, se repiten (TD-010).
    """
    ordered = events.unique(
        ["_topic", "_partition", "_offset", "source_lsn"], keep="first", maintain_order=True
    ).sort("source_lsn", "source_ts_ms", "_offset")

    changed: dict[int, dict[str, Any]] = {}
    for event in ordered.iter_rows(named=True):
        key = _primary_key(contract, event["key"])
        state = changed.get(key) or current.get(key)
        position = (event["source_lsn"], event["_offset"])
        if state is not None and position <= _position(state):
            continue
        # Un borrado conserva los últimos valores: solo marca la fila.
        values = {} if event["op"] == "d" else decoder.row(_payload(contract, event["after"]))
        row = dict.fromkeys(contract.names) if state is None else dict(state)
        row |= values
        row |= {
            "id": key,
            "_is_deleted": event["op"] == "d",
            "_op": event["op"],
            "_source_lsn": event["source_lsn"],
            "_source_ts": _source_time(event["source_ts_ms"]),
            "_bronze_offset": event["_offset"],
            "_updated_at": now,
        }
        changed[key] = row
    return changed


def _primary_key(contract: Contract, text: str | None) -> int:
    key = json.loads(text) if text else None
    value = key.get("id") if isinstance(key, dict) else None
    if isinstance(value, bool) or not isinstance(value, int):
        raise ContractError(contract.table, "evento sin clave primaria")
    return value


def _payload(contract: Contract, text: str | None) -> dict[str, Any]:
    payload = json.loads(text) if text else None
    if not isinstance(payload, dict):
        raise ContractError(contract.table, "evento sin contenido")
    return payload


def _source_time(milliseconds: int | None) -> datetime | None:
    if milliseconds is None:
        return None
    return datetime.fromtimestamp(0, tz=UTC) + timedelta(milliseconds=milliseconds)


def refresh(lake: Lake, contract: Contract, language: str, now: datetime) -> SilverResult:
    """Lleva a Silver los cambios de una tabla que aún no se han aplicado."""
    uri = lake.silver(contract.domain, contract.table)
    table = lake.open_or_create(
        uri,
        delta_schema(silver_columns(contract), required={"id", "_source_lsn", "_bronze_offset"}),
        contract.table,
        contract.description,
    )
    mark = table.transaction_version(WATERMARK)
    since = None if mark is None else _from_micros(mark) - OVERLAP
    events = lake.read_bronze(contract.table, since)
    if events.is_empty():
        return SilverResult(contract.table, 0, 0)
    if events["source_lsn"].null_count():
        raise ContractError(contract.table, "eventos sin source_lsn")

    keys = {_primary_key(contract, text) for text in events["key"].unique().to_list()}
    current = _current_rows(lake, uri, contract, keys)
    changed = apply_events(contract, Decoder(contract, language), events, current, now)
    if changed:
        _merge(table, contract, changed.values(), events["_ingested_at"].max())
    return SilverResult(contract.table, events.height, len(changed))


def _from_micros(value: int) -> datetime:
    return datetime.fromtimestamp(0, tz=UTC) + timedelta(microseconds=value)


def _micros(value: Any) -> int:
    delta: timedelta = value - datetime.fromtimestamp(0, tz=UTC)
    return delta // timedelta(microseconds=1)


def _current_rows(
    lake: Lake, uri: str, contract: Contract, keys: Iterable[int]
) -> dict[int, dict[str, Any]]:
    # TODO(TD-028): lee la tabla entera para quedarse con unas pocas claves.
    frame = lake.read(uri).filter(pl.col("id").is_in(list(keys)))
    rows: Sequence[dict[str, Any]] = frame.select(list(silver_schema(contract))).rows(named=True)
    return {row["id"]: dict(row) for row in rows}


def _merge(
    table: DeltaTable, contract: Contract, rows: Iterable[dict[str, Any]], watermark: Any
) -> None:
    source = pl.DataFrame(list(rows), schema=silver_schema(contract), orient="row")
    (
        table.merge(
            source,
            predicate="t.id = s.id",
            source_alias="s",
            target_alias="t",
            commit_properties=CommitProperties(
                app_transactions=[Transaction(WATERMARK, _micros(watermark))]
            ),
        )
        .when_matched_update_all()
        .when_not_matched_insert_all()
        .execute()
    )
