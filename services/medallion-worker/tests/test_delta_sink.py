"""Escritura Delta real, sobre disco local."""

import json
from datetime import UTC, datetime
from pathlib import Path

import polars as pl
import pytest
from deltalake import DeltaTable
from deltalake.exceptions import DeltaError

from medallion_worker.batching import Batcher
from medallion_worker.events import RejectedEvent, parse_event
from medallion_worker.sinks import EVENT_COLUMNS, REJECTED_TABLE, DeltaSink

TOPIC = "odoo.public.sale_order"
NOW = datetime(2026, 11, 25, 12, 0, 0, 123456, tzinfo=UTC)


def write(sink: DeltaSink, *offsets: int, topic: str = TOPIC, rejected: int | None = None) -> None:
    batcher = Batcher(max_records=10**6, max_bytes=10**9, max_seconds=3600)
    for offset in offsets:
        value = {
            "op": "u",
            "before": None,
            "after": {"id": offset, "amount_total": "1250.10", "name": "Año ñ 💶"},
            "source": {"lsn": 10**12 + offset, "ts_ms": 1700000000000},
        }
        event = parse_event(topic, 0, offset, b'{"id":1}', json.dumps(value).encode())
        assert event is not None
        batcher.add(event)
    if rejected is not None:
        batcher.add(RejectedEvent(topic, 0, rejected, "JSON no válido", None, b"\xff\x00roto"))
    for batch in batcher.drain():
        sink.write(batch)


@pytest.fixture
def sink(tmp_path: Path) -> DeltaSink:
    return DeltaSink(str(tmp_path), now=lambda: NOW)


def test_rows_are_appended_with_their_types(sink: DeltaSink, tmp_path: Path) -> None:
    write(sink, 0, 1)
    write(sink, 2)
    frame = pl.read_delta(str(tmp_path / "sale_order")).sort("_offset")
    assert dict(frame.schema) == EVENT_COLUMNS
    assert frame["_offset"].to_list() == [0, 1, 2]
    assert frame["_ingested_at"].unique().to_list() == [NOW]
    row = frame.row(0, named=True)
    assert (row["_topic"], row["op"], row["source_lsn"]) == (TOPIC, "u", 10**12)
    assert row["before"] is None
    assert json.loads(row["after"]) == {"id": 0, "amount_total": "1250.10", "name": "Año ñ 💶"}


def test_each_batch_is_one_commit(sink: DeltaSink, tmp_path: Path) -> None:
    write(sink, 0, 1)
    write(sink, 2)
    # Versión 0: creación. Versiones 1 y 2: los dos lotes.
    assert DeltaTable(str(tmp_path / "sale_order")).version() == 2


def test_a_new_process_continues_the_same_table(sink: DeltaSink, tmp_path: Path) -> None:
    write(sink, 0)
    write(DeltaSink(str(tmp_path), now=lambda: NOW), 1)
    assert pl.read_delta(str(tmp_path / "sale_order")).height == 2


def test_each_topic_has_its_own_table(sink: DeltaSink, tmp_path: Path) -> None:
    write(sink, 0)
    write(sink, 0, topic="odoo.public.res_partner")
    assert sorted(path.name for path in tmp_path.iterdir()) == ["res_partner", "sale_order"]


def test_rejected_messages_are_kept_verbatim(sink: DeltaSink, tmp_path: Path) -> None:
    write(sink, 0, rejected=1)
    [row] = pl.read_delta(str(tmp_path / REJECTED_TABLE)).rows(named=True)
    assert (row["_topic"], row["_offset"], row["reason"]) == (TOPIC, 1, "JSON no válido")
    assert row["key"] is None
    assert row["value"] == b"\xff\x00roto"


def test_bronze_tables_reject_changes(sink: DeltaSink, tmp_path: Path) -> None:
    write(sink, 0, 1)
    table = DeltaTable(str(tmp_path / "sale_order"))
    assert table.metadata().configuration["delta.appendOnly"] == "true"
    with pytest.raises(DeltaError):
        table.delete("_offset = 0")
    with pytest.raises(DeltaError):
        table.update(predicate="_offset = 0", new_values={"op": "d"})
    assert pl.read_delta(str(tmp_path / "sale_order")).height == 2


def test_empty_batches_write_nothing(sink: DeltaSink, tmp_path: Path) -> None:
    batcher = Batcher(max_records=10, max_bytes=10, max_seconds=10)
    batcher.skip(TOPIC, 0, 7)
    [batch] = batcher.drain()
    sink.write(batch)
    assert list(tmp_path.iterdir()) == []
