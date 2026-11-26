"""Bucle de consumo de Kafka, con entrega al menos una vez."""

import logging
import threading
from collections.abc import Callable
from typing import Any

from confluent_kafka import Consumer, KafkaError, KafkaException, TopicPartition

from medallion_worker.batching import Batcher
from medallion_worker.config import Settings
from medallion_worker.events import InvalidEventError, RejectedEvent, parse_event
from medallion_worker.sinks import BronzeSink

logger = logging.getLogger(__name__)

POLL_SECONDS = 1.0
POLL_RECORDS = 500


def build_consumer(settings: Settings) -> Any:
    return Consumer(
        {
            "bootstrap.servers": settings.kafka_bootstrap_servers,
            "group.id": settings.worker_consumer_group,
            "auto.offset.reset": "earliest",
            # Los offsets se confirman a mano y de forma explícita: ver Ingestor.flush.
            "enable.auto.commit": False,
            "enable.auto.offset.store": False,
            # Un topic nuevo no obliga a soltar las particiones que ya se están leyendo.
            "partition.assignment.strategy": "cooperative-sticky",
            "allow.auto.create.topics": False,
            # Detecta en poco tiempo los topics de tablas nuevas.
            "topic.metadata.refresh.interval.ms": 30_000,
            # Cota de memoria del búfer local (límite del contenedor: 384 MiB).
            "queued.max.messages.kbytes": 65_536,
        }
    )


class Ingestor:
    """Pasa mensajes de Kafka al destino y confirma solo lo que ya está guardado."""

    def __init__(
        self,
        consumer: Any,
        sink: BronzeSink,
        batcher: Batcher,
        alive: Callable[[], None] = lambda: None,
    ) -> None:
        self._consumer = consumer
        self._sink = sink
        self._batcher = batcher
        self._alive = alive
        self._rejected = 0

    def handle(self, message: Any) -> None:
        error = message.error()
        if error is not None:
            if error.retriable() or error.code() in (
                KafkaError._PARTITION_EOF,
                KafkaError.UNKNOWN_TOPIC_OR_PART,
            ):
                return
            raise KafkaException(error)

        topic, partition, offset = message.topic(), message.partition(), message.offset()
        key, value = message.key(), message.value()
        try:
            event = parse_event(topic, partition, offset, key, value)
        except InvalidEventError as reason:
            self._rejected += 1
            logger.warning(
                "evento rechazado",
                extra={
                    "fields": {
                        "topic": topic,
                        "partition": partition,
                        "offset": offset,
                        "reason": str(reason),
                        "rejected_total": self._rejected,
                    }
                },
            )
            self._batcher.add(RejectedEvent(topic, partition, offset, str(reason), key, value))
            return
        if event is None:
            self._batcher.skip(topic, partition, offset)
        else:
            self._batcher.add(event)

    def flush(self) -> None:
        """Escribe el lote y confirma, topic a topic, solo lo escrito.

        Se confirman offsets explícitos: commit() sin argumentos confirmaría también los
        mensajes ya devueltos por consume() que todavía no se han escrito. Si una tabla falla,
        se confirman las anteriores y la excepción detiene el proceso: al reiniciarse, Kafka
        vuelve a entregar el resto.
        """
        written: list[TopicPartition] = []
        try:
            for batch in self._batcher.drain():
                self._sink.write(batch)
                written += [
                    TopicPartition(batch.topic, partition, offset)
                    for partition, offset in batch.next_offsets.items()
                ]
                self._alive()
        finally:
            if written:
                self._commit(written)

    def _commit(self, offsets: list[TopicPartition]) -> None:
        for result in self._consumer.commit(offsets=offsets, asynchronous=False) or []:
            if result.error is not None:
                raise KafkaException(result.error)

    def _on_revoke(self, _consumer: Any, _partitions: list[TopicPartition]) -> None:
        self.flush()

    def _on_lost(self, _consumer: Any, partitions: list[TopicPartition]) -> None:
        # Ya no se pueden confirmar. Kafka volverá a entregar lo que no se llegó a escribir.
        self._batcher.drain()
        logger.warning("particiones perdidas", extra={"fields": {"partitions": len(partitions)}})

    def run(self, topic_pattern: str, stop: threading.Event) -> None:
        self._consumer.subscribe([topic_pattern], on_revoke=self._on_revoke, on_lost=self._on_lost)
        try:
            while not stop.is_set():
                for message in self._consumer.consume(POLL_RECORDS, POLL_SECONDS):
                    self.handle(message)
                    if self._batcher.ready:
                        self.flush()
                if self._batcher.ready:
                    self.flush()
                self._alive()
            self.flush()
        finally:
            # Tras un fallo, lo pendiente no se escribe al cerrar: se volverá a entregar.
            self._batcher.drain()
            self._consumer.close()


def run(settings: Settings, sink: BronzeSink, stop: threading.Event) -> None:
    batcher = Batcher(
        settings.worker_batch_max_records,
        settings.worker_batch_max_bytes,
        settings.worker_batch_max_seconds,
    )
    ingestor = Ingestor(build_consumer(settings), sink, batcher, settings.worker_alive_file.touch)
    logger.info("consumidor iniciado", extra={"fields": {"group": settings.worker_consumer_group}})
    try:
        ingestor.run(settings.worker_topic_pattern, stop)
    finally:
        logger.info("consumidor detenido")
