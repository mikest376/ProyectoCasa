"""Bronze -> Silver: tipos, orden, duplicados, borrados, TOAST y contratos."""

from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any

import polars as pl
import pytest

import samples
from medallion_worker import silver
from medallion_worker.contracts import BY_TABLE, CONTRACTS, Contract
from medallion_worker.decoding import UNAVAILABLE, ContractError, Decoder
from medallion_worker.storage import Lake
from samples import NOW, days, event, local_lake, micros, payload, write_bronze

ORDERS = BY_TABLE["sale_order"]


def refresh(lake: Lake, contract: Contract = ORDERS) -> silver.SilverResult:
    return silver.refresh(lake, contract, "es_ES", NOW)


def rows(lake: Lake, contract: Contract = ORDERS) -> list[dict[str, Any]]:
    uri = lake.silver(contract.domain, contract.table)
    return lake.read(uri).sort("id").rows(named=True)


def order(identifier: int = 1, **values: Any) -> dict[str, Any]:
    return payload("sale_order", identifier, **values)


# --- Tipos --------------------------------------------------------------------------------------


def test_values_are_strictly_typed() -> None:
    row = Decoder(ORDERS, "es_ES").row(
        order(
            7,
            amount_total="1250.10",
            date_order=micros(2026, 11, 25, 9, 15),
            validity_date=days(2026, 12, 31),
            locked=False,
        )
    )
    assert row["amount_total"] == Decimal("1250.10")
    assert row["date_order"] == datetime(2026, 11, 25, 9, 15, tzinfo=UTC)
    assert row["validity_date"] == date(2026, 12, 31)
    assert row["locked"] is False
    assert row["user_id"] is None


@pytest.mark.parametrize(
    ("column", "value", "reason"),
    [
        ("partner_id", "7", "partner_id: no es integer"),
        ("partner_id", True, "partner_id: no es integer"),
        ("amount_total", 12.5, "amount_total: no es decimal"),
        ("amount_total", "NaN", "amount_total: no es decimal"),
        ("amount_total", "doce", "amount_total: no es decimal"),
        ("date_order", "2026-11-25", "date_order: no es timestamp"),
        ("locked", 1, "locked: no es boolean"),
        ("name", 5, "name: no es text"),
        ("name", None, "name: nulo no permitido"),
    ],
)
def test_contract_violations_are_rejected(column: str, value: Any, reason: str) -> None:
    with pytest.raises(ContractError, match=reason) as info:
        Decoder(ORDERS, "es_ES").row(order(1, **{column: value}))
    # El mensaje identifica la columna, nunca el dato.
    assert str(value) not in str(info.value) or value in (None, True, 1, 5, "7")


def test_a_missing_column_is_a_contract_violation() -> None:
    row = order()
    del row["amount_total"], row["state"]
    with pytest.raises(ContractError, match="faltan columnas: state, amount_total"):
        Decoder(ORDERS, "es_ES").row(row)


@pytest.mark.parametrize(
    ("language", "expected"), [("es_ES", "Escritorio"), ("en_US", "Desk"), ("fr_FR", "Desk")]
)
def test_translated_names_fall_back_to_english(language: str, expected: str) -> None:
    decoder = Decoder(BY_TABLE["product_template"], language)
    assert decoder.row(payload("product_template", 1))["name"] == expected


def test_every_contract_keeps_sensitive_columns_out() -> None:
    """TD-004: lo que el análisis no necesita no pasa de Bronze."""
    forbidden = {
        "login", "password", "email", "phone", "mobile", "street", "vat", "comment",
        "email_from", "contact_name", "access_token", "signup_token", "totp_secret",
    }  # fmt: skip
    for contract in CONTRACTS:
        assert not forbidden & set(contract.names), contract.table
        assert contract.columns[0].name == "id"
        assert len(set(contract.names)) == len(contract.names)


# --- Estado actual ------------------------------------------------------------------------------


def test_all_tables_load(tmp_path: Path) -> None:
    write_bronze(tmp_path, samples.demo_events())
    lake = local_lake(tmp_path)
    for contract in CONTRACTS:
        result = refresh(lake, contract)
        assert result.applied == result.read > 0, contract.table
        frame = lake.read(lake.silver(contract.domain, contract.table))
        assert dict(frame.schema) == silver.silver_schema(contract)


def test_the_latest_change_wins_by_lsn_not_by_arrival(tmp_path: Path) -> None:
    write_bronze(
        tmp_path,
        [
            event("sale_order", 0, order(state="draft"), lsn=100),
            event("sale_order", 1, order(state="sale"), lsn=300, op="u"),
            event("sale_order", 2, order(state="sent"), lsn=200, op="u"),
        ],
    )
    lake = local_lake(tmp_path)
    refresh(lake)
    [row] = rows(lake)
    assert (row["state"], row["_op"], row["_source_lsn"]) == ("sale", "u", 300)


def test_old_events_do_not_undo_newer_ones(tmp_path: Path) -> None:
    lake = local_lake(tmp_path)
    write_bronze(tmp_path, [event("sale_order", 0, order(state="sale"), lsn=300)])
    refresh(lake)
    write_bronze(tmp_path, [event("sale_order", 1, order(state="draft"), lsn=200, op="u")])
    assert refresh(lake).applied == 0
    assert rows(lake)[0]["state"] == "sale"


def test_repeated_events_are_applied_once(tmp_path: Path) -> None:
    """Bronze entrega al menos una vez: el mismo evento puede estar dos veces."""
    lake = local_lake(tmp_path)
    repeated = event("sale_order", 0, order(), lsn=100)
    write_bronze(tmp_path, [repeated])
    write_bronze(tmp_path, [repeated], now=NOW + timedelta(seconds=30))
    assert refresh(lake).applied == 1
    assert len(rows(lake)) == 1


