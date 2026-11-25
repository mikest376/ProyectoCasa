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
    # Cota de memoria: el límite del contenedor es de 1536 MiB.
    worker_batch_max_bytes: int = Field(default=32 * 1024 * 1024, gt=0)
    worker_batch_max_seconds: float = Field(default=30.0, gt=0)

    # Usuario propio de MinIO, no el raíz (infra/minio/policies/medallion-worker.json).
    s3_endpoint_url: str = "http://minio:9000"
    s3_region: str = "us-east-1"
    s3_access_key_id: SecretStr
    s3_secret_access_key: SecretStr
    s3_bucket_bronze: str = "bronze"

    # El healthcheck del contenedor comprueba que este fichero se actualiza.
    worker_alive_file: Path = Path("/tmp/medallion-worker.alive")  # noqa: S108

    @property
    def bronze_uri(self) -> str:
        return f"s3://{self.s3_bucket_bronze}/odoo"
