"""Registro en Trino y Unity Catalog, y comparación, con servidores simulados."""

import json
import re
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import httpx
import pytest

import samples
from medallion_worker import catalogs
from medallion_worker.catalogs import CatalogError, Entry, TrinoClient, UnityClient
from medallion_worker.gold_models import MODELS
from medallion_worker.storage import Lake
from medallion_worker.transform import Transformer
from samples import NOW, SALT, local_lake, write_bronze


class FakeTrino:
    """Entiende solo las sentencias que envía el worker."""

    def __init__(self, entries: list[Entry]) -> None:
        self.entries = {(entry.schema, entry.name): entry for entry in entries}
        self.schemas: set[str] = set()
        self.tables: dict[str, set[str]] = {"silver": set(), "gold": set()}
        self.users: set[str] = set()
        self.pending: dict[str, dict[str, Any]] = {}

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.users.add(request.headers["x-trino-user"])
        if request.method == "GET":
            return httpx.Response(200, json=self.pending.pop(request.url.path))
        rows = self.run(request.content.decode())
        if isinstance(rows, dict):
            return httpx.Response(200, json=rows)
        # Como Trino: los datos llegan en una página posterior.
        path = f"/v1/statement/{len(self.pending)}"
        self.pending[path] = {"data": rows}
        return httpx.Response(200, json={"nextUri": f"http://trino:8080{path}"})

    def run(self, sql: str) -> list[list[Any]] | dict[str, Any]:
        if sql.startswith("SHOW SCHEMAS"):
            return [[name] for name in self.schemas]
        if match := re.match(r"CREATE SCHEMA lakehouse\.(\w+)", sql):
            self.schemas.add(match[1])
            return []
        if match := re.match(r"SHOW TABLES FROM lakehouse\.(\w+)", sql):
            return [[name] for name in self.tables[match[1]]]
        if match := re.search(
            r"(un)?register_table\(schema_name => '(\w+)', table_name => '(\w+)'", sql
        ):
            operation = self.tables[match[2]].discard if match[1] else self.tables[match[2]].add
            operation(match[3])
            return []
        if match := re.search(r"table_schema = '(\w+)'", sql):
            return [
                [name, column, catalogs.trino_type(kind).replace("(3)", "(6)")]
                for name in sorted(self.tables[match[1]])
                for column, kind in self.entries[match[1], name].types.items()
            ]
        return {"error": {"errorName": "SYNTAX_ERROR", "message": "no entendido"}}


class FakeUnity:
    def __init__(self) -> None:
        self.catalogs: set[str] = set()
        self.schemas: set[str] = set()
        self.tables: dict[str, dict[str, Any]] = {}
        self.created: list[str] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path.removeprefix("/api/2.1/unity-catalog")
        body = json.loads(request.content) if request.content else {}
        if path == "/catalogs" and request.method == "POST":
            self.catalogs.add(body["name"])
        elif path == "/schemas" and request.method == "POST":
            self.schemas.add(f"{body['catalog_name']}.{body['name']}")
        elif path == "/tables" and request.method == "POST":
            name = f"{body['catalog_name']}.{body['schema_name']}.{body['name']}"
            if name in self.tables:
                return httpx.Response(409, text="ALREADY_EXISTS")
            self.tables[name] = body
            self.created.append(name)
        elif path == "/tables":
            return self.page(request)
        elif path.startswith("/tables/") and request.method == "DELETE":
            del self.tables[path.removeprefix("/tables/")]
            # Como Unity Catalog 0.7: no es JSON.
            return httpx.Response(200, text="200 OK")
        elif path.startswith("/catalogs/"):
            return httpx.Response(200 if path[10:] in self.catalogs else 404, json={})
        elif path.startswith("/schemas/"):
            return httpx.Response(200 if path[9:] in self.schemas else 404, json={})
        return httpx.Response(200, json={})

    def page(self, request: httpx.Request) -> httpx.Response:
        schema = request.url.params["schema_name"]
        names = sorted(n for n in self.tables if n.split(".")[1] == schema)
        start = int(request.url.params.get("page_token", "0"))
        chunk = names[start : start + 5]
        following = str(start + 5) if start + 5 < len(names) else None
        tables = [{**self.tables[n], "name": n.split(".")[2]} for n in chunk]
        return httpx.Response(200, json={"tables": tables, "next_page_token": following})