def test_same_coordinates_with_another_lsn_are_different_events(tmp_path: Path) -> None:
    """TD-010: si se recrea el clúster de Kafka, los offsets vuelven a empezar."""
    lake = local_lake(tmp_path)
    write_bronze(tmp_path, [event("sale_order", 0, order(1, state="draft"), lsn=100)])
    refresh(lake)
    write_bronze(
        tmp_path,
        [event("sale_order", 0, order(2, state="sale"), lsn=900)],
        now=NOW + timedelta(hours=1),
    )
    assert refresh(lake).applied == 1
    assert [row["id"] for row in rows(lake)] == [1, 2]


def test_deletes_mark_the_row_and_keep_its_values(tmp_path: Path) -> None:
    lake = local_lake(tmp_path)
    write_bronze(
        tmp_path,
        [
            event("sale_order", 0, order(amount_total="99.00"), lsn=100),
            event("sale_order", 1, None, lsn=200, op="d", key=1),
        ],
    )
    refresh(lake)
    [row] = rows(lake)
    assert row["_is_deleted"] is True
    assert row["amount_total"] == Decimal("99.00")


def test_a_recreated_row_is_no_longer_deleted(tmp_path: Path) -> None:
    lake = local_lake(tmp_path)
    write_bronze(
        tmp_path,
        [
            event("sale_order", 0, order(), lsn=100),
            event("sale_order", 1, None, lsn=200, op="d", key=1),
            event("sale_order", 2, order(state="sent"), lsn=300, op="c"),
        ],
    )
    refresh(lake)
    [row] = rows(lake)
    assert (row["_is_deleted"], row["state"]) == (False, "sent")


def test_toast_values_survive_in_the_same_and_in_later_runs(tmp_path: Path) -> None:
    lake = local_lake(tmp_path)
    write_bronze(
        tmp_path,
        [
            event("sale_order", 0, order(invoice_status="to invoice"), lsn=100),
            event(
                "sale_order", 1, order(invoice_status=UNAVAILABLE, state="sale"), lsn=200, op="u"
            ),
        ],
    )
    refresh(lake)
    assert rows(lake)[0]["invoice_status"] == "to invoice"
    write_bronze(
        tmp_path,
        [event("sale_order", 2, order(invoice_status=UNAVAILABLE, state="sent"), lsn=300, op="u")],
        now=NOW + timedelta(hours=1),
    )
    refresh(lake)
    [row] = rows(lake)
    assert (row["state"], row["invoice_status"]) == ("sent", "to invoice")
    assert UNAVAILABLE not in lake.read(lake.silver("ventas", "sale_order")).write_json()


def test_nothing_new_means_no_commit(tmp_path: Path) -> None:
    lake = local_lake(tmp_path)
    write_bronze(tmp_path, samples.demo_events())
    refresh(lake)
    uri = lake.silver("ventas", "sale_order")
    version = lake.version(uri)
    assert refresh(lake).applied == 0
    assert lake.version(uri) == version


def test_the_watermark_limits_what_is_read_again(tmp_path: Path) -> None:
    lake = local_lake(tmp_path)
    for day in range(2):
        write_bronze(
            tmp_path,
            [event("sale_order", day, order(day + 1), lsn=100 + day)],
            now=NOW + timedelta(days=day),
        )
    assert refresh(lake).applied == 2
    write_bronze(tmp_path, [event("sale_order", 2, order(3), lsn=300)], now=NOW + timedelta(days=2))
    result = refresh(lake)
    # Relee el último lote, por el margen del reloj, pero no el anterior.
    assert (result.read, result.applied) == (2, 1)


@pytest.mark.parametrize("partitioned", [[], ["sale_order"]])
def test_partitioned_bronze_gives_the_same_result(tmp_path: Path, partitioned: list[str]) -> None:
    lake = local_lake(tmp_path)
    for day in range(3):
        write_bronze(
            tmp_path,
            [event("sale_order", day, order(day + 1), lsn=100 + day)],
            now=NOW + timedelta(days=day),
            partitioned=partitioned,
        )
        refresh(lake)
    assert [row["id"] for row in rows(lake)] == [1, 2, 3]


def test_a_broken_contract_leaves_silver_as_it_was(tmp_path: Path) -> None:
    lake = local_lake(tmp_path)
    write_bronze(tmp_path, [event("sale_order", 0, order(1), lsn=100)])
    refresh(lake)
    broken = order(2)
    del broken["amount_total"]
    write_bronze(tmp_path, [event("sale_order", 1, broken, lsn=200)], now=NOW + timedelta(hours=1))
    with pytest.raises(ContractError, match="amount_total"):
        refresh(lake)
    assert [row["id"] for row in rows(lake)] == [1]


def test_events_without_lsn_are_not_guessed(tmp_path: Path) -> None:
    lake = local_lake(tmp_path)
    write_bronze(tmp_path, [event("sale_order", 0, order(1), lsn=None)])
    with pytest.raises(ContractError, match="sin source_lsn"):
        refresh(lake)


def test_missing_bronze_is_not_an_error(tmp_path: Path) -> None:
    result = refresh(local_lake(tmp_path))
    assert (result.read, result.applied) == (0, 0)


def test_columns_carry_their_description(tmp_path: Path) -> None:
    lake = local_lake(tmp_path)
    write_bronze(tmp_path, samples.demo_events())
    refresh(lake)
    table = lake.open(lake.silver("ventas", "sale_order"))
    assert table.metadata().description == ORDERS.description
    comments = {field.name: field.metadata.get("comment") for field in table.schema().fields}
    assert comments["amount_total"] == "Total con impuestos"
    assert all(comments.values())
    assert pl.Series(list(comments)).is_unique().all()
