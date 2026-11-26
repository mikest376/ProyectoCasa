"""El conjunto de evaluación y la comparación de resultados."""

from collections import Counter
from typing import Any

import pytest

from lakehouse_api.evals.__main__ import Outcome, summarize
from lakehouse_api.evals.compare import load_cases, normalize, percentile, same_result
from lakehouse_api.shots import SHOTS
from lakehouse_api.sql_guard import SqlGuard

GOLD = [
    "agg_invoicing_monthly", "agg_sales_monthly", "agg_stock_by_product", "dim_customer",
    "dim_product", "dim_salesperson", "fact_customer_credit_notes",
    "fact_customer_invoice_lines", "fact_customer_invoices", "fact_leads", "fact_opportunities",
    "fact_quotations", "fact_sales_order_lines", "fact_sales_orders", "fact_stock_moves",
    "fact_vendor_bills",
]  # fmt: skip


def test_questions_are_balanced_by_domain_and_split() -> None:
    cases = load_cases()
    assert len(cases) == 100
    domains = ("ventas", "facturacion", "inventario", "crm", "maestros")
    assert Counter((case.domain, case.split) for case in cases) == {
        **{(domain, "dev"): 15 for domain in domains},
        **{(domain, "test"): 5 for domain in domains},
    }
    assert len({case.identifier for case in cases}) == 100
    assert len({case.question.casefold() for case in cases}) == 100


def test_every_reference_passes_the_guard() -> None:
    guard = SqlGuard(dict.fromkeys(GOLD, ()), 1000)
    for case in load_cases():
        for sql in case.sql:
            assert guard.check(sql).tables, case.identifier


def test_prompt_examples_are_not_part_of_the_evaluation() -> None:
    questions = {case.question.casefold() for case in load_cases()}
    assert not questions & {shot.question.casefold() for shot in SHOTS}


@pytest.mark.parametrize(
    ("left", "right"),
    [
        ("1250.10", 1250.1),
        ("19", 19),
        ("33.333333", "33.33"),
        ("2026-11-01", "2026-11-01 00:00:00.000 UTC"),
        ("2026-11", "2026-11-01"),
        ("Azure Interior", " azure interior "),
        ("0.00", 0),
    ],
)
def test_equivalent_values(left: Any, right: Any) -> None:
    assert normalize(left) == normalize(right)


@pytest.mark.parametrize(
    ("expected", "actual", "ordered", "same"),
    [
        ([["a", 1], ["b", 2]], [["b", 2], ["a", 1]], False, True),
        ([["a", 1], ["b", 2]], [["b", 2], ["a", 1]], True, False),
        # Columnas en otro orden o de más.
        ([["a", 1]], [[1, "a"]], False, True),
        ([["a", 1]], [[7, "a", 1]], False, True),
        # Faltan columnas, filas o valores.
        ([["a", 1]], [["a"]], False, False),
        ([["a", 1]], [["a", 1], ["b", 2]], False, False),
        ([["a", 1], ["b", 2]], [["a", 2], ["b", 1]], False, False),
        ([[10]], [[11]], False, False),
        ([], [], False, True),
        ([[None]], [[0]], False, False),
        # Con empates vale cualquier orden.
        ([["a", 5], ["b", 5], ["c", 1]], [["b", 5], ["a", 5], ["c", 1]], True, True),
        ([["a", 5], ["b", 3]], [["b", 3], ["a", 5]], True, False),
    ],
)
def test_comparison(
    expected: list[list[Any]], actual: list[list[Any]], ordered: bool, same: bool
) -> None:
    assert same_result(expected, actual, ordered) is same


def test_summary() -> None:
    outcomes = [
        Outcome("V01", "ventas", "¿?", True, "", "SELECT", 1, 900, 1000),
        Outcome("V02", "ventas", "¿?", False, "timeout", None, 2, 0, 3000),
        Outcome("C01", "crm", "¿?", True, "", "SELECT", 2, 1800, 2000),
    ]
    report = summarize("ollama", "modelo", "dev", outcomes)
    assert (report["correct"], report["accuracy"]) == (2, 66.7)
    assert report["by_domain"] == {"crm": "1/1", "ventas": "1/2"}
    assert report["first_attempt"] == 1
    assert percentile([], 0.5) == 0
    assert percentile([1, 2, 3, 4, 100], 0.95) == 100
