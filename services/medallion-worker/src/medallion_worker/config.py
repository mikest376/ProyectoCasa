"""Configuración del worker, leída del entorno."""

from pathlib import Path
from typing import Literal

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_ignore_empty=True, extra="ignore", frozen=True)

    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR"] = "INFO"

    kafka_bootstrap_servers: str = "kafka:19092"
    worker_consumer_group: str = "medallion-worker"
    worker_topic_pattern: str = r"^odoo\.public\..*"

    worker_batch_max_records: int = Field(default=5000, gt=0)
    worker_batch_max_seconds: float = Field(default=30.0, gt=0)

    # El healthcheck del contenedor comprueba que este fichero se actualiza.
    worker_alive_file: Path = Path("/tmp/medallion-worker.alive")  # noqa: S108
