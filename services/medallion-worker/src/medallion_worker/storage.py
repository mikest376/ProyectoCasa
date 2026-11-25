"""Acceso al lakehouse: rutas de las tres capas y operaciones comunes sobre tablas Delta."""

from collections.abc import Iterable, Sequence
from datetime import datetime

import polars as pl
from deltalake import DeltaTable
from deltalake.schema import Field, PrimitiveType, Schema

from medallion_worker.config import Settings

Columns = Sequence[tuple[str, pl.DataType, str]]


def storage_options(settings: Settings) -> dict[str, str]:
    return {
        "AWS_ENDPOINT_URL": settings.s3_endpoint_url,
        "AWS_REGION": settings.s3_region,
        "AWS_ACCESS_KEY_ID": settings.s3_access_key_id.get_secret_value(),
        "AWS_SECRET_ACCESS_KEY": settings.s3_secret_access_key.get_secret_value(),
        # Dentro de la red de Docker no hay TLS (TD-014).
        "AWS_ALLOW_HTTP": str(settings.s3_endpoint_url.startswith("http://")).lower(),
        "AWS_VIRTUAL_HOSTED_STYLE_REQUEST": "false",
        # Escritura condicional: dos commits con el mismo número de versión no se pisan.
        "aws_conditional_put": "etag",
    }


def delta_type(dtype: pl.DataType) -> str:
    if isinstance(dtype, pl.Decimal):
        return f"decimal({dtype.precision},{dtype.scale})"
    names: dict[type[pl.DataType], str] = {
        pl.String: "string",
        pl.Int32: "integer",
        pl.Int64: "long",
        pl.Float64: "double",
        pl.Boolean: "boolean",
        pl.Binary: "binary",
        pl.Date: "date",
        pl.Datetime: "timestamp",
    }
    return names[type(dtype)]


def delta_schema(columns: Columns, required: Iterable[str] = ()) -> Schema:
    """Esquema con las descripciones como comentarios: Trino y Unity Catalog las muestran."""
    mandatory = set(required)
    return Schema(
        [
            Field(
                name,
                PrimitiveType(delta_type(dtype)),
                nullable=name not in mandatory,
                metadata={"comment": description} if description else None,
            )
            for name, dtype, description in columns
        ]
    )


class Lake:
    """Rutas y acceso. Sin opciones de S3 trabaja sobre disco local (pruebas)."""

    def __init__(
        self, bronze: str, silver: str, gold: str, options: dict[str, str] | None = None
    ) -> None:
        self._bronze = bronze.rstrip("/")
        self._silver = silver.rstrip("/")
        self._gold = gold.rstrip("/")
        self.options = options

    @classmethod
    def from_settings(cls, settings: Settings) -> "Lake":
        return cls(
            settings.bronze_uri,
            f"s3://{settings.s3_bucket_silver}",
            f"s3://{settings.s3_bucket_gold}",
            storage_options(settings),
        )

    def bronze(self, table: str) -> str:
        return f"{self._bronze}/{table}"

    def silver(self, domain: str, table: str) -> str:
        return f"{self._silver}/{domain}/{table}"

    def gold(self, domain: str, model: str) -> str:
        return f"{self._gold}/{domain}/{model}"

    def exists(self, uri: str) -> bool:
        return DeltaTable.is_deltatable(uri, self.options)

    def open(self, uri: str) -> DeltaTable:
        return DeltaTable(uri, storage_options=self.options)

    def version(self, uri: str) -> int:
        """Versión de la tabla; -1 si no existe."""
        return self.open(uri).version() if self.exists(uri) else -1

    def open_or_create(self, uri: str, schema: Schema, name: str, description: str) -> DeltaTable:
        DeltaTable.create(
            uri,
            schema=schema,
            mode="ignore",
            name=name,
            description=description,
            storage_options=self.options,
        )
        return self.open(uri)

    def read(self, uri: str) -> pl.DataFrame:
        return pl.read_delta(uri, storage_options=self.options)

    def read_bronze(self, table: str, since: datetime | None) -> pl.DataFrame:
        """Eventos de una tabla desde un momento de ingesta. Vacío si aún no existe."""
        uri = self.bronze(table)
        if not self.exists(uri):
            return pl.DataFrame()
        frame = pl.scan_delta(uri, storage_options=self.options)
        if since is not None:
            frame = frame.filter(pl.col("_ingested_at") >= since)
            if "_ingested_date" in frame.collect_schema().names():
                # Columna de partición: evita leer los días anteriores (TD-007).
                frame = frame.filter(pl.col("_ingested_date") >= since.date())
        return frame.collect()
