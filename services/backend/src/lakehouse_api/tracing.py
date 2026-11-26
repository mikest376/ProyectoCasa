"""Trazas en Langfuse 2, por su API HTTP de ingesta.

Se guardan la pregunta, el prompt (metadatos de Gold), la respuesta del modelo, el SQL y los
tiempos. Nunca filas de resultados. Un fallo de Langfuse no afecta a la respuesta.
"""

import logging
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, Protocol

import httpx

from lakehouse_api.config import Settings

logger = logging.getLogger(__name__)

TIMEOUT = 5.0


def _now() -> datetime:
    return datetime.now(tz=UTC)


@dataclass(slots=True)
class Generation:
    prompt: list[dict[str, str]]
    completion: str
    model: str
    started: datetime
    ended: datetime
    prompt_tokens: int | None = None
    completion_tokens: int | None = None
    error: str | None = None


@dataclass(slots=True)
class Trace:
    """Lo ocurrido con una pregunta."""

    question: str
    provider: str
    model: str
    identifier: str = field(default_factory=lambda: uuid.uuid4().hex)
    started: datetime = field(default_factory=_now)
    generations: list[Generation] = field(default_factory=list)
    sql: str | None = None
    status: str = "ok"
    query_started: datetime | None = None
    query_ended: datetime | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


class Tracer(Protocol):
    @property
    def enabled(self) -> bool: ...

    async def send(self, trace: Trace) -> None: ...


class NullTracer:
    enabled = False

    async def send(self, trace: Trace) -> None:
        """Sin claves de Langfuse no se traza."""


class LangfuseTracer:
    enabled = True

    def __init__(
        self, http: httpx.AsyncClient, host: str, public_key: str, secret_key: str
    ) -> None:
        self._http = http
        self._url = f"{host.rstrip('/')}/api/public/ingestion"
        self._auth = httpx.BasicAuth(public_key, secret_key)

    async def send(self, trace: Trace) -> None:
        try:
            response = await self._http.post(
                self._url, json={"batch": events(trace)}, auth=self._auth, timeout=TIMEOUT
            )
            rejected = response.json().get("errors") if response.content else None
            if response.status_code >= httpx.codes.BAD_REQUEST or rejected:
                logger.warning(
                    "traza rechazada", extra={"fields": {"status": response.status_code}}
                )
        except (httpx.HTTPError, ValueError) as error:
            logger.warning("traza no enviada", extra={"fields": {"error": type(error).__name__}})


def events(trace: Trace) -> list[dict[str, Any]]:
    batch = [
        _event(
            "trace-create",
            {
                "id": trace.identifier,
                "timestamp": trace.started.isoformat(),
                "name": "text2sql",
                "input": trace.question,
                "output": {"sql": trace.sql, "status": trace.status},
                "metadata": trace.metadata,
                "tags": [trace.provider, trace.status],
            },
        )
    ]
    for number, generation in enumerate(trace.generations, start=1):
        batch.append(
            _event(
                "generation-create",
                {
                    "id": uuid.uuid4().hex,
                    "traceId": trace.identifier,
                    "name": f"intento {number}",
                    "model": generation.model,
                    "input": generation.prompt,
                    "output": generation.completion,
                    "startTime": generation.started.isoformat(),
                    "endTime": generation.ended.isoformat(),
                    "usage": {
                        "input": generation.prompt_tokens,
                        "output": generation.completion_tokens,
                        "unit": "TOKENS",
                    },
                    "level": "ERROR" if generation.error else "DEFAULT",
                    "statusMessage": generation.error,
                },
            )
        )
    if trace.query_started and trace.query_ended:
        batch.append(
            _event(
                "span-create",
                {
                    "id": uuid.uuid4().hex,
                    "traceId": trace.identifier,
                    "name": "trino",
                    "input": trace.sql,
                    "startTime": trace.query_started.isoformat(),
                    "endTime": trace.query_ended.isoformat(),
                },
            )
        )
    return batch


def _event(kind: str, body: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": uuid.uuid4().hex,
        "type": kind,
        "timestamp": _now().isoformat(),
        "body": {key: value for key, value in body.items() if value is not None},
    }


def build_tracer(settings: Settings, http: httpx.AsyncClient) -> Tracer:
    if settings.langfuse_public_key and settings.langfuse_secret_key:
        return LangfuseTracer(
            http,
            settings.langfuse_host,
            settings.langfuse_public_key,
            settings.langfuse_secret_key.get_secret_value(),
        )
    return NullTracer()
