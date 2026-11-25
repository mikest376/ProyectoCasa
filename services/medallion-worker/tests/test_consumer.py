"""Entrega al menos una vez: qué se confirma, cuándo y qué ocurre al fallar."""

import threading
from collections.abc import Callable
from typing import Any

import pytest
from confluent_kafka import KafkaError, KafkaException, TopicPartition

from medallion_worker.batching import Batcher, TopicBatch
from medallion_worker.consumer import Ingestor

ORDERS = "odoo.public.sale_order"
PARTNERS = "odoo.public.res_partner"
VALID = b'{"op":"c","after":{"id":1}}'


class FakeMessage:
    def __init__(
        self,
        topic: str,
        offset: int,
        value: bytes | None = VALID,
        error: KafkaError | None = None,
    ) -> None:
        self._topic, self._offset, self._value, self._error = topic, offset, value, error

    def error(self) -> KafkaError | None:
        return self._error

    def topic(self) -> str:
        return self._topic

    def partition(self) -> int:
        return 0

    def offset(self) -> int:
        return self._offset

    def key(self) -> bytes | None:
        return b'{"id":1}'

    def value(self) -> bytes | None:
        return self._value


class FakeConsumer:
    """Entrega lotes preparados y, como Kafka, recuerda solo lo confirmado."""

    def __init__(self, polls: list[list[FakeMessage]], stop: threading.Event) -> None:
        self._polls = polls
        self._stop = stop
        self.committed: dict[str, int] = {}
        self.history: list[dict[str, int]] = []
        self.commit_error: KafkaError | None = None
        self.closed = False
        self.on_revoke: Callable[[Any, list[TopicPartition]], None] = lambda *_: None
        self.on_lost: Callable[[Any, list[TopicPartition]], None] = lambda *_: None

    def subscribe(self, _topics: list[str], on_revoke: Any, on_lost: Any) -> None:
        self.on_revoke, self.on_lost = on_revoke, on_lost

    def consume(self, _records: int, _seconds: float) -> list[FakeMessage]:
        if len(self._polls) <= 1:
            self._stop.set()
        return self._polls.pop(0) if self._polls else []

    def commit(self, offsets: list[TopicPartition], asynchronous: bool) -> list[Any]:
        assert asynchronous is False
        if self.commit_error is not None:
            raise KafkaException(self.commit_error)
        committed = {item.topic: item.offset for item in offsets}
        self.committed |= committed
        self.history.append(committed)
        return []

    def close(self) -> None:
        self.closed = True


class FakeSink:
    def __init__(self, fail_on: str | None = None) -> None:
        self.fail_on = fail_on
        self.rows: list[tuple[str, int]] = []
        self.rejected: list[tuple[str, int]] = []

    def write(self, batch: TopicBatch) -> None:
        if batch.topic == self.fail_on:
            raise OSError("almacenamiento no disponible")
        self.rows += [(event.topic, event.offset) for event in batch.events]
        self.rejected += [(event.topic, event.offset) for event in batch.rejected]


def run(
    polls: list[list[FakeMessage]], sink: FakeSink, max_records: int = 100
) -> tuple[FakeConsumer, Ingestor]:
    stop = threading.Event()
    consumer = FakeConsumer(polls, stop)
    batcher = Batcher(max_records=max_records, max_bytes=10**9, max_seconds=3600)
    ingestor = Ingestor(consumer, sink, batcher)
    ingestor.run("^odoo", stop)
    return consumer, ingestor


def test_offsets_are_committed_after_writing() -> None:
    sink = FakeSink()
    consumer, _ = run([[FakeMessage(ORDERS, 0), FakeMessage(ORDERS, 1)]], sink)
    assert sink.rows == [(ORDERS, 0), (ORDERS, 1)]
    assert consumer.committed == {ORDERS: 2}
    assert consumer.closed


def test_a_flush_in_the_middle_of_a_poll_only_commits_what_was_written() -> None:
    """Regresión: commit() sin argumentos confirmaba los cinco mensajes del sondeo."""
    messages = [FakeMessage(ORDERS, offset) for offset in range(5)]
    sink = FakeSink()
    consumer, _ = run([messages], sink, max_records=2)
    assert consumer.history == [{ORDERS: 2}, {ORDERS: 4}, {ORDERS: 5}]


