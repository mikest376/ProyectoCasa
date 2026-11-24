"""Proveedor local: Ollama con GPU. El prompt no sale del host."""

from collections.abc import Sequence

import httpx

from lakehouse_api.config import Settings
from lakehouse_api.llm.base import ChatMessage, post_json, require_text


class OllamaClient:
    provider = "ollama"
    external = False

    def __init__(self, settings: Settings, http: httpx.AsyncClient) -> None:
        self.model = settings.ollama_model
        self._url = f"{settings.ollama_base_url.rstrip('/')}/api/chat"
        self._timeout = settings.llm_request_timeout_seconds
        self._http = http

    async def complete(self, messages: Sequence[ChatMessage]) -> str:
        data = await post_json(
            self._http,
            self._url,
            {
                "model": self.model,
                "messages": [message.as_dict() for message in messages],
                "stream": False,
                "options": {"temperature": 0},
            },
            provider=self.provider,
            max_seconds=self._timeout,
        )
        message = data.get("message")
        return require_text(
            message.get("content") if isinstance(message, dict) else None, self.provider
        )
