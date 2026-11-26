"""Ciclo de transformación: Bronze -> Silver -> Gold -> catálogos."""

import logging
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta

import httpx
from deltalake.exceptions import DeltaError
from polars.exceptions import PolarsError

from medallion_worker import catalogs, gold, maintenance, silver
from medallion_worker.config import Settings
from medallion_worker.contracts import CONTRACTS
from medallion_worker.decoding import ContractError
from medallion_worker.gold_models import MODELS
from medallion_worker.storage import Lake

logger = logging.getLogger(__name__)

# Aunque no haya cambios, los catálogos se revisan: alguien puede haberlos tocado a mano.
SYNC_SECONDS = 600.0
STORAGE_ERRORS = (OSError, DeltaError, PolarsError)


@dataclass(slots=True)
class Summary:
    applied: int = 0
    models: list[str] = field(default_factory=list)
    broken: dict[str, str] = field(default_factory=dict)


class Transformer:
    """Un error de contrato detiene solo su tabla: las demás siguen y Gold conserva lo anterior."""

    def __init__(self, lake: Lake, language: str, salt: bytes) -> None:
        self._lake = lake
        self._language = language
        self._salt = salt
        # Versión de Bronze ya procesada por tabla: evita releer si no hay nada nuevo.
        self._seen: dict[str, int] = {}
        self._broken: dict[str, str] = {}

    def run_once(self, now: datetime | None = None) -> Summary:
        moment = now or datetime.now(tz=UTC)
        summary = Summary()
        for contract in CONTRACTS:
            version = self._lake.version(self._lake.bronze(contract.table))
            if version == self._seen.get(contract.table):
                continue
            try:
                result = silver.refresh(self._lake, contract, self._language, moment, self._salt)
            except ContractError as error:
                # Reintentar sin datos ni código nuevos no sirve: se espera a que cambie Bronze.
                self._broken[contract.table] = error.reason
                logger.error(
                    "contrato incumplido",
                    extra={"fields": {"table": error.table, "reason": error.reason}},
                )
            else:
                self._broken.pop(contract.table, None)
                summary.applied += result.applied
                if result.applied:
                    logger.info(
                        "silver actualizado",
                        extra={"fields": {"table": result.table, "applied": result.applied}},
                    )
            self._seen[contract.table] = version
        for model in MODELS:
            built = gold.refresh(self._lake, model)
            if built.written:
                summary.models.append(model.name)
                logger.info(
                    "gold actualizado",
                    extra={"fields": {"model": built.model, "rows": built.rows}},
                )
        summary.broken = dict(self._broken)
        return summary


def sync_catalogs(settings: Settings, lake: Lake, http: httpx.Client) -> int:
    entries = catalogs.expected(lake)
    trino = catalogs.TrinoClient(
        http, settings.trino_url, settings.trino_worker_user, settings.trino_catalog
    )
    unity = catalogs.UnityClient(http, settings.unity_catalog_url, settings.unity_catalog_name)
    return catalogs.sync_unity(unity, lake, entries) + catalogs.sync_trino(trino, lake, entries)


def _maintenance_day(settings: Settings) -> date:
    """Día de mantenimiento vigente: cambia a la hora configurada, en UTC."""
    return (datetime.now(tz=UTC) - timedelta(hours=settings.worker_maintenance_hour)).date()


def _maintain(lake: Lake, alive: Callable[[], None]) -> None:
    """Compacta y limpia Silver y Gold. Bronze tiene su propio servicio y su propio usuario."""
    try:
        locations = [e.location for e in catalogs.expected(lake) if lake.exists(e.location)]
    except STORAGE_ERRORS:
        logger.exception("fallo de almacenamiento")
        return
    maintenance.run(lake, locations, alive)


def run(
    settings: Settings,
    lake: Lake,
    stop: threading.Event,
    alive: Callable[[], None],
    clock: Callable[[], float] = time.monotonic,
) -> None:
    """Repite el ciclo. Un fallo de almacenamiento o de catálogo se reintenta en el siguiente."""
    transformer = Transformer(lake, settings.worker_language, settings.pii_salt)
    synced_at: float | None = None
    maintained = _maintenance_day(settings)
    with httpx.Client(timeout=catalogs.TIMEOUT) as http:
        while not stop.is_set():
            started = clock()
            try:
                summary = transformer.run_once()
                if summary.models or synced_at is None or started - synced_at > SYNC_SECONDS:
                    synced_at = None
                    changes = sync_catalogs(settings, lake, http)
                    synced_at = started
                    if changes:
                        logger.info(
                            "catálogos actualizados", extra={"fields": {"changes": changes}}
                        )
                if not summary.broken:
                    # Con un contrato roto, el healthcheck deja de pasar: alguien debe mirarlo.
                    alive()
            except (httpx.HTTPError, catalogs.CatalogError) as error:
                # Trino o Unity Catalog pueden arrancar más tarde: los datos ya están escritos.
                logger.warning("catálogo no disponible", extra={"fields": {"error": str(error)}})
                alive()
            except STORAGE_ERRORS:
                logger.exception("fallo de almacenamiento")
            if maintained != (today := _maintenance_day(settings)):
                maintained = today
                _maintain(lake, alive)
            stop.wait(max(0.0, settings.worker_transform_seconds - (clock() - started)))
