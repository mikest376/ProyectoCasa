"""Registro de las tablas en Trino y en Unity Catalog, y comparación de los dos (TD-002).

Unity Catalog es el catálogo de referencia: de él sale el esquema que se enseña al LLM. Trino
necesita su propio registro porque no lee Unity Catalog OSS para tablas Delta. Los dos se derivan
de las mismas definiciones, así que solo pueden divergir si alguien los cambia a mano: de ahí la
comparación.
"""

import json
import logging
import time
from dataclasses import dataclass
from typing import Any

import httpx
import polars as pl

from medallion_worker.contracts import CONTRACTS
from medallion_worker.gold_models import MODELS
from medallion_worker.silver import silver_columns
from medallion_worker.storage import Columns, Lake, delta_type

logger = logging.getLogger(__name__)

TIMEOUT = 60.0
SCHEMAS = {
    "silver": "Estado actual de las tablas core de Odoo, tipado y sin duplicados",
    "gold": "Modelo dimensional para consumo. Única capa que consulta Text-to-SQL",
}


class CatalogError(RuntimeError):
    """Trino o Unity Catalog han rechazado una operación."""


@dataclass(frozen=True, slots=True)
class Entry:
    """Una tabla que debe existir en los dos catálogos."""

    schema: str
    name: str
    description: str
    location: str
    columns: Columns

    @property
    def types(self) -> dict[str, str]:
        return {name: delta_type(dtype) for name, dtype, _ in self.columns}


def expected(lake: Lake) -> list[Entry]:
    silver = [
        Entry(
            "silver",
            contract.table,
            contract.description,
            lake.silver(contract.domain, contract.table),
            silver_columns(contract),
        )
        for contract in CONTRACTS
    ]
    gold = [
        Entry(
            "gold",
            model.name,
            model.description,
            lake.gold(model.domain, model.name),
            model.columns,
        )
        for model in MODELS
    ]
    return silver + gold


# --- Trino --------------------------------------------------------------------------------------

# Tipo Delta -> tipo que muestra Trino.
TRINO_TYPES = {
    "string": "varchar",
    "integer": "integer",
    "long": "bigint",
    "double": "double",
    "boolean": "boolean",
    "date": "date",
    "timestamp": "timestamp(3) with time zone",
}


def trino_type(delta: str) -> str:
    return TRINO_TYPES.get(delta, delta)


class TrinoClient:
    """Cliente mínimo del protocolo HTTP de Trino."""

    def __init__(self, http: httpx.Client, url: str, user: str, catalog: str) -> None:
        self._http = http
        self._url = url.rstrip("/")
        self._headers = {"X-Trino-User": user, "X-Trino-Source": "medallion-worker"}
        self.catalog = catalog

    def query(self, sql: str) -> list[list[Any]]:
        rows: list[list[Any]] = []
        page = self._send("POST", f"{self._url}/v1/statement", sql)
        while True:
            error = page.get("error")
            if error is not None:
                raise CatalogError(f"trino: {error.get('errorName')}: {error.get('message')}")
            rows += page.get("data") or []
            following = page.get("nextUri")
            if following is None:
                return rows
            page = self._send("GET", following)

    def _send(self, method: str, url: str, body: str | None = None) -> dict[str, Any]:
        for attempt in range(10):
            response = self._http.request(method, url, content=body, headers=self._headers)
            if response.status_code not in (429, 502, 503, 504):
                break
            time.sleep(0.1 * 2**attempt)
        if response.status_code != httpx.codes.OK:
            raise CatalogError(f"trino: HTTP {response.status_code}")
        page: dict[str, Any] = response.json()
        return page

    def tables(self, schema: str) -> dict[str, dict[str, str]]:
        """Tablas registradas en un esquema, con sus columnas y tipos."""
        found: dict[str, dict[str, str]] = {}
        # El catálogo y los esquemas son constantes del código, no entradas externas.
        rows = self.query(
            "SELECT table_name, column_name, data_type "  # noqa: S608
            f"FROM {self.catalog}.information_schema.columns "
            f"WHERE table_schema = '{schema}' ORDER BY table_name, ordinal_position"
        )
        for table, column, data_type in rows:
            found.setdefault(table, {})[column] = data_type
        return found

    def register(self, entry: Entry) -> None:
        self.query(
            f"CALL {self.catalog}.system.register_table("
            f"schema_name => '{entry.schema}', table_name => '{entry.name}', "
            f"table_location => '{entry.location}')"
        )

    def unregister(self, schema: str, name: str) -> None:
        self.query(
            f"CALL {self.catalog}.system.unregister_table("
            f"schema_name => '{schema}', table_name => '{name}')"
        )


