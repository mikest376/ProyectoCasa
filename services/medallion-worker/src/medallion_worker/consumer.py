"""Bucle de consumo de Kafka."""

import logging
import threading
from typing import Any

from confluent_kafka import Consumer, KafkaError, KafkaException

from medallion_worker.batching import Batcher
from medallion_worker.config import Settings
from medallion_worker.events import InvalidEventError, parse_event
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
            "enable.auto.commit": False,
            "allow.auto.create.topics": False,
            # Detecta en poco tiempo los topics de tablas nuevas.
            "topic.metadata.refresh.interval.ms": 30_000,
            # Cota de memoria del búfer local (límite del contenedor: 1536 MiB).
            "queued.max.messages.kbytes": 65_536,
        }
    )


def _flush(consumer: Any, batcher: Batcher, sink: BronzeSink) -> None:
    events = batcher.drain()
    if not events:
        return
    sink.write(events)
    # El offset se confirma solo cuando los datos ya están a salvo.
    if sink.durable:
        consumer.commit(asynchronous=False)


def run(settings: Settings, sink: BronzeSink, stop: threading.Event) -> None:
    consumer = build_consumer(settings)
    consumer.subscribe([settings.worker_topic_pattern])
    batcher = Batcher(settings.worker_batch_max_records, settings.worker_batch_max_seconds)
    invalid = 0
    logger.info("consumidor iniciado", extra={"fields": {"group": settings.worker_consumer_group}})
    try:
        while not stop.is_set():
            for message in consumer.consume(POLL_RECORDS, POLL_SECONDS):
                error = message.error()
                if error is not None:
                    if error.code() == KafkaError._PARTITION_EOF or error.retriable():
                        continue
                    if error.code() == KafkaError.UNKNOWN_TOPIC_OR_PART:
                        continue
                    raise KafkaException(error)
                try:
                    event = parse_event(
                        message.topic(),
                        message.partition(),
                        message.offset(),
                        message.key(),
                        message.value(),
                    )
                except InvalidEventError as reason:
                    invalid += 1
                    logger.warning(
                        "evento descartado",
                        extra={
                            "fields": {
                                "topic": message.topic(),
                                "partition": message.partition(),
                                "offset": message.offset(),
                                "reason": str(reason),
                                "invalid_total": invalid,
                            }
                        },
                    )
                    continue
                if event is not None:
                    batcher.add(event)
                if batcher.ready:
                    _flush(consumer, batcher, sink)
            if batcher.ready:
                _flush(consumer, batcher, sink)
            settings.worker_alive_file.touch()
        _flush(consumer, batcher, sink)
    finally:
        consumer.close()
        logger.info("consumidor detenido")
