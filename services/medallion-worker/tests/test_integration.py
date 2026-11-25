"""Kafka y MinIO reales. Los arranca scripts/test_integration.sh en un proyecto aislado."""

import json
import os
import threading
import uuid
from collections.abc import Iterator

import polars as pl
import pytest
from confluent_kafka import Producer, TopicPartition
from confluent_kafka.admin import AdminClient
from confluent_kafka.cimpl import NewTopic
from deltalake import DeltaTable
from deltalake.exceptions import DeltaError

from medallion_worker.batching import TopicBatch
from medallion_worker.config import Settings
from medallion_worker.consumer import build_consumer, run
from medallion_worker.sinks import REJECTED_TABLE, BronzeSink, DeltaSink, storage_options

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        "LAKEHOUSE_INTEGRATION" not in os.environ,
        reason="requiere scripts/test_integration.sh",
    ),
]

EVENTS = 1200


@pytest.fixture
def prefix() -> str:
    return f"it{uuid.uuid4().hex[:8]}"


@pytest.fixture
def settings(prefix: str, monkeypatch: pytest.MonkeyPatch) -> Settings:
    monkeypatch.setenv("WORKER_CONSUMER_GROUP", prefix)
    monkeypatch.setenv("WORKER_TOPIC_PATTERN", rf"^{prefix}\.public\..*")
    monkeypatch.setenv("WORKER_BATCH_MAX_RECORDS", "250")
    monkeypatch.setenv("WORKER_BATCH_MAX_SECONDS", "2")
    return Settings()


@pytest.fixture
def topics(prefix: str, settings: Settings) -> Iterator[list[str]]:
    names = [f"{prefix}.public.{prefix}_orders", f"{prefix}.public.{prefix}_partners"]
    admin = AdminClient({"bootstrap.servers": settings.kafka_bootstrap_servers})
    for future in admin.create_topics([NewTopic(name, 1, 1) for name in names]).values():
        future.result(30)
    producer = Producer({"bootstrap.servers": settings.kafka_bootstrap_servers})
    for number in range(EVENTS):
        event = {
            "op": "c",
            "after": {"id": number, "amount_total": "10.50"},
            "source": {"lsn": number, "ts_ms": 1700000000000},
        }
        producer.produce(names[number % 2], json.dumps(event).encode(), b'{"id":%d}' % number)
        producer.poll(0)
    producer.produce(names[0], b"no es JSON")
    assert producer.flush(30) == 0
    yield names
    for future in admin.delete_topics(names).values():
        future.result(30)


def committed(settings: Settings, topics: list[str]) -> list[int]:
    consumer = build_consumer(settings)
    try:
        return [
            position.offset
            for position in consumer.committed([TopicPartition(t, 0) for t in topics], timeout=20)
        ]
    finally:
        consumer.close()


def run_until(settings: Settings, sink: BronzeSink, topics: list[str], timeout: float) -> None:
    """Ejecuta el worker real hasta que confirma todo o vence el plazo."""
    stop = threading.Event()
    errors: list[Exception] = []

    def target() -> None:
        try:
            run(settings, sink, stop)
        except Exception as error:
            errors.append(error)

    thread = threading.Thread(target=target)
    thread.start()
    expected = [EVENTS // 2 + 1, EVENTS // 2]
    waited = 0.0
    while thread.is_alive() and waited < timeout:
        thread.join(2)
        waited += 2
        if committed(settings, topics) == expected:
            break
    stop.set()
    thread.join(60)
    if errors:
        raise errors[0]


def read(settings: Settings, table: str) -> pl.DataFrame:
    return pl.read_delta(
        f"{settings.bronze_uri}/{table}", storage_options=storage_options(settings)
    )


def test_events_reach_delta_and_a_crash_loses_nothing(
    settings: Settings, topics: list[str], prefix: str
) -> None:
    real = DeltaSink(settings.bronze_uri, storage_options(settings))

    class CrashesOnThirdWrite:
        writes = 0

        def write(self, batch: TopicBatch) -> None:
            self.writes += 1
            if self.writes == 3:
                raise OSError("caída simulada")
            real.write(batch)

    with pytest.raises(OSError, match="caída simulada"):
        run_until(settings, CrashesOnThirdWrite(), topics, timeout=60)
    assert sum(offset for offset in committed(settings, topics) if offset > 0) < EVENTS

    run_until(settings, real, topics, timeout=90)
    assert committed(settings, topics) == [EVENTS // 2 + 1, EVENTS // 2]

    for topic, table in zip(topics, [f"{prefix}_orders", f"{prefix}_partners"], strict=True):
        frame = read(settings, table)
        assert frame["_topic"].unique().to_list() == [topic]
        # Puede haber repetidos (al menos una vez), pero no puede faltar ninguno.
        assert frame["_offset"].n_unique() == EVENTS // 2
        assert frame.unique(["_topic", "_partition", "_offset"]).height == EVENTS // 2
        assert json.loads(frame["after"][0])["amount_total"] == "10.50"

    rejected = read(settings, REJECTED_TABLE).filter(pl.col("_topic") == topics[0])
    assert rejected["value"].unique().to_list() == [b"no es JSON"]


def test_worker_user_cannot_delete_from_bronze(settings: Settings, topics: list[str]) -> None:
    """El protocolo Delta lo impide y, además, el usuario de MinIO no tiene permiso."""
    sink = DeltaSink(settings.bronze_uri, storage_options(settings))
    run_until(settings, sink, topics, timeout=90)
    uri = f"{settings.bronze_uri}/{topics[0].rsplit('.', 1)[-1]}"
    table = DeltaTable(uri, storage_options=storage_options(settings))
    with pytest.raises(DeltaError):
        table.delete()
