"""Interpretación de los eventos de Debezium (JSON sin esquema)."""

import json
from dataclasses import dataclass
from typing import Any

# c: alta · u: cambio · d: borrado · r: lectura del snapshot
OPERATIONS = frozenset({"c", "u", "d", "r"})


class InvalidEventError(ValueError):
    """El mensaje no es un evento CDC válido."""


@dataclass(frozen=True, slots=True)
class BronzeEvent:
    """Fila de Bronze: coordenadas de Kafka y evento crudo como texto JSON."""

    topic: str
    partition: int
    offset: int
    op: str
    source_lsn: int | None
    source_ts_ms: int | None
    key: str | None
    before: str | None
    after: str | None

    @property
    def table(self) -> str:
        return self.topic.rsplit(".", maxsplit=1)[-1]


def _as_text(value: Any) -> str | None:
    if value is None:
        return None
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def _as_int(value: Any) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def parse_event(
    topic: str, partition: int, offset: int, key: bytes | None, value: bytes | None
) -> BronzeEvent | None:
    """Convierte un mensaje de Kafka. Devuelve None si es una lápida (valor vacío)."""
    if value is None:
        return None
    try:
        envelope = json.loads(value)
        key_text = None if key is None else _as_text(json.loads(key))
    except (json.JSONDecodeError, UnicodeDecodeError) as error:
        raise InvalidEventError("JSON no válido") from error
    if not isinstance(envelope, dict):
        raise InvalidEventError("el evento no es un objeto JSON")

    op = envelope.get("op")
    if op not in OPERATIONS:
        raise InvalidEventError("operación desconocida")

    source = envelope.get("source")
    if not isinstance(source, dict):
        source = {}
    return BronzeEvent(
        topic=topic,
        partition=partition,
        offset=offset,
        op=op,
        source_lsn=_as_int(source.get("lsn")),
        source_ts_ms=_as_int(source.get("ts_ms")),
        key=key_text,
        before=_as_text(envelope.get("before")),
        after=_as_text(envelope.get("after")),
    )
