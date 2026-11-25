"""Qué gráfico corresponde a un resultado. La propuesta del modelo se respeta si encaja."""

from collections.abc import Sequence
from typing import Literal

from pydantic import BaseModel

from lakehouse_api.trino_client import ResultColumn

ChartType = Literal["kpi", "bar", "line", "area", "table"]
ColumnKind = Literal["number", "date", "boolean", "text"]

NUMERIC = ("decimal", "bigint", "integer", "smallint", "tinyint", "double", "real")
MAX_BARS = 40
MAX_KPIS = 4


class ChartSpec(BaseModel):
    type: ChartType
    x_axis: str | None = None
    y_axis: str | None = None


class ColumnInfo(BaseModel):
    name: str
    kind: ColumnKind


def kind_of(trino_type: str) -> ColumnKind:
    base = trino_type.lower()
    if base.startswith(NUMERIC):
        return "number"
    if base.startswith(("date", "timestamp")):
        return "date"
    return "boolean" if base == "boolean" else "text"


def describe(columns: Sequence[ResultColumn]) -> list[ColumnInfo]:
    return [ColumnInfo(name=column.name, kind=kind_of(column.type)) for column in columns]


def _measures(columns: Sequence[ColumnInfo]) -> list[str]:
    """Columnas numéricas que se pueden dibujar. Un identificador no es una medida."""
    return [c.name for c in columns if c.kind == "number" and not c.name.lower().endswith("_id")]


def choose(
    proposed: str | None,
    x_axis: str | None,
    y_axis: str | None,
    columns: Sequence[ColumnInfo],
    rows: int,
) -> ChartSpec:
    measures = _measures(columns)
    kinds = {column.name: column.kind for column in columns}
    if not rows or not measures:
        return ChartSpec(type="table")
    if rows == 1 and len(columns) <= MAX_KPIS:
        return ChartSpec(type="kpi", y_axis=y_axis if y_axis in measures else measures[0])

    y = y_axis if y_axis in measures else measures[0]
    labels = [c.name for c in columns if c.name != y and c.kind in ("date", "text", "boolean")]
    x = x_axis if x_axis in kinds and x_axis != y and x_axis in labels else None
    if x is None and labels:
        # Una fecha manda sobre un texto: es el eje natural de una evolución.
        x = next((name for name in labels if kinds[name] == "date"), labels[0])
    if x is None or rows == 1:
        return ChartSpec(type="table")

    if proposed in ("line", "area") and kinds[x] == "date":
        return ChartSpec(type="line" if proposed == "line" else "area", x_axis=x, y_axis=y)
    if proposed == "table":
        return ChartSpec(type="table", x_axis=x, y_axis=y)
    if kinds[x] == "date" and proposed != "bar":
        return ChartSpec(type="line", x_axis=x, y_axis=y)
    if rows > MAX_BARS:
        return ChartSpec(type="table", x_axis=x, y_axis=y)
    return ChartSpec(type="bar", x_axis=x, y_axis=y)
