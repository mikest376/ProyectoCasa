"""MinIO, Trino y Unity Catalog reales. Los arranca scripts/test_integration.sh.

Recorre Bronze -> Silver -> Gold -> catálogos con los usuarios de cada servicio, y comprueba que
los dos catálogos coinciden (TD-002) y que cada usuario hace solo lo suyo (TD-007 y TD-014).
"""

import os
from collections.abc import Iterator
from datetime import timedelta

import httpx
import polars as pl
import pytest
from deltalake.exceptions import DeltaError
from pydantic import SecretStr

import samples
from medallion_worker import catalogs, maintenance
from medallion_worker.batching import Batcher
from medallion_worker.catalogs import CatalogError, TrinoClient, UnityClient
from medallion_worker.config import Settings
from medallion_worker.sinks import DeltaSink
from medallion_worker.storage import Lake, storage_options
from medallion_worker.transform import Transformer, sync_catalogs

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        "LAKEHOUSE_INTEGRATION" not in os.environ,
        reason="requiere scripts/test_integration.sh",
    ),
]


@pytest.fixture(scope="module")
def settings() -> Settings:
    return Settings()


@pytest.fixture(scope="module")
def maintenance_settings(settings: Settings) -> Settings:
    return settings.model_copy(
        update={
            "s3_access_key_id": SecretStr(os.environ["MAINTENANCE_S3_ACCESS_KEY_ID"]),
            "s3_secret_access_key": SecretStr(os.environ["MAINTENANCE_S3_SECRET_ACCESS_KEY"]),
        }
    )


@pytest.fixture(scope="module")
def http() -> Iterator[httpx.Client]:
    with httpx.Client(timeout=120) as client:
        yield client


@pytest.fixture(scope="module")
def lake(settings: Settings) -> Lake:
    """Bronze con eventos de las once tablas, transformado y registrado."""
    batcher = Batcher(max_records=10**6, max_bytes=10**9, max_seconds=3600)
    for item in samples.demo_events():
        batcher.add(item)
    sink = DeltaSink(
        settings.bronze_uri, storage_options(settings), partitioned=settings.partitioned_tables
    )
    for batch in batcher.drain():
        sink.write(batch)
    result = Lake.from_settings(settings)
    summary = Transformer(result, "es_ES", samples.SALT).run_once()
    assert summary.broken == {}
    return result


@pytest.fixture(scope="module")
def synced(settings: Settings, lake: Lake, http: httpx.Client) -> Lake:
    assert sync_catalogs(settings, lake, http) > 0
    return lake


def trino(settings: Settings, http: httpx.Client, user: str) -> TrinoClient:
    return TrinoClient(http, settings.trino_url, user, settings.trino_catalog)


def test_both_catalogs_match(settings: Settings, synced: Lake, http: httpx.Client) -> None:
    problems = catalogs.compare(
        trino(settings, http, settings.trino_worker_user),
        UnityClient(http, settings.unity_catalog_url, settings.unity_catalog_name),
        catalogs.expected(synced),
    )
    assert problems == []
    assert sync_catalogs(settings, synced, http) == 0


def test_a_changed_definition_is_registered_again(
    settings: Settings, synced: Lake, http: httpx.Client
) -> None:
    """Otra descripción en el código: se retira y se vuelve a registrar en Unity Catalog."""
    entries = catalogs.expected(synced)
    unity = UnityClient(http, settings.unity_catalog_url, settings.unity_catalog_name)
    changed = [
        catalogs.Entry(e.schema, e.name, "Otra descripción", e.location, e.columns)
        if e.name == "dim_product"
        else e
        for e in entries
    ]
    assert catalogs.sync_unity(unity, synced, changed) == 1
    assert unity.tables("gold")["dim_product"]["comment"] == "Otra descripción"
    assert catalogs.sync_unity(unity, synced, entries) == 1


def test_text2sql_reads_gold_with_exact_amounts(
    settings: Settings, synced: Lake, http: httpx.Client
) -> None:
    client = trino(settings, http, "text2sql")
    [[orders, total]] = client.query(
        "SELECT count(*), sum(amount_total) FROM lakehouse.gold.fact_sales_orders"
    )
    assert (orders, total) == (2, "292.12")
    [[comment]] = [
        [row[3]]
        for row in client.query("SHOW COLUMNS FROM lakehouse.gold.fact_sales_orders")
        if row[0] == "amount_total"
    ]
    assert comment == "Importe total con impuestos"


def test_changes_are_visible_without_registering_again(
    settings: Settings, synced: Lake, http: httpx.Client
) -> None:
    client = trino(settings, http, "text2sql")
    query = "SELECT count(*) FROM lakehouse.gold.dim_customer"
    [[before]] = client.query(query)
    batcher = Batcher(max_records=10, max_bytes=10**9, max_seconds=3600)
    row = samples.payload("res_partner", 99, name="Cliente nuevo", active=True)
    batcher.add(samples.event("res_partner", 900, row, lsn=9000, op="c"))
    for batch in batcher.drain():
        DeltaSink(settings.bronze_uri, storage_options(settings)).write(batch)
    assert "dim_customer" in Transformer(synced, "es_ES", samples.SALT).run_once().models
    assert client.query(query) == [[before + 1]]


