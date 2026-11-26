"""Eventos de Debezium de ejemplo para las tablas core, con los tipos que envía el conector."""

import json
from collections.abc import Iterable
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

from medallion_worker.batching import Batcher
from medallion_worker.contracts import BY_TABLE, CONTRACTS
from medallion_worker.events import BronzeEvent, parse_event
from medallion_worker.sinks import DeltaSink
from medallion_worker.storage import Lake

NOW = datetime(2026, 11, 25, 12, 0, tzinfo=UTC)
PREFIX = "odoo.public"


def days(year: int, month: int, day: int) -> int:
    return (date(year, month, day) - date(1970, 1, 1)).days


def micros(year: int, month: int, day: int, hour: int = 0, minute: int = 0) -> int:
    moment = datetime(year, month, day, hour, minute, tzinfo=UTC)
    return int((moment - datetime(1970, 1, 1, tzinfo=UTC)).total_seconds()) * 10**6


DEFAULTS: dict[str, Any] = {
    "integer": 1,
    "decimal": "1.50",
    "double": 10.0,
    "boolean": True,
    "text": "texto",
    "translated": '{"en_US": "Desk", "es_ES": "Escritorio"}',
    "date": days(2026, 11, 2),
    "timestamp": micros(2026, 11, 2, 10, 30),
}


def payload(table: str, identifier: int, **values: Any) -> dict[str, Any]:
    """Fila completa. Las columnas opcionales que no se indican van a nulo."""
    contract = BY_TABLE[table]
    row = {
        column.name: DEFAULTS[column.kind] if column.required else None
        for column in contract.columns
    }
    unknown = set(values) - set(row)
    assert not unknown, unknown
    # Una columna que no está en el contrato no molesta: Odoo tiene muchas más.
    return row | values | {"id": identifier, "columna_fuera_del_contrato": "x"}


def event(
    table: str,
    offset: int,
    after: dict[str, Any] | None,
    *,
    lsn: int | None,
    op: str = "r",
    key: int | None = None,
) -> BronzeEvent:
    identifier = key if key is not None else (after or {})["id"]
    envelope = {
        "op": op,
        "before": {"id": identifier} if op == "d" else None,
        "after": after,
        "source": {"lsn": lsn, "ts_ms": 1764072000000 + (lsn or 0)},
    }
    result = parse_event(
        f"{PREFIX}.{table}",
        0,
        offset,
        json.dumps({"id": identifier}).encode(),
        json.dumps(envelope).encode(),
    )
    assert result is not None
    return result


def local_lake(path: Path) -> Lake:
    return Lake(str(path / "bronze"), str(path / "silver"), str(path / "gold"))


def write_bronze(
    path: Path,
    events: Iterable[BronzeEvent],
    now: datetime = NOW,
    partitioned: Iterable[str] = (),
) -> None:
    batcher = Batcher(max_records=10**6, max_bytes=10**9, max_seconds=3600)
    for item in events:
        batcher.add(item)
    sink = DeltaSink(str(path / "bronze"), now=lambda: now, partitioned=list(partitioned))
    for batch in batcher.drain():
        sink.write(batch)


