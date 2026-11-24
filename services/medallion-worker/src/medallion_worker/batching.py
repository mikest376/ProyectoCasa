"""Agrupación de eventos en lotes por tamaño y por tiempo."""

import time
from collections.abc import Callable

from medallion_worker.events import BronzeEvent


class Batcher:
    def __init__(
        self,
        max_records: int,
        max_seconds: float,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._max_records = max_records
        self._max_seconds = max_seconds
        self._clock = clock
        self._events: list[BronzeEvent] = []
        self._started_at = 0.0

    def __len__(self) -> int:
        return len(self._events)

    def add(self, event: BronzeEvent) -> None:
        if not self._events:
            self._started_at = self._clock()
        self._events.append(event)

    @property
    def ready(self) -> bool:
        if not self._events:
            return False
        return (
            len(self._events) >= self._max_records
            or self._clock() - self._started_at >= self._max_seconds
        )

    def drain(self) -> list[BronzeEvent]:
        events, self._events = self._events, []
        return events