@pytest.mark.parametrize(
    "sql",
    [
        "SELECT * FROM lakehouse.silver.res_users",
        "SELECT * FROM system.runtime.queries",
        "SELECT * FROM system.metadata.catalogs",
        "DROP TABLE lakehouse.gold.dim_customer",
        "DELETE FROM lakehouse.gold.dim_customer",
        "INSERT INTO lakehouse.gold.dim_salesperson VALUES (9, 'x', true, false)",
        "CREATE TABLE lakehouse.gold.copia AS SELECT 1 AS a",
        "CREATE SCHEMA lakehouse.otro",
        "COMMENT ON TABLE lakehouse.gold.dim_customer IS 'x'",
        "CALL lakehouse.system.unregister_table('gold', 'dim_customer')",
        "CALL lakehouse.system.register_table('gold', 'x', 's3://bronze/odoo/res_users')",
        "CALL lakehouse.system.vacuum('gold', 'dim_customer', '7d')",
        "SELECT * FROM TABLE(lakehouse.system.table_changes('gold', 'dim_customer', 0))",
        "SET SESSION query_max_run_time = '9h'",
    ],
)
def test_text2sql_can_only_read_gold(
    settings: Settings, synced: Lake, http: httpx.Client, sql: str
) -> None:
    with pytest.raises(CatalogError, match=r"PERMISSION_DENIED|NOT_FOUND"):
        trino(settings, http, "text2sql").query(sql)


def test_unknown_users_cannot_query(settings: Settings, synced: Lake, http: httpx.Client) -> None:
    with pytest.raises(CatalogError, match="PERMISSION_DENIED"):
        trino(settings, http, "cualquiera").query("SELECT 1")


@pytest.mark.parametrize(
    "sql",
    [
        "DELETE FROM lakehouse.gold.dim_salesperson",
        "INSERT INTO lakehouse.gold.dim_salesperson VALUES (9, 'x', true, false)",
        "CREATE TABLE lakehouse.gold.copia WITH (location = 's3://gold/ventas/copia') "
        "AS SELECT 1 AS a",
        "CREATE TABLE lakehouse.silver.copia WITH (location = 's3://silver/ventas/copia') "
        "AS SELECT 1 AS a",
        "CREATE TABLE lakehouse.gold.copia WITH (location = 's3://bronze/odoo/copia') "
        "AS SELECT 1 AS a",
    ],
)
def test_trino_never_writes_data(
    settings: Settings, synced: Lake, http: httpx.Client, sql: str
) -> None:
    """Ni con el usuario que registra las tablas: en MinIO solo puede escribir su metastore."""
    with pytest.raises(CatalogError):
        trino(settings, http, settings.trino_worker_user).query(sql)


def test_bronze_maintenance_uses_its_own_user(
    settings: Settings, maintenance_settings: Settings, lake: Lake
) -> None:
    table = "it_maintenance"
    uri = lake.bronze(table)
    sink = DeltaSink(settings.bronze_uri, storage_options(settings), partitioned=[table])
    for number in range(12):
        batcher = Batcher(max_records=10, max_bytes=10**9, max_seconds=3600)
        batcher.add(samples.event(table, number, {"id": number}, lsn=number, key=number, op="c"))
        for batch in batcher.drain():
            sink.write(batch)
    before = lake.read(uri).sort("_offset")
    janitor = Lake.from_settings(maintenance_settings)
    result = maintenance.maintain(janitor, uri)
    assert (result.files_before, result.files_after) == (12, 1)
    assert lake.read(uri).sort("_offset").equals(before)
    # La ingesta sigue escribiendo después, con su usuario.
    batcher = Batcher(max_records=10, max_bytes=10**9, max_seconds=3600)
    batcher.add(samples.event(table, 12, {"id": 12}, lsn=12, key=12, op="c"))
    for batch in batcher.drain():
        sink.write(batch)
    assert lake.read(uri).height == 13


def test_the_ingestion_user_cannot_vacuum_bronze(settings: Settings, lake: Lake) -> None:
    table = "it_vacuum"
    uri = lake.bronze(table)
    for number in range(10):
        batcher = Batcher(max_records=10, max_bytes=10**9, max_seconds=3600)
        batcher.add(samples.event(table, number, {"id": number}, lsn=1, key=number))
        for batch in batcher.drain():
            DeltaSink(settings.bronze_uri, storage_options(settings)).write(batch)
    lake.open(uri).optimize.compact()
    with pytest.raises((DeltaError, OSError)):
        lake.open(uri).vacuum(retention_hours=0, dry_run=False, enforce_retention_duration=False)
    assert lake.read(uri).height == 10


def test_the_maintenance_user_only_reaches_bronze(
    maintenance_settings: Settings, lake: Lake
) -> None:
    janitor = Lake.from_settings(maintenance_settings)
    with pytest.raises((DeltaError, OSError)):
        janitor.read(janitor.gold("maestros", "dim_customer"))
    assert janitor.read(janitor.bronze("res_partner")).height > 0


def test_partitioning_on_object_storage(
    settings: Settings, maintenance_settings: Settings, lake: Lake
) -> None:
    table = "it_partition"
    for number in range(3):
        batcher = Batcher(max_records=10, max_bytes=10**9, max_seconds=3600)
        batcher.add(samples.event(table, number, {"id": number}, lsn=number, key=number))
        for batch in batcher.drain():
            DeltaSink(
                settings.bronze_uri,
                storage_options(settings),
                now=lambda n=number: samples.NOW + timedelta(days=n),  # type: ignore[misc]
            ).write(batch)
    before = lake.read(lake.bronze(table)).sort("_offset")
    janitor = Lake.from_settings(maintenance_settings)
    assert maintenance.partition(janitor, table) is True
    after = lake.read(lake.bronze(table)).sort("_offset")
    assert after.select(before.columns).equals(before)
    assert after["_ingested_date"].n_unique() == 3
    assert isinstance(after, pl.DataFrame)
