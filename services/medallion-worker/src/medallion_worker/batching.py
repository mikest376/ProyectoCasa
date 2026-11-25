"""Agrupación de eventos en lotes por número, tamaño y tiempo."""

import time
from collections.abc import Callable
from dataclasses import dataclass, field

from medallion_worker.events import BronzeEvent, RejectedEvent


@dataclass(slots=True)
class TopicBatch:
    """Lo leído de un topic y el offset desde el que hay que seguir en cada partición."""

    topic: str
    events: list[BronzeEvent] = field(default_factory=list)
    rejected: list[RejectedEvent] = field(default_factory=list)
    next_offsets: dict[int, int] = field(default_factory=dict)


class Batcher:
    """Acumula por topic, porque cada topic es una tabla Delta y un commit."""

    def __init__(
        self,
        max_records: int,
        max_bytes: int,
        max_seconds: float,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._max_records = max_records
        self._max_bytes = max_bytes
        self._max_seconds = max_seconds
        self._clock = clock
        self._topics: dict[str, TopicBatch] = {}
        self._records = 0
        self._bytes = 0
        self._started_at = 0.0

    def __len__(self) -> int:
        return self._records

    def _advance(self, topic: str, partition: int, offset: int) -> TopicBatch:
        if not self._topics:
            self._started_at = self._clock()
        batch = self._topics.setdefault(topic, TopicBatch(topic))
        batch.next_offsets[partition] = offset + 1
        return batch

    def add(self, event: BronzeEvent | RejectedEvent) -> None:
        batch = self._advance(event.topic, event.partition, event.offset)
        if isinstance(event, BronzeEvent):
            batch.events.append(event)
        else:
            batch.rejected.append(event)
        self._records += 1
        self._bytes += event.size

    def skip(self, topic: str, partition: int, offset: int) -> None:
        """Mensaje sin contenido que guardar (lápida): solo avanza el offset."""
        self._advance(topic, partition, offset)

    @property
    def ready(self) -> bool:
        if not self._topics:
            return False
        return (
            self._records >= self._max_records
            or self._bytes >= self._max_bytes
            or self._clock() - self._started_at >= self._max_seconds
        )

    def drain(self) -> list[TopicBatch]:
        topics = list(self._topics.values())
        self._topics = {}
        self._records = 0
        self._bytes = 0
        return topics