def test_a_failed_table_is_not_committed_and_the_others_are() -> None:
    polls = [[FakeMessage(ORDERS, 0), FakeMessage(PARTNERS, 0)]]
    stop = threading.Event()
    consumer = FakeConsumer(polls, stop)
    sink = FakeSink(fail_on=PARTNERS)
    ingestor = Ingestor(consumer, sink, Batcher(max_records=2, max_bytes=10**9, max_seconds=3600))
    with pytest.raises(OSError, match="no disponible"):
        ingestor.run("^odoo", stop)
    assert consumer.committed == {ORDERS: 1}
    assert consumer.closed


def test_restart_after_a_failure_loses_nothing() -> None:
    """Segundo arranque desde lo confirmado: pueden repetirse filas, nunca faltar."""
    log = [FakeMessage(ORDERS, offset) for offset in range(10)]
    sink = FakeSink()
    stop = threading.Event()
    first = FakeConsumer([log[:6], log[6:]], stop)

    class FailsOnSecondWrite(FakeSink):
        def write(self, batch: TopicBatch) -> None:
            if sink.rows:
                raise OSError("caída")
            sink.write(batch)

    ingestor = Ingestor(
        first,
        FailsOnSecondWrite(),
        Batcher(max_records=4, max_bytes=10**9, max_seconds=3600),
    )
    with pytest.raises(OSError, match="caída"):
        ingestor.run("^odoo", stop)
    assert first.committed == {ORDERS: 4}

    run([log[first.committed[ORDERS] :]], sink)
    assert {offset for _, offset in sink.rows} == set(range(10))


def test_invalid_messages_are_stored_and_do_not_stop_the_flow() -> None:
    sink = FakeSink()
    polls = [[FakeMessage(ORDERS, 0, b"roto"), FakeMessage(ORDERS, 1)]]
    consumer, _ = run(polls, sink)
    assert sink.rejected == [(ORDERS, 0)]
    assert sink.rows == [(ORDERS, 1)]
    assert consumer.committed == {ORDERS: 2}


def test_tombstones_advance_the_offset() -> None:
    sink = FakeSink()
    consumer, _ = run([[FakeMessage(ORDERS, 4, None)]], sink)
    assert sink.rows == []
    assert consumer.committed == {ORDERS: 5}


def test_revoked_partitions_are_flushed_and_lost_ones_are_not() -> None:
    stop = threading.Event()
    consumer = FakeConsumer([], stop)
    sink = FakeSink()
    ingestor = Ingestor(consumer, sink, Batcher(max_records=100, max_bytes=10**9, max_seconds=3600))
    ingestor.run("^odoo", stop)

    ingestor.handle(FakeMessage(ORDERS, 0))
    consumer.on_revoke(consumer, [TopicPartition(ORDERS, 0)])
    assert consumer.committed == {ORDERS: 1}

    ingestor.handle(FakeMessage(ORDERS, 1))
    consumer.on_lost(consumer, [TopicPartition(ORDERS, 0)])
    ingestor.flush()
    assert sink.rows == [(ORDERS, 0)]
    assert consumer.committed == {ORDERS: 1}


def test_a_rejected_commit_stops_the_worker() -> None:
    stop = threading.Event()
    consumer = FakeConsumer([[FakeMessage(ORDERS, 0)]], stop)
    consumer.commit_error = KafkaError(KafkaError.ILLEGAL_GENERATION)
    ingestor = Ingestor(
        consumer, FakeSink(), Batcher(max_records=1, max_bytes=10**9, max_seconds=3600)
    )
    with pytest.raises(KafkaException):
        ingestor.run("^odoo", stop)
    assert consumer.closed


def test_transient_errors_are_ignored_and_fatal_ones_are_not() -> None:
    sink = FakeSink()
    gone = FakeMessage(ORDERS, 0, error=KafkaError(KafkaError.UNKNOWN_TOPIC_OR_PART))
    consumer, _ = run([[gone]], sink)
    assert consumer.committed == {}

    denied = FakeMessage(ORDERS, 0, error=KafkaError(KafkaError.TOPIC_AUTHORIZATION_FAILED))
    with pytest.raises(KafkaException):
        run([[denied]], sink)


def test_alive_is_signalled_after_every_table() -> None:
    stop = threading.Event()
    consumer = FakeConsumer([[FakeMessage(ORDERS, 0), FakeMessage(PARTNERS, 0)]], stop)
    beats: list[int] = []
    ingestor = Ingestor(
        consumer,
        FakeSink(),
        Batcher(max_records=2, max_bytes=10**9, max_seconds=3600),
        lambda: beats.append(1),
    )
    ingestor.run("^odoo", stop)
    # Dos tablas escritas y una vuelta del bucle.
    assert len(beats) == 3
