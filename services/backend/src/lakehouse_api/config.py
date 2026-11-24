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

    ollama_base_url: str = "http://ollama:11434"
    ollama_model: str = "qwen2.5-coder:3b-instruct-q4_K_M"

    nim_base_url: str = "https://integrate.api.nvidia.com/v1"
    nim_model: str = "nvidia/nemotron-3.5-lightning-30b-a3b"
    nim_api_key: SecretStr | None = None
    nim_max_tokens: int = Field(default=4096, gt=0)

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
    def cors_origins(self) -> list[str]:
        return [origin.strip() for origin in self.backend_cors_origins.split(",") if origin.strip()]
