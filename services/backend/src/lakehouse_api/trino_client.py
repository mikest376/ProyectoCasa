"""Cliente asíncrono de Trino, de solo lectura, con límite de filas y de tiempo.

Al vencer el plazo se cancela la consulta en Trino: no sigue gastando memoria.
"""

import asyncio
import time
from dataclasses import dataclass
from typing import Any

import httpx

# Errores de análisis: su mensaje solo contiene nombres de la propia consulta, nunca datos.
ANALYSIS_ERRORS = frozenset(
    {
        "AMBIGUOUS_NAME",
        "COLUMN_NOT_FOUND",
        "EXPRESSION_NOT_AGGREGATE",
        "EXPRESSION_NOT_IN_DISTINCT",
        "EXPRESSION_NOT_SCALAR",
        "FUNCTION_NOT_FOUND",
        "INVALID_FUNCTION_ARGUMENT",
        "MISSING_COLUMN_NAME",
        "MISSING_GROUP_BY",
        "NESTED_AGGREGATION",
        "NOT_SUPPORTED",
        "SYNTAX_ERROR",
        "TABLE_NOT_FOUND",
        "TYPE_MISMATCH",
    }
)
RETRY_STATUS = frozenset({429, 502, 503, 504})


class TrinoError(RuntimeError):
    """Trino no ha podido ejecutar la consulta."""

    def __init__(self, name: str, message: str) -> None:
        super().__init__(message)
        self.name = name

    @property
    def repairable(self) -> bool:
        """El modelo puede corregirlo con el mensaje, que no contiene datos."""
        return self.name in ANALYSIS_ERRORS


class TrinoTimeoutError(TrinoError):
    def __init__(self) -> None:
        super().__init__("TIMEOUT", "la consulta ha superado el tiempo máximo")


@dataclass(frozen=True, slots=True)
class ResultColumn:
    name: str
    type: str


@dataclass(frozen=True, slots=True)
class QueryResult:
    columns: tuple[ResultColumn, ...]
    rows: list[list[Any]]
    milliseconds: int


class TrinoClient:
    def __init__(
        self,
        http: httpx.AsyncClient,
        url: str,
        user: str,
        catalog: str,
        schema: str,
        max_rows: int,
        max_seconds: float,
    ) -> None:
        self._http = http
        self._url = f"{url.rstrip('/')}/v1/statement"
        self._headers = {
            "X-Trino-User": user,
            "X-Trino-Catalog": catalog,
            "X-Trino-Schema": schema,
            "X-Trino-Source": "text2sql",
            "X-Trino-Time-Zone": "UTC",
        }
        self._max_rows = max_rows
        self._max_seconds = max_seconds

    async def query(self, sql: str) -> QueryResult:
        started = time.monotonic()
        pending: list[str] = []
        try:
            async with asyncio.timeout(self._max_seconds):
                columns, rows = await self._run(sql, pending)
        except TimeoutError as error:
            await self._cancel(pending)
            raise TrinoTimeoutError from error
        except httpx.HTTPError as error:
            raise TrinoError("UNAVAILABLE", "Trino no responde") from error
        milliseconds = round((time.monotonic() - started) * 1000)
        return QueryResult(columns, rows[: self._max_rows], milliseconds)

    async def _run(
        self, sql: str, pending: list[str]
    ) -> tuple[tuple[ResultColumn, ...], list[list[Any]]]:
        columns: tuple[ResultColumn, ...] = ()
        rows: list[list[Any]] = []
        page = await self._send("POST", self._url, sql)
        while True:
            error = page.get("error")
            if error:
                raise TrinoError(str(error.get("errorName")), str(error.get("message")))
            if page.get("columns"):
                columns = tuple(ResultColumn(c["name"], c["type"]) for c in page["columns"])
            rows += page.get("data") or []
            following = page.get("nextUri")
            pending[:] = [following] if following else []
            if following is None or len(rows) > self._max_rows:
                if following is not None:
                    await self._cancel(pending)
                return columns, rows
            page = await self._send("GET", following)

    async def _send(self, method: str, url: str, body: str | None = None) -> dict[str, Any]:
        for attempt in range(8):
            response = await self._http.request(
                method, url, content=body, headers=self._headers, timeout=self._max_seconds
            )
            if response.status_code not in RETRY_STATUS:
                break
            await asyncio.sleep(0.1 * 2**attempt)
        if response.status_code != httpx.codes.OK:
            raise TrinoError("UNAVAILABLE", f"Trino: HTTP {response.status_code}")
        page: dict[str, Any] = response.json()
        return page

    async def _cancel(self, pending: list[str]) -> None:
        for url in pending:
            try:
                await self._http.delete(url, headers=self._headers, timeout=5)
            except httpx.HTTPError:
                return
