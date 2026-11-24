"""Contrato común de los proveedores."""

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any, Literal, Protocol

import httpx


class LLMError(RuntimeError):
    """El proveedor no ha devuelto una respuesta utilizable."""


@dataclass(frozen=True, slots=True)
class ChatMessage:
    role: Literal["system", "user", "assistant"]
    content: str

    def as_dict(self) -> dict[str, str]:
        return {"role": self.role, "content": self.content}


class LLMClient(Protocol):
    @property
    def provider(self) -> str: ...

    @property
    def model(self) -> str: ...

    @property
    def external(self) -> bool:
        """True si el prompt sale del host."""

    async def complete(self, messages: Sequence[ChatMessage]) -> str: ...


async def post_json(
    http: httpx.AsyncClient,
    url: str,
    payload: dict[str, Any],
    *,
    provider: str,
    max_seconds: float,
    headers: dict[str, str] | None = None,
) -> dict[str, Any]:
    """POST con errores traducidos. Los mensajes no incluyen cabeceras ni prompts."""
    try:
        response = await http.post(url, json=payload, headers=headers, timeout=max_seconds)
    except httpx.TimeoutException as error:
        raise LLMError(f"{provider}: tiempo de espera agotado") from error
    except httpx.HTTPError as error:
        raise LLMError(f"{provider}: no se puede conectar") from error
    if response.status_code != httpx.codes.OK:
        raise LLMError(f"{provider}: HTTP {response.status_code}")
    try:
        data = response.json()
    except ValueError as error:
        raise LLMError(f"{provider}: respuesta no válida") from error
    if not isinstance(data, dict):
        raise LLMError(f"{provider}: respuesta no válida")
    return data


def require_text(value: Any, provider: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise LLMError(f"{provider}: respuesta vacía")
    return value.strip()