def sync_trino(trino: TrinoClient, lake: Lake, entries: list[Entry]) -> int:
    """Registra lo que falta y retira lo que sobra. Devuelve el número de cambios."""
    changes = 0
    schemas = {row[0] for row in trino.query(f"SHOW SCHEMAS FROM {trino.catalog}")}
    for schema in SCHEMAS:
        if schema not in schemas:
            trino.query(f"CREATE SCHEMA {trino.catalog}.{schema}")
            changes += 1
        registered = {row[0] for row in trino.query(f"SHOW TABLES FROM {trino.catalog}.{schema}")}
        wanted = {entry.name: entry for entry in entries if entry.schema == schema}
        for name, entry in wanted.items():
            if name not in registered and lake.exists(entry.location):
                trino.register(entry)
                changes += 1
        for name in registered - set(wanted):
            trino.unregister(schema, name)
            changes += 1
    return changes


# --- Unity Catalog --------------------------------------------------------------------------------

UNITY_TYPES = {
    "string": "STRING",
    "integer": "INT",
    "long": "LONG",
    "double": "DOUBLE",
    "boolean": "BOOLEAN",
    "date": "DATE",
    "timestamp": "TIMESTAMP",
    "binary": "BINARY",
}
UNITY_TEXT = {"INT": "int", "LONG": "bigint"}


def unity_column(position: int, name: str, dtype: pl.DataType, comment: str) -> dict[str, Any]:
    delta = delta_type(dtype)
    column: dict[str, Any] = {
        "name": name,
        "position": position,
        "nullable": True,
        "comment": comment,
        "type_json": json.dumps(
            {"name": name, "type": delta, "nullable": True, "metadata": {"comment": comment}},
            ensure_ascii=False,
        ),
    }
    if isinstance(dtype, pl.Decimal):
        column |= {
            "type_name": "DECIMAL",
            "type_text": delta,
            "type_precision": dtype.precision,
            "type_scale": dtype.scale,
        }
    else:
        type_name = UNITY_TYPES[delta]
        column |= {
            "type_name": type_name,
            "type_text": UNITY_TEXT.get(type_name, type_name.lower()),
        }
    return column


class UnityClient:
    """Cliente de la API REST de Unity Catalog OSS."""

    def __init__(self, http: httpx.Client, url: str, catalog: str) -> None:
        self._http = http
        self._api = f"{url.rstrip('/')}/api/2.1/unity-catalog"
        self.catalog = catalog

    def _call(
        self, method: str, path: str, body: dict[str, Any] | None = None, missing_ok: bool = False
    ) -> dict[str, Any] | None:
        response = self._http.request(method, f"{self._api}{path}", json=body)
        if missing_ok and response.status_code == httpx.codes.NOT_FOUND:
            return None
        if response.status_code >= httpx.codes.BAD_REQUEST:
            raise CatalogError(
                f"unity: {method} {path}: HTTP {response.status_code}: {response.text[:200]}"
            )
        # DELETE responde con el texto "200 OK", que no es JSON.
        data: dict[str, Any] = response.json() if method == "GET" else {}
        return data

    def ensure_schemas(self) -> int:
        changes = 0
        if self._call("GET", f"/catalogs/{self.catalog}", missing_ok=True) is None:
            self._call(
                "POST",
                "/catalogs",
                {"name": self.catalog, "comment": "Lakehouse de Odoo: Silver y Gold"},
            )
            changes += 1
        for schema, comment in SCHEMAS.items():
            path = f"/schemas/{self.catalog}.{schema}"
            if self._call("GET", path, missing_ok=True) is None:
                self._call(
                    "POST",
                    "/schemas",
                    {"name": schema, "catalog_name": self.catalog, "comment": comment},
                )
                changes += 1
        return changes

    def tables(self, schema: str) -> dict[str, dict[str, Any]]:
        found: dict[str, dict[str, Any]] = {}
        token = ""
        while True:
            page = self._call(
                "GET",
                f"/tables?catalog_name={self.catalog}&schema_name={schema}&max_results=50"
                + (f"&page_token={token}" if token else ""),
            )
            assert page is not None  # noqa: S101
            for table in page.get("tables") or []:
                found[table["name"]] = table
            token = page.get("next_page_token") or ""
            if not token:
                return found

    def payload(self, entry: Entry) -> dict[str, Any]:
        return {
            "name": entry.name,
            "catalog_name": self.catalog,
            "schema_name": entry.schema,
            "table_type": "EXTERNAL",
            "data_source_format": "DELTA",
            "storage_location": entry.location,
            "comment": entry.description,
            "columns": [
                unity_column(position, name, dtype, comment)
                for position, (name, dtype, comment) in enumerate(entry.columns)
            ],
        }

    def create(self, entry: Entry) -> None:
        self._call("POST", "/tables", self.payload(entry))

    def delete(self, schema: str, name: str) -> None:
        self._call("DELETE", f"/tables/{self.catalog}.{schema}.{name}")


