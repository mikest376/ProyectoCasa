"""Esquema de Gold, leído de Unity Catalog: es lo único que se enseña al modelo."""

import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

import httpx

PAGE = 50
TIMEOUT = 15.0


class CatalogUnavailableError(RuntimeError):
    """Unity Catalog no responde o no tiene tablas de Gold."""


@dataclass(frozen=True, slots=True)
class Column:
    name: str
    type: str
    description: str


@dataclass(frozen=True, slots=True)
class Table:
    name: str
    description: str
    domain: str
    columns: tuple[Column, ...]


def parse_table(data: dict[str, Any]) -> Table:
    columns = sorted(data.get("columns") or [], key=lambda column: column["position"])
    # La ubicación es s3://gold/<dominio>/<modelo>.
    parts = str(data.get("storage_location", "")).rstrip("/").split("/")
    return Table(
        name=data["name"],
        description=data.get("comment") or "",
        domain=parts[-2] if len(parts) >= 2 else "",
        columns=tuple(
            Column(column["name"], str(column["type_text"]).lower(), column.get("comment") or "")
            for column in columns
        ),
    )


class GoldCatalog:
    """Tablas de Gold con una caché breve: cambian poco y se usan en cada pregunta."""

    def __init__(
        self,
        http: httpx.AsyncClient,
        url: str,
        catalog: str,
        schema: str,
        ttl: float,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._http = http
        self._url = f"{url.rstrip('/')}/api/2.1/unity-catalog/tables"
        self._catalog = catalog
        self._schema = schema
        self._ttl = ttl
        self._clock = clock
        self._tables: tuple[Table, ...] = ()
        self._loaded_at: float | None = None

    async def tables(self) -> tuple[Table, ...]:
        fresh = self._loaded_at is not None and self._clock() - self._loaded_at < self._ttl
        if fresh:
            return self._tables
        try:
            tables = await self._load()
        except (httpx.HTTPError, KeyError, TypeError, ValueError) as error:
            # Con una copia anterior se sigue respondiendo: los nombres apenas cambian.
            if self._tables:
                return self._tables
            raise CatalogUnavailableError("Unity Catalog no responde") from error
        if not tables:
            raise CatalogUnavailableError("no hay tablas en Gold")
        self._tables, self._loaded_at = tables, self._clock()
        return tables

    async def _load(self) -> tuple[Table, ...]:
        found: list[Table] = []
        token = ""
        while True:
            parameters: dict[str, str | int] = {
                "catalog_name": self._catalog,
                "schema_name": self._schema,
                "max_results": PAGE,
            }
            if token:
                parameters["page_token"] = token
            response = await self._http.get(self._url, params=parameters, timeout=TIMEOUT)
            response.raise_for_status()
            page = response.json()
            found += [parse_table(item) for item in page.get("tables") or []]
            token = page.get("next_page_token") or ""
            if not token:
                return tuple(sorted(found, key=lambda table: table.name))
