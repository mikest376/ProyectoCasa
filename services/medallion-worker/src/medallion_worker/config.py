"""Configuración del worker, leída del entorno."""

from pathlib import Path
from typing import Literal

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_ignore_empty=True, extra="ignore", frozen=True)

    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR"] = "INFO"

    kafka_bootstrap_servers: str = "kafka:19092"
    worker_consumer_group: str = "medallion-worker"
    worker_topic_pattern: str = r"^odoo\.public\..*"

    worker_batch_max_records: int = Field(default=5000, gt=0)
    # Cota de memoria: el límite del contenedor es de 512 MiB.
    worker_batch_max_bytes: int = Field(default=32 * 1024 * 1024, gt=0)
    worker_batch_max_seconds: float = Field(default=30.0, gt=0)

    # Tablas Bronze particionadas por fecha de ingesta: las de mayor volumen o crecimiento.
    worker_partitioned_tables: str = (
        "ir_model_data,ir_model_fields,mail_message,mail_tracking_value,account_move_line,"
        "sale_order_line,stock_move,stock_move_line"
    )

    # Silver y Gold.
    worker_transform_seconds: float = Field(default=60.0, gt=0)
    worker_language: str = "es_ES"
    # Hora UTC del mantenimiento diario.
    worker_maintenance_hour: int = Field(default=3, ge=0, le=23)

    # Usuario propio de MinIO, no el raíz (infra/minio/policies).
    s3_endpoint_url: str = "http://minio:9000"
    s3_region: str = "us-east-1"
    s3_access_key_id: SecretStr
    s3_secret_access_key: SecretStr
    s3_bucket_bronze: str = "bronze"
    s3_bucket_silver: str = "silver"
    s3_bucket_gold: str = "gold"

    trino_url: str = "http://trino:8080"
    trino_worker_user: str = "medallion-worker"
    trino_catalog: str = "lakehouse"
    unity_catalog_url: str = "http://unity-catalog:8080"
    unity_catalog_name: str = "lakehouse"

    # El healthcheck del contenedor comprueba que este fichero se actualiza.
    worker_alive_file: Path = Path("/tmp/medallion-worker.alive")  # noqa: S108

    @property
    def bronze_uri(self) -> str:
        return f"s3://{self.s3_bucket_bronze}/odoo"

    @property
    def partitioned_tables(self) -> frozenset[str]:
        names = (name.strip() for name in self.worker_partitioned_tables.split(","))
        return frozenset(name for name in names if name)
