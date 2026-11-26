import json
from typing import Any

import pytest
from pydantic import ValidationError

from medallion_worker.batching import Batcher
from medallion_worker.config import Settings
from medallion_worker.events import BronzeEvent, InvalidEventError, RejectedEvent, parse_event
from medallion_worker.storage import storage_options

TOPIC = "odoo.public.sale_order"


def encode(envelope: Any) -> bytes:
    return json.dumps(envelope).encode()


def make_event(offset: int = 0, topic: str = TOPIC) -> BronzeEvent:
    event = parse_event(topic, 0, offset, b'{"id":1}', encode({"op": "c", "after": {"id": 1}}))
    assert event is not None
    return event


def make_batcher(**limits: Any) -> Batcher:
    return Batcher(**{"max_records": 100, "max_bytes": 10**9, "max_seconds": 60.0, **limits})


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
    [
        b"not json",
        b"\xff\xfe",
        encode([1, 2]),
        encode({"after": {}}),
        encode({"op": "x"}),
        encode({"op": ["c"]}),
        b"[" * 100_000,
    ],
)
def test_invalid_events_are_rejected(value: bytes) -> None:
    with pytest.raises(InvalidEventError):
        parse_event(TOPIC, 0, 0, None, value)


def test_lone_surrogates_do_not_break_utf8() -> None:
    event = parse_event(TOPIC, 0, 0, None, b'{"op":"c","after":{"name":"\\ud800"}}')
    assert event is not None
    assert event.after is not None
    assert event.after.encode()
    assert json.loads(event.after)["name"] == "\ud800"


def test_source_with_unexpected_types() -> None:
    event = parse_event(TOPIC, 0, 0, None, encode({"op": "c", "source": {"lsn": "x"}}))
    assert event is not None
    assert event.source_lsn is None


def test_batch_is_ready_by_records() -> None:
    batcher = make_batcher(max_records=2)
    batcher.add(make_event(1))
    assert not batcher.ready
    batcher.add(make_event(2))
    assert batcher.ready
    [batch] = batcher.drain()
    assert [event.offset for event in batch.events] == [1, 2]
    assert len(batcher) == 0
    assert not batcher.ready


def test_batch_is_ready_by_size() -> None:
    event = make_event()
    batcher = make_batcher(max_bytes=event.size + 1)
    batcher.add(event)
    assert not batcher.ready
    batcher.add(make_event(1))
    assert batcher.ready


def test_batch_is_ready_by_time() -> None:
    now = [100.0]
    batcher = make_batcher(max_seconds=30, clock=lambda: now[0])
    assert not batcher.ready
    batcher.add(make_event())
    now[0] += 29
    assert not batcher.ready
    now[0] += 1
    assert batcher.ready


def test_batch_groups_by_topic_and_tracks_next_offsets() -> None:
    batcher = make_batcher()
    batcher.add(make_event(5))
    batcher.add(make_event(9, "odoo.public.res_partner"))
    batcher.add(RejectedEvent(TOPIC, 0, 6, "JSON no válido", None, b"x"))
    batcher.skip(TOPIC, 0, 7)
    batcher.skip(TOPIC, 1, 3)
    orders, partners = batcher.drain()
    assert orders.topic == TOPIC
    assert (len(orders.events), len(orders.rejected)) == (1, 1)
    assert orders.next_offsets == {0: 8, 1: 4}
    assert partners.next_offsets == {0: 10}


@pytest.fixture
def s3_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("S3_ACCESS_KEY_ID", "ejemplo")
    monkeypatch.setenv("S3_SECRET_ACCESS_KEY", "valor-de-ejemplo")


@pytest.mark.usefixtures("s3_environment")
def test_empty_environment_values_use_defaults(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("WORKER_TOPIC_PATTERN", "")
    monkeypatch.setenv("WORKER_BATCH_MAX_RECORDS", "10")
    settings = Settings()
    assert settings.worker_topic_pattern == r"^odoo\.public\..*"
    assert settings.worker_batch_max_records == 10


def test_s3_credentials_are_mandatory(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("S3_ACCESS_KEY_ID", raising=False)
    monkeypatch.delenv("S3_SECRET_ACCESS_KEY", raising=False)
    with pytest.raises(ValidationError):
        Settings()


@pytest.mark.usefixtures("s3_environment")
def test_secrets_are_not_shown() -> None:
    settings = Settings()
    assert "valor-de-ejemplo" not in repr(settings)
    assert storage_options(settings)["AWS_SECRET_ACCESS_KEY"] == "valor-de-ejemplo"


@pytest.mark.usefixtures("s3_environment")
def test_http_is_only_allowed_for_http_endpoints(monkeypatch: pytest.MonkeyPatch) -> None:
    assert storage_options(Settings())["AWS_ALLOW_HTTP"] == "true"
    monkeypatch.setenv("S3_ENDPOINT_URL", "https://minio:9000")
    assert storage_options(Settings())["AWS_ALLOW_HTTP"] == "false"
