"""Destinos de los lotes."""

import logging
from collections import Counter
from collections.abc import Sequence
from typing import Protocol

from medallion_worker.events import BronzeEvent

logger = logging.getLogger(__name__)


class BronzeSink(Protocol):
    @property
    def durable(self) -> bool:
        """True si, al volver de write, los eventos están a salvo en almacenamiento."""

    def write(self, events: Sequence[BronzeEvent]) -> None: ...


class LogSink:
    """Fase 1: resume cada lote sin guardar datos.

    No es duradero, así que el consumidor no confirma offsets: la Fase 2 (Delta Lake)
    empezará desde el principio de los topics sin perder eventos.
    """

    durable = False

    def write(self, events: Sequence[BronzeEvent]) -> None:
        operations = Counter(event.op for event in events)
        tables = Counter(event.table for event in events)
        logger.info(
            "lote recibido",
            extra={
                "fields": {
                    "events": len(events),
                    "operations": dict(operations),
                    "tables": len(tables),
                    "top_tables": dict(tables.most_common(5)),
                }
            },
        )
