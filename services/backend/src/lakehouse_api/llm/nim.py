"""Proveedor externo: API de NVIDIA NIM, compatible con OpenAI.

El prompt sale del host. Solo puede contener metadatos de Gold y la pregunta, nunca
filas de resultados (CLAUDE.md, regla 10; deuda TD-021).
"""

from collections.abc import Sequence
from typing import Any

import httpx

from lakehouse_api.config import Settings
from lakehouse_api.llm.base import (
    ChatMessage,
    Completion,
    LLMError,
    post_json,
    require_text,
    token_count,
)


class NimClient:
    provider = "nim"
    external = True

    def __init__(self, settings: Settings, http: httpx.AsyncClient) -> None:
        if settings.nim_api_key is None:
            raise LLMError("nim: falta NIM_API_KEY")
        self.model = settings.nim_model
        self._api_key = settings.nim_api_key
        self._url = f"{settings.nim_base_url}/chat/completions"
        self._max_tokens = settings.nim_max_tokens
        self._timeout = settings.llm_request_timeout_seconds
        self._http = http

    async def complete(
        self, messages: Sequence[ChatMessage], schema: dict[str, Any] | None = None
    ) -> Completion:
        # El catálogo de NIM no garantiza salida estructurada en todos sus modelos: el prompt pide
        # JSON y la API comprueba el formato al recibirlo (TD-021, sin probar con una clave).
        del schema
        data = await post_json(
            self._http,
            self._url,
            {
                "model": self.model,
                "messages": [message.as_dict() for message in messages],
                "stream": False,
                "temperature": 0,
                "max_tokens": self._max_tokens,
            },
            provider=self.provider,
            max_seconds=self._timeout,
            headers={"Authorization": f"Bearer {self._api_key.get_secret_value()}"},
        )
        usage = data.get("usage")
        counts = usage if isinstance(usage, dict) else {}
        return Completion(
            require_text(_first_content(data), self.provider),
            token_count(counts.get("prompt_tokens")),
            token_count(counts.get("completion_tokens")),
        )


def _first_content(data: dict[str, Any]) -> Any:
    """Texto final. El razonamiento (reasoning_content) se descarta."""
    choices = data.get("choices")
    if not isinstance(choices, list) or not choices or not isinstance(choices[0], dict):
        return None
    message = choices[0].get("message")
    return message.get("content") if isinstance(message, dict) else None
