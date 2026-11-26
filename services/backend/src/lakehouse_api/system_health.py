"""Estado de las dependencias del backend (TD-015): GET /api/system/health.

Solo lo que el backend ve sin romper la segmentación de redes (regla 12 de CLAUDE.md): Trino,
el modelo, Unity Catalog y Langfuse. El slot de WAL, la identidad de réplica, el retraso de
Kafka, la memoria de los contenedores y el disco del equipo los comprueba
scripts/health_monitor.py desde el host, que también consulta esta ruta.

La ruta pasa por Caddy: no devuelve direcciones internas ni mensajes de error.
"""

import asyncio
import time
from collections.abc import Awaitable, Callable
from typing import Literal

import httpx
from pydantic import BaseModel

from lakehouse_api.config import Settings

Status = Literal["ok", "warning", "fail"]
TIMEOUT = 5.0
MIB = 1024 * 1024


class Check(BaseModel):
    name: str
    status: Status
    detail: str
    elapsed_ms: int


class SystemHealth(BaseModel):
    status: Status
    checks: list[Check]


Probe = Callable[[httpx.AsyncClient, Settings], Awaitable[tuple[Status, str]]]


async def _trino(http: httpx.AsyncClient, settings: Settings) -> tuple[Status, str]:
    info = (await http.get(f"{settings.trino_url}/v1/info", timeout=TIMEOUT)).raise_for_status()
    return ("warning", "arrancando") if info.json().get("starting") else ("ok", "en marcha")


async def _model(http: httpx.AsyncClient, settings: Settings) -> tuple[Status, str]:
    if settings.llm_provider != "ollama":
        return "ok", "proveedor externo: no se comprueba desde aquí"
    base = settings.ollama_base_url.rstrip("/")
    loaded = (await http.get(f"{base}/api/ps", timeout=TIMEOUT)).raise_for_status().json()
    for model in loaded.get("models") or []:
        if model.get("name") == settings.ollama_model:
            vram = int(model.get("size_vram") or 0) // MIB
            return "ok", f"cargado, {vram} MiB en la GPU"
    available = (await http.get(f"{base}/api/tags", timeout=TIMEOUT)).raise_for_status().json()
    names = {model.get("name") for model in available.get("models") or []}
    if settings.ollama_model in names:
        return "ok", "disponible; se carga con la primera pregunta"
    return "fail", "el modelo configurado no está descargado"


async def _catalog(http: httpx.AsyncClient, settings: Settings) -> tuple[Status, str]:
    url = f"{settings.unity_catalog_url.rstrip('/')}/api/2.1/unity-catalog/catalogs"
    (await http.get(url, timeout=TIMEOUT)).raise_for_status()
    return "ok", "en marcha"


async def _tracing(http: httpx.AsyncClient, settings: Settings) -> tuple[Status, str]:
    if not settings.langfuse_public_key or settings.langfuse_secret_key is None:
        return "ok", "desactivado"
    url = f"{settings.langfuse_host.rstrip('/')}/api/public/health"
    response = await http.get(url, timeout=TIMEOUT)
    # Sin trazas, las preguntas se siguen respondiendo: es un aviso, no un fallo.
    return ("ok", "en marcha") if response.is_success else ("warning", "no responde")


PROBES: dict[str, tuple[Probe, Status]] = {
    "trino": (_trino, "fail"),
    "modelo": (_model, "fail"),
    "unity_catalog": (_catalog, "fail"),
    "langfuse": (_tracing, "warning"),
}


async def _run(
    name: str, probe: Probe, on_error: Status, http: httpx.AsyncClient, settings: Settings
) -> Check:
    start = time.monotonic()
    try:
        status, detail = await probe(http, settings)
    except (httpx.HTTPError, ValueError):
        status, detail = on_error, "sin respuesta"
    return Check(
        name=name, status=status, detail=detail, elapsed_ms=int((time.monotonic() - start) * 1000)
    )


async def check(http: httpx.AsyncClient, settings: Settings) -> SystemHealth:
    checks = await asyncio.gather(
        *(_run(name, probe, on_error, http, settings) for name, (probe, on_error) in PROBES.items())
    )
    statuses = {item.status for item in checks}
    overall: Status = "fail" if "fail" in statuses else "warning" if "warning" in statuses else "ok"
    return SystemHealth(status=overall, checks=list(checks))
