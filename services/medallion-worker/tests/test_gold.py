"""Silver -> Gold: reglas de negocio de los cinco dominios."""

from datetime import date, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any

import polars as pl
import pytest

import samples
from medallion_worker import gold
from medallion_worker.contracts import BY_TABLE, CONTRACTS
from medallion_worker.gold_models import DOMAINS, MODELS, Model
from medallion_worker.storage import Lake
from medallion_worker.transform import Transformer
from samples import NOW, event, local_lake, payload, write_bronze

BY_NAME = {model.name: model for model in MODELS}


def build(path: Path) -> Lake:
    write_bronze(path, samples.demo_events())
    result = local_lake(path)
    summary = Transformer(result, "es_ES").run_once(NOW)
    assert summary.broken == {}
    assert sorted(summary.models) == sorted(BY_NAME)
    return result


@pytest.fixture(scope="module")
def lake(tmp_path_factory: pytest.TempPathFactory) -> Lake:
    """Solo lectura: lo comparten las pruebas que no cambian nada."""
    return build(tmp_path_factory.mktemp("lake"))


def table(lake: Lake, name: str) -> pl.DataFrame:
    return lake.read(lake.gold(BY_NAME[name].domain, name))


def by(lake: Lake, name: str, key: str) -> dict[Any, dict[str, Any]]:
    return {row[key]: row for row in table(lake, name).rows(named=True)}


def test_definitions_are_consistent() -> None:
    assert {model.domain for model in MODELS} == set(DOMAINS)
    for model in MODELS:
        assert model.name.startswith(("dim_", "fact_", "agg_"))
        assert all(description for _, _, description in model.columns), model.name
        assert set(model.sources) <= set(BY_TABLE), model.name
    assert {source for model in MODELS for source in model.sources} == {
        contract.table for contract in CONTRACTS
    }


@pytest.mark.parametrize("model", MODELS, ids=lambda model: model.name)
def test_every_model_matches_its_declared_schema(lake: Lake, model: Model) -> None:
    frame = table(lake, model.name)
    assert dict(frame.schema) == model.schema
    assert frame.height > 0
    # Los importes nunca son coma flotante.
    assert pl.Float64() not in model.schema.values()


def test_orders_and_quotations_are_separate(lake: Lake) -> None:
    orders = by(lake, "fact_sales_orders", "order_number")
    assert sorted(orders) == ["S00100", "S00102"]
    assert orders["S00100"]["order_date"] == date(2026, 10, 5)
    assert orders["S00100"]["customer_name"] == "Azure Interior"
    assert orders["S00100"]["salesperson_name"] == "Mitchell Admin"
    assert [orders[n]["is_pending_invoicing"] for n in ("S00100", "S00102")] == [False, True]
    assert orders["S00100"]["is_pending_delivery"] is False
    assert all(row["is_confirmed"] for row in orders.values())
    quotations = by(lake, "fact_quotations", "quotation_number")
    assert {number: row["quotation_status"] for number, row in quotations.items()} == {
        "S00101": "open",
        "S00103": "cancelled",
    }


def test_amounts_are_exact_and_in_company_currency(lake: Lake) -> None:
    orders = by(lake, "fact_sales_orders", "order_number")
    assert orders["S00100"]["amount_total"] == Decimal("242.12")
    # 100 en una moneda a 2,0 por unidad de la empresa.
    assert orders["S00102"]["amount_total"] == Decimal("50.00")
    lines = by(lake, "fact_sales_order_lines", "line_id")
    assert lines[200]["unit_price"] == Decimal("100.05")
    assert lines[202]["amount_untaxed"] == Decimal("50.00")


def test_notes_are_not_lines(lake: Lake) -> None:
    assert sorted(by(lake, "fact_sales_order_lines", "line_id")) == [200, 202]


def test_monthly_sales(lake: Lake) -> None:
    months = by(lake, "agg_sales_monthly", "month")
    assert {m: (r["orders"], r["amount_total"]) for m, r in months.items()} == {
        date(2026, 10, 1): (1, Decimal("242.12")),
        date(2026, 11, 1): (1, Decimal("50.00")),
    }


def test_invoices_credit_notes_and_bills_are_separate(lake: Lake) -> None:
    """Sin borradores, y cada tabla con lo suyo: ninguna necesita filtros para responder."""
    assert set(by(lake, "fact_customer_invoices", "invoice_number")) == {"INV/300"}
    notes = by(lake, "fact_customer_credit_notes", "credit_note_number")
    assert set(notes) == {"RINV/301"}
    assert notes["RINV/301"]["amount_total"] == Decimal("42.12")
    bills = by(lake, "fact_vendor_bills", "bill_number")
    assert set(bills) == {"BILL/303"}
    assert bills["BILL/303"]["amount_total"] == Decimal("622.27")
    assert bills["BILL/303"]["vendor_name"] == "Wood Corner"


def test_invoice_lines_skip_taxes_and_credit_notes(lake: Lake) -> None:
    lines = by(lake, "fact_customer_invoice_lines", "line_id")
    assert set(lines) == {400}
    assert lines[400]["amount_untaxed"] == Decimal("200.10")