class World:
    def __init__(self, lake: Lake) -> None:
        self.lake = lake
        self.entries = catalogs.expected(lake)
        self.fake_trino = FakeTrino(self.entries)
        self.fake_unity = FakeUnity()
        self.http = httpx.Client(transport=httpx.MockTransport(self.dispatch))
        self.trino = TrinoClient(self.http, "http://trino:8080", "medallion-worker", "lakehouse")
        self.unity = UnityClient(self.http, "http://unity-catalog:8080/", "lakehouse")

    def dispatch(self, request: httpx.Request) -> httpx.Response:
        handler = self.fake_trino if request.url.host == "trino" else self.fake_unity
        return handler(request)

    def sync(self) -> int:
        return catalogs.sync_unity(self.unity, self.lake, self.entries) + catalogs.sync_trino(
            self.trino, self.lake, self.entries
        )

    def compare(self) -> list[str]:
        return catalogs.compare(self.trino, self.unity, self.entries)


@pytest.fixture(scope="module")
def lake(tmp_path_factory: pytest.TempPathFactory) -> Lake:
    path = tmp_path_factory.mktemp("lake")
    write_bronze(path, samples.demo_events())
    result = local_lake(path)
    Transformer(result, "es_ES", SALT).run_once(NOW)
    return result


@pytest.fixture
def world(lake: Lake) -> Iterator[World]:
    result = World(lake)
    yield result
    result.http.close()


def test_every_table_is_registered_in_both_catalogs(world: World) -> None:
    assert world.sync() > 0
    assert world.fake_trino.tables["gold"] == {model.name for model in MODELS}
    assert len(world.fake_unity.tables) == len(world.entries) == 11 + len(MODELS)
    assert world.compare() == []
    assert world.fake_trino.users == {"medallion-worker"}


def test_synchronising_again_changes_nothing(world: World) -> None:
    world.sync()
    created = list(world.fake_unity.created)
    assert world.sync() == 0
    assert world.fake_unity.created == created


def test_descriptions_and_decimals_reach_unity_catalog(world: World) -> None:
    world.sync()
    table = world.fake_unity.tables["lakehouse.gold.fact_sales_orders"]
    assert table["table_type"] == "EXTERNAL"
    assert table["comment"].startswith("Pedidos de venta")
    total = next(column for column in table["columns"] if column["name"] == "amount_total")
    assert (total["type_name"], total["type_text"]) == ("DECIMAL", "decimal(18,2)")
    assert (total["type_precision"], total["type_scale"]) == (18, 2)
    assert total["comment"] == "Importe total con impuestos"
    assert all(column["comment"] for column in table["columns"])


def test_a_changed_description_is_registered_again(world: World) -> None:
    world.sync()
    world.fake_unity.tables["lakehouse.gold.dim_customer"]["comment"] = "manipulada"
    assert world.compare() == ["gold.dim_customer: descripción distinta en Unity Catalog"]
    assert world.sync() == 1
    assert world.compare() == []


def test_tables_nobody_declared_are_removed(world: World) -> None:
    world.sync()
    world.fake_trino.tables["gold"].add("intrusa")
    world.fake_trino.entries["gold", "intrusa"] = world.entries[-1]
    assert world.compare() == ["gold.intrusa: falta en declarada, Unity Catalog"]
    assert world.sync() == 1
    assert world.compare() == []


@pytest.mark.parametrize(
    ("change", "problem"),
    [
        (lambda w: w.fake_trino.tables["gold"].discard("dim_customer"), "falta en Trino"),
        (
            lambda w: w.fake_unity.tables.pop("lakehouse.silver.sale_order"),
            "silver.sale_order: falta en Unity Catalog",
        ),
        (
            lambda w: w.fake_unity.tables["lakehouse.gold.dim_product"]["columns"].pop(),
            "gold.dim_product: columnas distintas en Unity Catalog",
        ),
        (
            lambda w: w.fake_unity.tables["lakehouse.gold.dim_product"].update(
                storage_location="s3://otro"
            ),
            "gold.dim_product: ubicación distinta",
        ),
    ],
)
def test_the_comparison_detects_divergence(world: World, change: Any, problem: str) -> None:
    world.sync()
    change(world)
    [found] = world.compare()
    assert problem in found


def test_trino_errors_are_reported(world: World) -> None:
    with pytest.raises(CatalogError, match="SYNTAX_ERROR"):
        world.trino.query("SELECT 1 FROM")


def test_tables_that_do_not_exist_yet_are_not_registered(tmp_path: Path) -> None:
    world = World(local_lake(tmp_path))
    world.sync()
    assert world.fake_unity.tables == {}
    assert world.fake_trino.tables == {"silver": set(), "gold": set()}
