"""Silver -> Gold: reconstruye los modelos cuyas tablas de origen han cambiado."""

import logging
from dataclasses import dataclass

import polars as pl
from deltalake import CommitProperties, DeltaTable, Transaction, write_deltalake

from medallion_worker.contracts import BY_TABLE
from medallion_worker.gold_models import Frames, Model
from medallion_worker.storage import Lake, delta_schema

logger = logging.getLogger(__name__)

SILVER_VERSIONS = "silver_versions"
REVISION = "model_revision"


class ModelError(RuntimeError):
    """El resultado no cumple el esquema declarado del modelo."""


@dataclass(frozen=True, slots=True)
class GoldResult:
    model: str
    rows: int
    written: bool


def _fingerprint(lake: Lake, model: Model) -> int | None:
    """Suma de versiones de las tablas Silver: solo crece, y crece si cambia alguna.

    None si falta alguna tabla: el modelo no se puede construir todavía.
    """
    versions = [lake.version(silver_uri(lake, source)) for source in model.sources]
    return None if -1 in versions else sum(versions)


def silver_uri(lake: Lake, table: str) -> str:
    return lake.silver(BY_TABLE[table].domain, table)


def load_frames(lake: Lake, tables: set[str]) -> Frames:
    """Tablas Silver sin las filas borradas en Odoo."""
    return {
        table: lake.read(silver_uri(lake, table)).filter(~pl.col("_is_deleted"))
        for table in sorted(tables)
    }


def conform(model: Model, frame: pl.DataFrame) -> pl.DataFrame:
    """Ajusta el resultado al esquema declarado: mismas columnas, redondeo y tipos estrictos."""
    if set(frame.columns) != set(model.schema):
        difference = sorted(set(frame.columns) ^ set(model.schema))
        raise ModelError(f"{model.name}: columnas distintas de las declaradas: {difference}")
    expressions = []
    for name, dtype in model.schema.items():
        column = pl.col(name)
        if isinstance(dtype, pl.Decimal):
            column = column.round(dtype.scale)
        expressions.append(column.cast(dtype, strict=True))
    return frame.select(expressions)


def refresh(lake: Lake, model: Model) -> GoldResult:
    fingerprint = _fingerprint(lake, model)
    if fingerprint is None:
        return GoldResult(model.name, 0, False)
    uri = lake.gold(model.domain, model.name)
    schema = delta_schema(model.columns)
    table = lake.open_or_create(uri, schema, model.name, model.description)
    if not _same_definition(table, model):
        # Cambio de columnas o de descripciones: se recrea; el contenido se reescribe abajo.
        table = DeltaTable.create(
            uri,
            schema=schema,
            mode="overwrite",
            name=model.name,
            description=model.description,
            storage_options=lake.options,
        )
    elif (
        table.transaction_version(SILVER_VERSIONS) == fingerprint
        and table.transaction_version(REVISION) == model.revision
    ):
        return GoldResult(model.name, 0, False)

    frame = conform(model, model.build(load_frames(lake, set(model.sources))))
    write_deltalake(
        table,
        frame,
        mode="overwrite",
        commit_properties=CommitProperties(
            app_transactions=[
                Transaction(SILVER_VERSIONS, fingerprint),
                Transaction(REVISION, model.revision),
            ]
        ),
    )
    return GoldResult(model.name, frame.height, True)


def _same_definition(table: DeltaTable, model: Model) -> bool:
    declared = {
        field.name: (field.type, field.metadata) for field in delta_schema(model.columns).fields
    }
    actual = {field.name: (field.type, field.metadata) for field in table.schema().fields}
    return declared == actual and table.metadata().description == model.description
