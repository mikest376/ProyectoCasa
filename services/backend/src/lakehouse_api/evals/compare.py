"""Precisión de ejecución: se comparan los resultados, no el texto del SQL."""

import json
import re
from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from importlib import resources
from itertools import permutations
from typing import Any

MAX_COLUMNS = 12
CENT = Decimal("0.01")
MONTH = re.compile(r"^\d{4}-\d{2}$")
DAY = re.compile(r"^(\d{4}-\d{2}-\d{2})(?:[ T]00:00.*)?$")

Row = Sequence[Any]


@dataclass(frozen=True, slots=True)
class Case:
    identifier: str
    domain: str
    split: str
    question: str
    sql: tuple[str, ...]
    ordered: bool


def load_cases(split: str = "all") -> list[Case]:
    text = resources.files(__package__).joinpath("questions.json").read_text(encoding="utf-8")
    cases = [
        Case(
            item["id"],
            item["domain"],
            item["split"],
            item["question"],
            tuple(item["sql"]),
            bool(item.get("ordered")),
        )
        for item in json.loads(text)
    ]
    return [case for case in cases if split in ("all", case.split)]


def normalize(value: Any) -> Any:
    """Dos formas de escribir el mismo valor son iguales; dos valores distintos, no."""
    if value is None or isinstance(value, bool):
        return value
    if isinstance(value, (int, float, Decimal)):
        return Decimal(str(value)).quantize(CENT).normalize()
    text = str(value).strip()
    if MONTH.match(text):
        return f"{text}-01"
    if match := DAY.match(text):
        return match[1]
    try:
        return Decimal(text).quantize(CENT).normalize()
    except InvalidOperation:
        return text.casefold()


def _column(rows: Sequence[Row], index: int) -> list[Any]:
    return [normalize(row[index]) for row in rows]


def same_result(expected: Sequence[Row], actual: Sequence[Row], ordered: bool) -> bool:
    """Las columnas esperadas deben estar, con los mismos valores y el mismo número de filas.

    Se admiten columnas de más y otro orden de columnas. El orden de las filas solo cuenta si la
    pregunta lo pide; entonces se compara por las columnas esperadas.
    """
    if len(expected) != len(actual):
        return False
    if not expected:
        return True
    width, available = len(expected[0]), len(actual[0])
    if width > available or available > MAX_COLUMNS:
        return False
    wanted = [_column(expected, index) for index in range(width)]
    given = [_column(actual, index) for index in range(available)]
    candidates = [
        [i for i in range(available) if Counter(map(repr, given[i])) == Counter(map(repr, column))]
        for column in wanted
    ]
    if not all(candidates):
        return False
    for choice in permutations(range(available), width):
        if any(index not in candidates[position] for position, index in enumerate(choice)):
            continue
        left = list(zip(*wanted, strict=True))
        right = list(zip(*(given[index] for index in choice), strict=True))
        if left == right or (not ordered and Counter(map(repr, left)) == Counter(map(repr, right))):
            return True
        # Empates: con el mismo valor de ordenación, cualquier orden es correcto.
        if ordered and _same_order_except_ties(left, right):
            return True
    return False


def _same_order_except_ties(left: list[tuple[Any, ...]], right: list[tuple[Any, ...]]) -> bool:
    if Counter(map(repr, left)) != Counter(map(repr, right)):
        return False
    return [row[-1] for row in left] == [row[-1] for row in right]


def percentile(values: Sequence[int], fraction: float) -> int:
    if not values:
        return 0
    ordered = sorted(values)
    return ordered[min(len(ordered) - 1, round(fraction * (len(ordered) - 1)))]
