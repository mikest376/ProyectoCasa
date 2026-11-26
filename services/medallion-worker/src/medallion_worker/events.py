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
        return table_name(self.topic)

    @property
    def size(self) -> int:
        """Tamaño aproximado del contenido, para acotar la memoria del lote."""
        return len(self.key or "") + len(self.before or "") + len(self.after or "")


@dataclass(frozen=True, slots=True)
class RejectedEvent:
    """Mensaje que no se puede interpretar. Se guarda tal cual: descartarlo sería perderlo."""

    topic: str
    partition: int
    offset: int
    reason: str
    key: bytes | None
    value: bytes | None

    @property
    def size(self) -> int:
        return len(self.key or b"") + len(self.value or b"")


def table_name(topic: str) -> str:
    """Tabla de origen a partir del topic: odoo.public.sale_order -> sale_order."""
    return topic.rsplit(".", maxsplit=1)[-1]


def as_text(value: Any) -> str | None:
    """JSON compacto, como se guarda en Bronze."""
    if value is None:
        return None
    text = json.dumps(value, ensure_ascii=False, separators=(",", ":"))
    try:
        text.encode()
    except UnicodeEncodeError:
        # Sustitutos sueltos (\ud800): no caben en UTF-8, pero sí como secuencia de escape.
        return json.dumps(value, separators=(",", ":"))
    return text


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
        key_text = None if key is None else as_text(json.loads(key))
    except (json.JSONDecodeError, UnicodeDecodeError, RecursionError) as error:
        raise InvalidEventError("JSON no válido") from error
    if not isinstance(envelope, dict):
        raise InvalidEventError("el evento no es un objeto JSON")

    op = envelope.get("op")
    if not isinstance(op, str) or op not in OPERATIONS:
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
        before=as_text(envelope.get("before")),
        after=as_text(envelope.get("after")),
    )
