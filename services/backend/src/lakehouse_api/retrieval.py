"""Parecido léxico entre la pregunta, las tablas y los ejemplos. Sin modelos ni servicios.

Un modelo de 3B acierta más si se le recuerda, junto a la pregunta, la tabla probable y ejemplos
parecidos. Todas las tablas siguen en el prompt: esto solo orienta, no excluye.
"""

import unicodedata
from collections import Counter
from collections.abc import Iterable, Sequence
from math import log
from typing import Protocol, TypeVar

from lakehouse_api.gold_catalog import Table

STEM = 5
# Palabras sin significado para elegir tabla.
STOPWORDS = frozenset(
    (  # noqa: SIM905 (una lista de palabras se lee mejor así)
        "ante aquel bajo cada como con cual cuales cuando cuanta cuantas cuanto cuantos dame datos "
        "del desde dime donde durante entre esta estan este estos han hasta hay hemos las list "
        "lista los mostrar muestra nuestra nuestro nuestros numero para pero por porque que quien "
        "quiero segun sido sobre solo son tanto tenemos tiene tienen todo todos total una unos"
    ).split()
)

# Cómo se habla de cada tabla. El orden decide los empates: si solo se nombra un producto, un
# cliente o un comercial, sin vender ni facturar, la pregunta es sobre su dimensión.
VOCABULARY = {
    "dim_product": "producto catalogo precio servicio tarifa",
    "dim_customer": "contacto cliente empresa ciudad proveedor archivado",
    "dim_salesperson": "comercial vendedor usuario equipo",
    "fact_sales_orders": "venta vender vendido pedido comercial ingreso compra pendiente entregar",
    "fact_quotations": "presupuesto oferta caduca",
    "fact_sales_order_lines": "venta vendido producto articulo unidades linea descuento",
    "fact_customer_invoices": "factura cobro cobrar pendiente vencida cliente",
    "fact_customer_credit_notes": "abono abonado rectificativa devolucion cliente",
    "fact_customer_invoice_lines": "factura producto linea",
    "fact_vendor_bills": "proveedor compra pagar pago debemos deuda gasto",
    "agg_stock_by_product": "stock inventario existencias disponible reservada unidades producto "
    "valor almacen",
    "fact_stock_moves": "movimiento almacen entrada salida ajuste albaran recibida",
    "fact_opportunities": "oportunidad crm ganada perdida abierta probabilidad prioridad esperado "
    "cierre embudo ponderado",
    "fact_leads": "iniciativa lead crm prospecto",
    "agg_sales_monthly": "venta mensual evolucion tendencia",
    "agg_invoicing_monthly": "factura mensual evolucion tendencia neta neto",
}
# Los agregados mensuales solo encajan si se pide una evolución o el neto.
MONTHLY = ("por mes", "cada mes", "mensual", "evolucion", "tendencia", "mes a mes", "neta", "neto")
CLOSE = 0.85


class HasQuestion(Protocol):
    @property
    def question(self) -> str: ...


T = TypeVar("T", bound=HasQuestion)


def plain(text: str) -> str:
    """Minúsculas y sin tildes."""
    decomposed = unicodedata.normalize("NFKD", text.casefold())
    return "".join(c for c in decomposed if not unicodedata.combining(c))


def _singular(word: str) -> str:
    if word.endswith("es") and len(word) > 4:
        return word[:-2]
    return word[:-1] if word.endswith("s") and len(word) > 3 else word


def stems(text: str) -> list[str]:
    letters = "".join(c if c.isalnum() else " " for c in plain(text))
    words = (word for word in letters.split() if len(word) > 2 and word not in STOPWORDS)
    return [_singular(word)[:STEM] for word in words]


def _weights(documents: Iterable[set[str]]) -> dict[str, float]:
    """Lo raro pesa más: «reservadas» orienta mejor que «producto»."""
    sets = list(documents)
    counts = Counter(stem for document in sets for stem in document)
    return {stem: log(1 + len(sets) / count) for stem, count in counts.items()}


def likely_tables(tables: Sequence[Table], question: str) -> list[Table]:
    """La tabla más probable y, si está muy cerca, la segunda."""
    documents = {name: set(stems(text)) for name, text in VOCABULARY.items()}
    weights = _weights(documents.values())
    words = set(stems(question))
    monthly = any(phrase in plain(question) for phrase in MONTHLY)

    scores: dict[str, float] = {}
    for table in tables:
        if table.name.endswith("_monthly") and not monthly:
            continue
        known = words & documents.get(table.name, set())
        columns = words & set(stems(" ".join(column.description for column in table.columns)))
        scores[table.name] = sum(weights[word] for word in known) + 0.2 * len(columns - known)
        if table.name.endswith("_monthly"):
            scores[table.name] += 0.5
    order = list(VOCABULARY)
    ranked = sorted(
        (table for table in tables if scores.get(table.name, 0) > 0),
        key=lambda table: (
            -scores[table.name],
            order.index(table.name) if table.name in order else len(order),
        ),
    )
    close = len(ranked) > 1 and scores[ranked[1].name] >= CLOSE * scores[ranked[0].name]
    # Si gana una dimensión, la pregunta solo nombra la entidad: ofrecer otra tabla confunde.
    return ranked[:2] if close and not ranked[0].name.startswith("dim_") else ranked[:1]


def rank_examples(examples: Sequence[T], question: str, limit: int) -> list[T]:
    documents = [set(stems(example.question)) for example in examples]
    weights = _weights(documents)
    words = set(stems(question))

    def score(index: int) -> float:
        shared = sum(weights[word] for word in words & documents[index])
        return shared / (1 + 0.1 * len(documents[index]))

    best = sorted(range(len(examples)), key=lambda index: (-score(index), index))[:limit]
    # El más parecido, el último: queda junto a la pregunta.
    return [examples[index] for index in reversed(best)]
