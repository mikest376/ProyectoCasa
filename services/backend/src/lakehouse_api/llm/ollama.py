"""Proveedor local: Ollama con GPU. El prompt no sale del host."""

from collections.abc import Sequence
from typing import Any

import httpx

from lakehouse_api.config import Settings
from lakehouse_api.llm.base import (
    ChatMessage,
    Completion,
    post_json,
    require_text,
    token_count,
)


class OllamaClient:
    provider = "ollama"
    external = False

    def __init__(self, settings: Settings, http: httpx.AsyncClient) -> None:
        self.model = settings.ollama_model
        self._url = f"{settings.ollama_base_url.rstrip('/')}/api/chat"
        self._timeout = settings.llm_request_timeout_seconds
        self._options = {
            "temperature": 0,
            "seed": 7,
            "num_ctx": settings.ollama_context_length,
            "num_predict": settings.llm_max_tokens,
        }
        self._http = http

    async def complete(
        self, messages: Sequence[ChatMessage], schema: dict[str, Any] | None = None
    ) -> Completion:
        payload: dict[str, Any] = {
            "model": self.model,
            "messages": [message.as_dict() for message in messages],
            "stream": False,
            "options": self._options,
        }
        if schema is not None:
            # Ollama restringe la generación: la respuesta siempre es JSON con esta forma.
            payload["format"] = schema
        data = await post_json(
            self._http, self._url, payload, provider=self.provider, max_seconds=self._timeout
        )
        message = data.get("message")
        text = require_text(
            message.get("content") if isinstance(message, dict) else None, self.provider
        )
        return Completion(
            text, token_count(data.get("prompt_eval_count")), token_count(data.get("eval_count"))
        )