def _unity_signature(table: dict[str, Any]) -> tuple[Any, ...]:
    """Lo que distingue dos definiciones de la misma tabla."""
    columns = sorted(table.get("columns") or [], key=lambda column: column["position"])
    return (
        table.get("comment"),
        str(table.get("storage_location", "")).rstrip("/"),
        tuple(
            (column["name"], str(column["type_text"]).lower(), column.get("comment"))
            for column in columns
        ),
    )


def sync_unity(unity: UnityClient, lake: Lake, entries: list[Entry]) -> int:
    """Registra lo que falta, rehace lo que ha cambiado y retira lo que sobra."""
    changes = unity.ensure_schemas()
    for schema in SCHEMAS:
        registered = unity.tables(schema)
        wanted = {entry.name: entry for entry in entries if entry.schema == schema}
        for name, entry in wanted.items():
            if not lake.exists(entry.location):
                continue
            current = registered.get(name)
            if current is not None:
                if _unity_signature(current) == _unity_signature(unity.payload(entry)):
                    continue
                unity.delete(schema, name)
            unity.create(entry)
            changes += 1
        for name in set(registered) - set(wanted):
            unity.delete(schema, name)
            changes += 1
    return changes


# --- Comparación --------------------------------------------------------------------------------


def compare(trino: TrinoClient, unity: UnityClient, entries: list[Entry]) -> list[str]:
    """Diferencias entre los dos catálogos y con lo declarado. Vacío si coinciden."""
    problems: list[str] = []
    for schema in SCHEMAS:
        wanted = {entry.name: entry for entry in entries if entry.schema == schema}
        in_trino, in_unity = trino.tables(schema), unity.tables(schema)
        for name in sorted(set(wanted) | set(in_trino) | set(in_unity)):
            label = f"{schema}.{name}"
            places = {
                "declarada": name in wanted,
                "Trino": name in in_trino,
                "Unity Catalog": name in in_unity,
            }
            if not all(places.values()):
                missing = [place for place, present in places.items() if not present]
                problems.append(f"{label}: falta en {', '.join(missing)}")
                continue
            declared = wanted[name]
            columns = sorted(in_unity[name]["columns"], key=lambda column: column["position"])
            unity_types = {
                column["name"]: json.loads(column["type_json"])["type"] for column in columns
            }
            if unity_types != declared.types:
                problems.append(f"{label}: columnas distintas en Unity Catalog")
            trino_types = {column: trino_type(kind) for column, kind in declared.types.items()}
            actual = {
                column: kind.replace("timestamp(6)", "timestamp(3)")
                for column, kind in in_trino[name].items()
            }
            if actual != trino_types:
                problems.append(f"{label}: columnas distintas en Trino")
            location = str(in_unity[name]["storage_location"]).rstrip("/")
            if location != declared.location:
                problems.append(f"{label}: ubicación distinta en Unity Catalog")
            if in_unity[name].get("comment") != declared.description:
                problems.append(f"{label}: descripción distinta en Unity Catalog")
    return problems
