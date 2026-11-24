"""Proveedores de LLM. El resto de la API solo conoce LLMClient."""

import httpx

from lakehouse_api.config import Settings
from lakehouse_api.llm.base import ChatMessage, LLMClient, LLMError
from lakehouse_api.llm.nim import NimClient
from lakehouse_api.llm.ollama import OllamaClient

__all__ = ["ChatMessage", "LLMClient", "LLMError", "build_llm_client"]


def build_llm_client(settings: Settings, http: httpx.AsyncClient) -> LLMClient:
    if settings.llm_provider == "nim":
        return NimClient(settings, http)
    return OllamaClient(settings, http)