def demo_events() -> list[BronzeEvent]:
    """Un Odoo mínimo y coherente: los cinco dominios con relaciones entre ellos."""
    rows: list[tuple[str, dict[str, Any]]] = [
        (
            "res_partner",
            payload(
                "res_partner",
                1,
                name="Azure Interior",
                city="Fremont",
                is_company=True,
                customer_rank=2,
                active=True,
                create_date=micros(2026, 1, 1),
            ),
        ),
        (
            "res_partner",
            payload(
                "res_partner",
                2,
                name="Deco Addict",
                city="Pleasant Hill",
                is_company=True,
                customer_rank=1,
                active=True,
            ),
        ),
        (
            "res_partner",
            payload("res_partner", 3, name="Mitchell Admin", is_company=False, active=True),
        ),
        (
            "res_partner",
            payload(
                "res_partner", 4, name="Wood Corner", is_company=True, supplier_rank=1, active=False
            ),
        ),
        ("res_users", payload("res_users", 2, partner_id=3, active=True, share=False)),
        (
            "product_template",
            payload(
                "product_template",
                10,
                detailed_type="product",
                list_price="100.00",
                sale_ok=True,
                active=True,
                default_code="T10",
            ),
        ),
        (
            "product_template",
            payload(
                "product_template",
                11,
                detailed_type="service",
                list_price="30.25",
                sale_ok=True,
                active=True,
                name='{"en_US": "Consulting"}',
            ),
        ),
        (
            "product_product",
            payload("product_product", 20, product_tmpl_id=10, default_code="P20", active=True),
        ),
        ("product_product", payload("product_product", 21, product_tmpl_id=11, active=True)),
        (
            "sale_order",
            payload(
                "sale_order",
                100,
                name="S00100",
                state="sale",
                date_order=micros(2026, 10, 5, 23, 59),
                partner_id=1,
                user_id=2,
                currency_rate="1.0",
                amount_untaxed="200.10",
                amount_tax="42.02",
                amount_total="242.12",
                invoice_status="invoiced",
                delivery_status="full",
            ),
        ),
        (
            "sale_order",
            payload(
                "sale_order",
                101,
                name="S00101",
                state="draft",
                date_order=micros(2026, 11, 2, 8),
                partner_id=2,
                user_id=2,
                currency_rate="1.0",
                amount_untaxed="1000.00",
                amount_tax="0",
                amount_total="1000.00",
                invoice_status="no",
            ),
        ),
        (
            "sale_order",
            payload(
                "sale_order",
                102,
                name="S00102",
                state="sale",
                date_order=micros(2026, 11, 20, 8),
                partner_id=1,
                user_id=2,
                currency_rate="2.0",
                amount_untaxed="100.00",
                amount_tax="0.00",
                amount_total="100.00",
                invoice_status="to invoice",
            ),
        ),
        (
            "sale_order",
            payload(
                "sale_order",
                103,
                name="S00103",
                state="cancel",
                date_order=micros(2026, 11, 21, 8),
                partner_id=2,
                amount_total="5.00",
            ),
        ),
        (
            "sale_order_line",
            payload(
                "sale_order_line",
                200,
                order_id=100,
                product_id=20,
                product_uom_qty="2.0",
                qty_delivered="2.0",
                qty_invoiced="2.0",
                price_unit="100.05",
                discount="0.0",
                price_subtotal="200.10",
                price_total="242.12",
            ),
        ),
        (
            "sale_order_line",
            payload(
                "sale_order_line",
                201,
                order_id=100,
                display_type="line_note",
                product_uom_qty="0",
                price_unit="0",
            ),
        ),
        (
            "sale_order_line",
            payload(
                "sale_order_line",
                202,
                order_id=102,
                product_id=21,
                product_uom_qty="1.0",
                price_unit="100.00",
                price_subtotal="100.00",
                price_total="100.00",
            ),
        ),
        (
            "account_move",
            payload(
                "account_move",
                300,
                name="INV/300",
                move_type="out_invoice",
                state="posted",
                payment_state="not_paid",
                invoice_date=days(2026, 11, 1),
                invoice_date_due=days(2026, 12, 1),
                partner_id=1,
                invoice_user_id=2,
                amount_untaxed_signed="200.10",
                amount_tax_signed="42.02",
                amount_total_signed="242.12",
                amount_residual_signed="242.12",
            ),
        ),
        (
            "account_move",
            payload(
                "account_move",
                301,
                name="RINV/301",
                move_type="out_refund",
                state="posted",
                payment_state="paid",
                invoice_date=days(2026, 11, 3),
                partner_id=1,
                amount_untaxed_signed="-40.00",
                amount_tax_signed="-2.12",
                amount_total_signed="-42.12",
                amount_residual_signed="0",
            ),
        ),
        (
            "account_move",
            payload(
                "account_move",
                302,
                name="/",
                move_type="out_invoice",
                state="draft",
                partner_id=2,
                amount_total_signed="999",
            ),
        ),
        (
            "account_move",
            payload(
                "account_move",
                303,
                name="BILL/303",
                move_type="in_invoice",
                state="posted",
                payment_state="not_paid",
                invoice_date=days(2026, 10, 9),
                partner_id=4,
                amount_untaxed_signed="-541.10",
                amount_total_signed="-622.27",
                amount_residual_signed="-622.27",
            ),
        ),
        (
            "account_move",
            payload("account_move", 304, name="MISC/304", move_type="entry", state="posted"),
        ),
        (
            "account_move_line",
            payload(
                "account_move_line",
                400,
                move_id=300,
                display_type="product",
                product_id=20,
                quantity="2.0",
                price_unit="100.05",
                balance="-200.10",
            ),
        ),
        (
            "account_move_line",
            payload("account_move_line", 401, move_id=300, display_type="tax", balance="-42.02"),
        ),
        (
            "account_move_line",
            payload(
                "account_move_line",
                402,
                move_id=301,
                display_type="product",
                product_id=21,
                quantity="1.0",
                price_unit="40.00",
                balance="40.00",
            ),
        ),
        (
            "stock_quant",
            payload(
                "stock_quant",
                500,
                product_id=20,
                location_id=8,
                quantity="12.0",
                reserved_quantity="3.0",
                inventory_date=days(2026, 12, 31),
            ),
        ),
        (
            "stock_quant",
            payload(
                "stock_quant",
                501,
                product_id=20,
                location_id=14,
                quantity="-12.0",
                reserved_quantity="0.0",
            ),
        ),
        (
            "stock_move",
            payload(
                "stock_move",
                600,
                state="done",
                product_id=20,
                product_uom_qty="12.0",
                quantity="12.0",
                location_id=14,
                location_dest_id=8,
                is_inventory=True,
                reference="Inventory",
            ),
        ),
        (
            "stock_move",
            payload(
                "stock_move",
                601,
                state="assigned",
                product_id=20,
                product_uom_qty="3.0",
                quantity="3.0",
                location_id=8,
                location_dest_id=5,
                reference="WH/OUT/1",
            ),
        ),
        (
            "stock_move",
            payload(
                "stock_move",
                602,
                state="done",
                product_id=20,
                product_uom_qty="5.0",
                quantity="5.0",
                location_id=4,
                location_dest_id=8,
                reference="WH/IN/1",
            ),
        ),
        (
            "crm_lead",
            payload(
                "crm_lead",
                700,
                name="Oficina nueva",
                type="opportunity",
                active=True,
                probability=33.33,
                priority="2",
                partner_id=2,
                user_id=2,
                expected_revenue="4000.00",
                prorated_revenue="1333.20",
                date_deadline=days(2026, 12, 15),
            ),
        ),
        (
            "crm_lead",
            payload(
                "crm_lead",
                701,
                name="Ganada",
                type="opportunity",
                active=True,
                probability=100.0,
                priority="1",
                user_id=2,
                partner_name="Empresa X",
                expected_revenue="500.00",
                date_closed=micros(2026, 11, 10, 9),
            ),
        ),
        (
            "crm_lead",
            payload(
                "crm_lead",
                702,
                name="Perdida",
                type="opportunity",
                active=False,
                probability=0.0,
                priority="0",
                expected_revenue="7500.00",
            ),
        ),
        (
            "crm_lead",
            payload(
                "crm_lead",
                703,
                name="Contacto web",
                type="lead",
                active=True,
                probability=5.0,
                city="Madrid",
                partner_name="Empresa Y",
            ),
        ),
    ]
    offsets: dict[str, int] = {}
    events = []
    for table, row in rows:
        offsets[table] = offsets.get(table, -1) + 1
        events.append(event(table, offsets[table], row, lsn=1000))
    assert {table for table, _ in rows} == {contract.table for contract in CONTRACTS}
    return events
