import json
from typing import Any

import pytest

from medallion_worker.batching import Batcher
from medallion_worker.config import Settings
from medallion_worker.events import BronzeEvent, InvalidEventError, parse_event
from medallion_worker.sinks import LogSink

TOPIC = "odoo.public.sale_order"


def encode(envelope: Any) -> bytes:
    return json.dumps(envelope).encode()


def make_event(offset: int = 0) -> BronzeEvent:
    event = parse_event(TOPIC, 0, offset, b'{"id":1}', encode({"op": "c", "after": {"id": 1}}))
    assert event is not None
    return event


def test_parse_keeps_raw_payload_and_coordinates() -> None:
    value = encode(
        {
            "op": "u",
            "before": None,
            "after": {"id": 7, "amount_total": "1250.00", "name": "Pedido ñ"},
            "source": {"lsn": 123456, "ts_ms": 1700000000000},
        }
    )
    event = parse_event(TOPIC, 2, 42, b'{"id":7}', value)
    assert event is not None
    assert (event.partition, event.offset, event.op) == (2, 42, "u")
    assert (event.source_lsn, event.source_ts_ms) == (123456, 1700000000000)
    assert event.table == "sale_order"
    assert event.key == '{"id":7}'
    assert event.before is None
    # Los decimales llegan como texto y no se convierten a coma flotante.
    assert json.loads(event.after or "")["amount_total"] == "1250.00"


def test_tombstone_is_ignored() -> None:
    assert parse_event(TOPIC, 0, 0, b'{"id":1}', None) is None


def test_table_without_key() -> None:
    event = parse_event(TOPIC, 0, 0, None, encode({"op": "r", "after": {"a": 1}}))
    assert event is not None
    assert event.key is None


@pytest.mark.parametrize(
    "value",
    [b"not json", b"\xff\xfe", encode([1, 2]), encode({"after": {}}), encode({"op": "x"})],
)
def test_invalid_events_are_rejected(value: bytes) -> None:
    with pytest.raises(InvalidEventError):
        parse_event(TOPIC, 0, 0, None, value)


def test_source_with_unexpected_types() -> None:
    event = parse_event(TOPIC, 0, 0, None, encode({"op": "c", "source": {"lsn": "x"}}))
    assert event is not None
    assert event.source_lsn is None


def test_batch_is_ready_by_size() -> None:
    batcher = Batcher(max_records=2, max_seconds=60, clock=lambda: 0.0)
    batcher.add(make_event(1))
    assert not batcher.ready
    batcher.add(make_event(2))
    assert batcher.ready
    assert [event.offset for event in batcher.drain()] == [1, 2]
    assert len(batcher) == 0
    assert not batcher.ready


def test_batch_is_ready_by_time() -> None:
    now = [100.0]
    batcher = Batcher(max_records=100, max_seconds=30, clock=lambda: now[0])
    assert not batcher.ready
    batcher.add(make_event())
    now[0] += 29
    assert not batcher.ready
    now[0] += 1
    assert batcher.ready


def test_log_sink_is_not_durable(caplog: pytest.LogCaptureFixture) -> None:
    sink = LogSink()
    assert sink.durable is False
    with caplog.at_level("INFO"):
        sink.write([make_event(1), make_event(2)])
    assert "lote recibido" in caplog.text


def test_empty_environment_values_use_defaults(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("WORKER_TOPIC_PATTERN", "")
    monkeypatch.setenv("WORKER_BATCH_MAX_RECORDS", "10")
    settings = Settings()
    assert settings.worker_topic_pattern == r"^odoo\.public\..*"
    assert settings.worker_batch_max_records == 10