def test_monthly_invoicing(lake: Lake) -> None:
    [row] = table(lake, "agg_invoicing_monthly").rows(named=True)
    assert (row["invoices"], row["credit_notes"]) == (1, 1)
    assert (row["invoiced_amount"], row["credited_amount"]) == (Decimal("242.12"), Decimal("42.12"))
    assert row["net_amount"] == Decimal("200.00")


def test_stock_only_counts_internal_locations(lake: Lake) -> None:
    stock = by(lake, "agg_stock_by_product", "product_id")
    assert set(stock) == {20}
    assert stock[20]["quantity_on_hand"] == Decimal("12.000")
    assert stock[20]["quantity_available"] == Decimal("9.000")
    assert stock[20]["stock_value"] == Decimal("1200.00")


def test_stock_moves_know_their_direction(lake: Lake) -> None:
    moves = by(lake, "fact_stock_moves", "move_id")
    assert {key: row["move_type"] for key, row in moves.items()} == {
        600: "adjustment",
        601: "outgoing",
        602: "incoming",
    }
    assert moves[601]["quantity_done"] == 0
    assert {key: row["is_pending"] for key, row in moves.items()} == {
        600: False,
        601: True,
        602: False,
    }


def test_products_use_their_variant_code_and_the_translated_name(lake: Lake) -> None:
    products = by(lake, "dim_product", "product_id")
    assert (products[20]["product_name"], products[20]["sku"]) == ("Escritorio", "P20")
    assert (products[21]["product_name"], products[21]["product_type"]) == ("Consulting", "service")


def test_opportunity_status(lake: Lake) -> None:
    opportunities = by(lake, "fact_opportunities", "opportunity_id")
    assert {key: row["status"] for key, row in opportunities.items()} == {
        700: "open",
        701: "won",
        702: "lost",
    }
    assert opportunities[700]["probability"] == Decimal("33.33")
    assert opportunities[700]["priority"] == "alta"
    assert opportunities[701]["customer_name"] == "Empresa X"
    assert list(by(lake, "fact_leads", "lead_id")) == [703]


def test_rows_deleted_in_odoo_disappear_from_gold(tmp_path: Path) -> None:
    lake = build(tmp_path)
    transformer = Transformer(lake, "es_ES")
    transformer.run_once(NOW)
    write_bronze(
        tmp_path,
        [event("sale_order", 50, None, lsn=5000, op="d", key=100)],
        now=NOW + timedelta(hours=1),
    )
    summary = transformer.run_once(NOW + timedelta(hours=1))
    assert "fact_sales_orders" in summary.models
    assert "fact_opportunities" not in summary.models
    assert "S00100" not in by(lake, "fact_sales_orders", "order_number")


def test_models_are_rebuilt_only_when_their_sources_change(lake: Lake) -> None:
    for model in MODELS:
        assert gold.refresh(lake, model).written is False


def test_a_new_revision_forces_a_rebuild(tmp_path: Path) -> None:
    lake = build(tmp_path)
    model = BY_NAME["dim_customer"]
    changed = Model(
        model.name, model.domain, model.description, model.columns, model.sources, model.build, 2
    )
    assert gold.refresh(lake, changed).written is True
    assert gold.refresh(lake, changed).written is False


def test_new_descriptions_recreate_the_table(tmp_path: Path) -> None:
    lake = build(tmp_path)
    model = BY_NAME["dim_salesperson"]
    changed = Model(
        model.name, model.domain, "Otra descripción", model.columns, model.sources, model.build
    )
    assert gold.refresh(lake, changed).written is True
    assert (
        lake.open(lake.gold(model.domain, model.name)).metadata().description == "Otra descripción"
    )
    assert table(lake, model.name).height == 1


def test_an_undeclared_column_is_an_error() -> None:
    model = BY_NAME["dim_salesperson"]
    frame = pl.DataFrame({"salesperson_id": [1], "inesperada": [2]})
    with pytest.raises(gold.ModelError, match="inesperada"):
        gold.conform(model, frame)


def test_a_broken_table_does_not_stop_the_others(tmp_path: Path) -> None:
    events = [item for item in samples.demo_events() if item.table != "crm_lead"]
    broken = payload("crm_lead", 1)
    del broken["type"]
    write_bronze(tmp_path, [*events, event("crm_lead", 0, broken, lsn=1)])
    summary = Transformer(local_lake(tmp_path), "es_ES").run_once(NOW)
    assert summary.broken == {"crm_lead": "faltan columnas: type"}
    assert "fact_sales_orders" in summary.models
    assert "fact_opportunities" in summary.models


def test_gold_waits_for_its_sources(tmp_path: Path) -> None:
    write_bronze(tmp_path, [event("res_partner", 0, payload("res_partner", 1), lsn=1)])
    lake = local_lake(tmp_path)
    assert gold.refresh(lake, BY_NAME["dim_customer"]).written is False
