"""Configuración de la API, leída del entorno."""

from typing import Literal, Self

from pydantic import Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_ignore_empty=True,
        extra="ignore",
        frozen=True,
        # Un error de validación no debe volcar la clave en los logs.
        hide_input_in_errors=True,
    )

    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR"] = "INFO"
    backend_cors_origins: str = "http://localhost:3000"

    # ollama: el prompt no sale del host. nim: se envía a la API de NVIDIA (TD-021).
    llm_provider: Literal["ollama", "nim"] = "ollama"
    llm_request_timeout_seconds: float = Field(default=120.0, gt=0)
    # Tokens de respuesta: una consulta larga cabe de sobra.
    llm_max_tokens: int = Field(default=450, gt=0)
    # Propuesta inicial y, si falla, una corrección.
    llm_max_attempts: int = Field(default=2, gt=0, le=4)

    ollama_base_url: str = "http://ollama:11434"
    ollama_model: str = "qwen2.5-coder:3b-instruct-q4_K_M"
    ollama_context_length: int = Field(default=4096, gt=0, le=4096)

    nim_base_url: str = "https://integrate.api.nvidia.com/v1"
    nim_model: str = "nvidia/nemotron-3.5-lightning-30b-a3b"
    nim_api_key: SecretStr | None = None
    nim_max_tokens: int = Field(default=4096, gt=0)

    unity_catalog_url: str = "http://unity-catalog:8080"
    unity_catalog_name: str = "lakehouse"
    catalog_ttl_seconds: float = Field(default=60.0, gt=0)

    trino_host: str = "trino"
    trino_port: int = 8080
    # Usuario limitado a SELECT sobre Gold (infra/trino/access-control.json).
    trino_user: str = "text2sql"
    trino_catalog: str = "lakehouse"
    trino_schema: str = "gold"
    sql_max_rows: int = Field(default=1000, gt=0, le=10_000)
    sql_timeout_seconds: float = Field(default=60.0, gt=0)

    # Sin claves no se envían trazas.
    langfuse_host: str = "http://langfuse:3000"
    langfuse_public_key: str | None = None
    langfuse_secret_key: SecretStr | None = None

    @field_validator("nim_base_url")
    @classmethod
    def _https_only(cls, value: str) -> str:
        if not value.startswith("https://"):
            raise ValueError("NIM_BASE_URL debe usar https")
        return value.rstrip("/")

    @model_validator(mode="after")
    def _require_nim_key(self) -> Self:
        if self.llm_provider == "nim":
            key = self.nim_api_key.get_secret_value() if self.nim_api_key else ""
            if key in {"", "CHANGE_ME"}:
                raise ValueError("LLM_PROVIDER=nim necesita NIM_API_KEY")
        return self

    @property
    def trino_url(self) -> str:
        return f"http://{self.trino_host}:{self.trino_port}"

    @property
    def cors_origins(self) -> list[str]:
        return [origin.strip() for origin in self.backend_cors_origins.split(",") if origin.strip()]
