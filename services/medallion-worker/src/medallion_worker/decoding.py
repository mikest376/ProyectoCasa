"""De los valores JSON de Debezium a tipos estrictos, según el contrato.

Debezium envía los numeric como texto (decimal.handling.mode=string), las fechas como días y las
marcas de tiempo como microsegundos desde 1970. Los decimales se redondean a la escala del
contrato (6), sin pasar por coma flotante. Un valor que no encaja es un incumplimiento: se rechaza
la tabla entera antes que convertirlo en nulo.
"""

import json
from collections.abc import Callable
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal, InvalidOperation
from typing import Any

from medallion_worker.contracts import Column, Contract

# Marcador de Debezium: el valor TOAST no ha cambiado y no viaja en el evento.
UNAVAILABLE = "__debezium_unavailable_value"
EPOCH = date(1970, 1, 1)
DECIMAL_STEP = Decimal("0.000001")


class ContractError(ValueError):
    """Los datos no cumplen el contrato. El mensaje no incluye valores."""

    def __init__(self, table: str, reason: str) -> None:
        super().__init__(f"{table}: {reason}")
        self.table = table
        self.reason = reason


def _integer(value: Any) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise TypeError
    return value


def _decimal(value: Any) -> Decimal:
    # Nunca float: perdería exactitud antes de llegar aquí.
    if isinstance(value, bool) or not isinstance(value, (str, int)):
        raise TypeError
    number = Decimal(value)
    if not number.is_finite():
        raise ValueError
    return number.quantize(DECIMAL_STEP)


def _double(value: Any) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise TypeError
    return float(value)


def _boolean(value: Any) -> bool:
    if not isinstance(value, bool):
        raise TypeError
    return value


def _text(value: Any) -> str:
    if not isinstance(value, str):
        raise TypeError
    return value


def _date(value: Any) -> date:
    return EPOCH + timedelta(days=_integer(value))


def _timestamp(value: Any) -> datetime:
    return datetime.fromtimestamp(0, tz=UTC) + timedelta(microseconds=_integer(value))


def _translated(language: str) -> Callable[[Any], str]:
    def convert(value: Any) -> str:
        texts = json.loads(_text(value))
        if not isinstance(texts, dict) or not texts:
            raise TypeError
        for key in (language, "en_US"):
            if isinstance(texts.get(key), str):
                return str(texts[key])
        return _text(next(iter(texts.values())))

    return convert


class Decoder:
    """Aplica un contrato a las filas de un evento."""

    def __init__(self, contract: Contract, language: str) -> None:
        self._contract = contract
        converters: dict[str, Callable[[Any], Any]] = {
            "integer": _integer,
            "decimal": _decimal,
            "double": _double,
            "boolean": _boolean,
            "text": _text,
            "date": _date,
            "timestamp": _timestamp,
            "translated": _translated(language),
        }
        # Las columnas personales no se leen nunca del evento, aunque vengan (TD-004).
        self._captured = tuple(column for column in contract.columns if column.pseudonym is None)
        self._converters = {column.name: converters[column.kind] for column in self._captured}

    def row(self, payload: dict[str, Any]) -> dict[str, Any]:
        """Valores tipados. Las columnas TOAST sin cambios no aparecen en el resultado."""
        missing = [column.name for column in self._captured if column.name not in payload]
        if missing:
            raise ContractError(self._contract.table, f"faltan columnas: {', '.join(missing)}")
        return {
            column.name: self._value(column, payload[column.name])
            for column in self._captured
            if payload[column.name] != UNAVAILABLE
        }

    def _value(self, column: Column, value: Any) -> Any:
        if value is None:
            if column.required:
                raise ContractError(self._contract.table, f"{column.name}: nulo no permitido")
            return None
        try:
            return self._converters[column.name](value)
        except (TypeError, ValueError, InvalidOperation, OverflowError) as error:
            raise ContractError(
                self._contract.table, f"{column.name}: no es {column.kind}"
            ) from error
