"""Limpieza única de datos personales (TD-004): Bronze sin las columnas, Silver con seudónimos."""

import json
from datetime import timedelta
from pathlib import Path

import polars as pl
import pytest
from deltalake import write_deltalake
from pydantic import SecretStr

import samples
from medallion_worker import redaction, silver
from medallion_worker.config import Settings
from medallion_worker.contracts import BY_TABLE
from medallion_worker.redaction import Exclusions, redact_text
from medallion_worker.storage import Lake
from samples import NOW, SALT, event, local_lake, payload, write_bronze

PATTERNS = [
    "# Datos personales",
    "",
    r"public\.[^.]+\.([a-z0-9_]*_)?(email|phone)",
    r"public\.res_partner\.name",
]


def partner(number: int) -> dict[str, object]:
    return payload("res_partner", number, name=f"Persona {number}") | {
        "email": f"persona{number}@example.com",
        "phone": f"+34 600 000 00{number}",
    }


def fill(path: Path) -> Lake:
    for number in range(1, 4):
        write_bronze(
            path,
            [event("res_partner", number, partner(number), lsn=100 + number)],
            now=NOW + timedelta(hours=number),
        )
    return local_lake(path)


def test_only_excluded_columns_leave_the_event() -> None:
    exclusions = Exclusions(PATTERNS)
    text = '{"id":1,"name":"Azure","email":"a@example.com","city":"Fremont"}'
    assert json.loads(redact_text(text, "res_partner", exclusions) or "") == {
        "id": 1,
        "city": "Fremont",
    }
    # El nombre solo es personal en res_partner; el correo, en cualquier tabla.
    assert json.loads(redact_text(text, "sale_order", exclusions) or "") == {
        "id": 1,
        "name": "Azure",
        "city": "Fremont",
    }
    unchanged = '{"id":1,"city":"Fremont"}'
    assert redact_text(unchanged, "res_partner", exclusions) is unchanged
    assert redact_text(None, "res_partner", exclusions) is None
    assert not Exclusions(["# solo comentarios", ""])


def test_bronze_is_rewritten_once_without_personal_columns(tmp_path: Path) -> None:
    lake = fill(tmp_path)
    uri = lake.bronze("res_partner")
    values = ["persona1@example.com", "Persona 2", "+34 600 000 003"]
    before = lake.read(uri).sort("_offset")
    # Control: antes de limpiar, los datos y las estadísticas del registro los contienen.
    assert any(
        "filas con datos personales en after" in p for p in redaction.scan(lake, [uri], values)
    )

    result = redaction.redact_bronze(lake, "res_partner", Exclusions(PATTERNS))
    assert (result.rows, result.changed) == (3, 3)
    assert result.removed_files == 3
    after = lake.read(uri).sort("_offset")
    assert after.drop("before", "after").equals(before.drop("before", "after"))
    for text in after["after"].to_list():
        assert set(json.loads(text)) & {"name", "email", "phone"} == set()
        assert json.loads(text)["city"] is None
    assert lake.open(uri).metadata().configuration["delta.appendOnly"] == "true"
    assert redaction.scan(lake, [uri], values) == []
    assert redaction.redact_bronze(lake, "res_partner", Exclusions(PATTERNS)).changed == 0


def test_replaced_files_with_personal_data_are_found(tmp_path: Path) -> None:
    """Control del escaneo: reescribir sin borrar deja los datos en el fichero sustituido."""
    lake = fill(tmp_path)
    uri = lake.bronze("res_partner")
    lake.open(uri).alter.set_table_properties({"delta.appendOnly": "false"})
    clean = lake.read(uri).with_columns(pl.lit(None, dtype=pl.String).alias("after"))
    write_deltalake(uri, clean, mode="overwrite")
    problems = redaction.scan(lake, [uri], ["persona1@example.com"])
    assert any("fichero sustituido aún contiene datos personales" in p for p in problems)
    redaction.purge(lake, uri)
    assert redaction.scan(lake, [uri], ["persona1@example.com"]) == []


def test_ingestion_continues_after_the_rewrite(tmp_path: Path) -> None:
    lake = fill(tmp_path)
    redaction.redact_bronze(lake, "res_partner", Exclusions(PATTERNS))
    write_bronze(tmp_path, [event("res_partner", 9, payload("res_partner", 9), lsn=900)])
    assert lake.read(lake.bronze("res_partner")).height == 4


def test_silver_gets_its_pseudonyms_in_place_and_keeps_the_watermark(tmp_path: Path) -> None:
    write_bronze(tmp_path, samples.demo_events())
    lake = local_lake(tmp_path)
    contract = BY_TABLE["res_partner"]
    silver.refresh(lake, contract, "es_ES", NOW, SALT)
    uri = lake.silver(contract.domain, contract.table)
    mark = lake.open(uri).transaction_version(silver.WATERMARK)
    # Una tabla de antes de TD-004, con los nombres en claro.
    old = lake.read(uri).with_columns(pl.lit("Azure Interior").alias("name"))
    write_deltalake(uri, old, mode="overwrite")
    assert redaction.scan(lake, [uri], ["Azure Interior"])

    result = redaction.pseudonymize_silver(lake, contract, SALT)
    assert result is not None
    assert result.rows == old.height
    assert all(name.startswith("Contacto_") for name in lake.read(uri)["name"].to_list())
    assert lake.open(uri).transaction_version(silver.WATERMARK) == mark
    assert redaction.scan(lake, [uri], ["Azure Interior"]) == []
    assert redaction.pseudonymize_silver(lake, BY_TABLE["sale_order"], SALT) is None


def test_tables_are_found_from_their_logs(tmp_path: Path) -> None:
    write_bronze(tmp_path, samples.demo_events())
    lake = local_lake(tmp_path)
    silver.refresh(lake, BY_TABLE["res_partner"], "es_ES", NOW, SALT)
    found = redaction.tables(lake)
    assert lake.bronze("res_partner") in found
    assert lake.silver("maestros", "res_partner") in found
    assert redaction.bronze_tables(lake, found) == sorted(
        {item.table for item in samples.demo_events()}
    )


def test_the_transform_does_not_run_without_salt() -> None:
    without = Settings(s3_access_key_id=SecretStr("x"), s3_secret_access_key=SecretStr("y"))
    with pytest.raises(ValueError, match="PII_HASH_SALT"):
        _ = without.pii_salt
    salted = Settings(
        s3_access_key_id=SecretStr("x"),
        s3_secret_access_key=SecretStr("y"),
        pii_hash_salt=SecretStr("sal"),
    )
    assert salted.pii_salt == b"sal"
