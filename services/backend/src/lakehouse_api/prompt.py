"""Prompt de Text-to-SQL: solo metadatos de Gold y la pregunta (CLAUDE.md, regla 10).

El mensaje de sistema es siempre el mismo, así que el proveedor puede reutilizarlo entre preguntas.
Lo que depende de la pregunta (ejemplos parecidos y tablas probables) va al final.
"""

from collections.abc import Sequence
from typing import Any

from lakehouse_api.gold_catalog import Table
from lakehouse_api.llm import ChatMessage
from lakehouse_api.retrieval import likely_tables, rank_examples
from lakehouse_api.shots import SHOTS

EXAMPLES = 3
MAX_OWNERS = 3

# El orden importa: el modelo razona en una frase antes de escribir el SQL.
OUTPUT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "explanation": {"type": "string"},
        "sql": {"type": "string"},
        "chart_type": {"type": "string", "enum": ["kpi", "bar", "line", "area", "table"]},
        "x_axis": {"type": ["string", "null"]},
        "y_axis": {"type": ["string", "null"]},
        "title": {"type": "string"},
    },
    "required": ["explanation", "sql", "chart_type", "x_axis", "y_axis", "title"],
}

RULES = """\
Traduces preguntas en español a una consulta SQL de Trino sobre el esquema gold de una empresa.

Reglas:
1. Usa una sola tabla de las listadas y solo sus columnas. Nunca JOIN, UNION ni EXCEPT: las tablas \
fact_ ya llevan los nombres de cliente, producto y comercial.
2. Una sola sentencia SELECT, sin punto y coma ni comentarios.
3. Responde exactamente a lo que se pide: sin filtros, agrupaciones ni columnas de más. \
«¿Cuántos…?» es count(*) y «¿Cuánto…?» es sum: un solo número, sin GROUP BY. Agrupa solo si se \
pide «por…», «cada…» o una evolución.
4. Ventas o pedidos: fact_sales_orders. Ventas por producto: fact_sales_order_lines, con \
sum(quantity) para las unidades. Presupuestos: fact_quotations. Ventas por mes: agg_sales_monthly.
5. Facturas o facturación: fact_customer_invoices. Abonos: fact_customer_credit_notes. Pendiente \
de cobro o de pago: suma amount_due. Vencida: due_date < current_date AND amount_due > 0. Compras \
o facturas de proveedor: fact_vendor_bills. Facturación por mes: agg_invoicing_monthly. \
Facturación neta: sum(net_amount) de esa misma tabla.
6. Stock, existencias o inventario: agg_stock_by_product. Movimientos, entradas, salidas o \
ajustes: fact_stock_moves, filtrando move_type; pendientes, is_pending = true; realizados, \
move_status = 'done'. Precios y catálogo: dim_product.
7. Contactos: todos los de dim_customer. Clientes: is_customer = true. Solo si se pide «activos»: \
is_active = true.
8. Fechas: current_date es hoy. Últimos 3 meses: columna >= date_add('month', -3, current_date). \
Este mes: date_trunc('month', columna) = date_trunc('month', current_date). \
Este año: year(columna) = year(current_date).
9. Textos: lower(columna) LIKE '%texto%'. Con GROUP BY, repite la expresión, no el alias. Da un \
alias a cada agregado. Top N: ORDER BY el agregado DESC LIMIT N.
10. chart_type: kpi (un solo número), bar (comparar categorías), line o area (evolución por \
fecha), table (listados). x_axis e y_axis son columnas o alias del SELECT, o null.
11. explanation y title, en español y breves.
"""


def _short_type(kind: str) -> str:
    return kind.partition("(")[0]


def render_schema(tables: Sequence[Table]) -> str:
    """Sin identificadores: los modelos están desnormalizados y solo distraen."""
    lines = []
    for table in tables:
        lines.append(f"\ngold.{table.name}: {table.description}")
        lines += [
            f"  {column.name} {_short_type(column.type)}: {column.description}"
            for column in table.columns
            if not column.name.endswith("_id")
        ]
    return "\n".join(lines)


def _columns(table: Table) -> str:
    names = [column.name for column in table.columns if not column.name.endswith("_id")]
    return f"gold.{table.name}({', '.join(names)})"


def build_messages(tables: Sequence[Table], question: str) -> list[ChatMessage]:
    messages = [ChatMessage("system", f"{RULES}\nTablas:{render_schema(tables)}")]
    for shot in rank_examples(SHOTS, question, EXAMPLES):
        messages.append(ChatMessage("user", shot.question))
        messages.append(ChatMessage("assistant", shot.answer()))
    likely = likely_tables(tables, question)
    hint = "\nTabla probable: " + " o ".join(map(_columns, likely)) if likely else ""
    messages.append(ChatMessage("user", f"{question}{hint}"))
    return messages


def repair_messages(
    tables: Sequence[Table], question: str, sql: str | None, reason: str
) -> list[ChatMessage]:
    """Segundo intento. El motivo describe el SQL, nunca los datos.

    No se repite la conversación, porque el modelo tiende a copiar su propia respuesta. Si falta
    una columna, se indica en qué tablas existe: casi siempre ha elegido la tabla equivocada.
    """
    missing = missing_column(reason)
    owners = [t for t in tables if missing and any(c.name == missing for c in t.columns)]
    known = owners or likely_tables(tables, question)
    messages = build_messages(tables, question)
    previous = f"\nEste SQL no sirve: {sql[:600]}" if sql else ""
    advice = f"\nLa columna {missing} está en otra tabla: cámbiala." if owners else ""
    correction = (
        f"{question}{previous}\nMotivo: {reason}{advice}\n"
        f"Usa una de estas tablas: {' o '.join(map(_columns, known[:MAX_OWNERS]))}"
    )
    return [*messages[:-1], ChatMessage("user", correction)]


def missing_column(reason: str) -> str | None:
    """Nombre de la columna en el mensaje de Trino: Column 'x' cannot be resolved."""
    _, found, rest = reason.partition("Column '")
    name, closed, _ = rest.partition("'")
    return name.rpartition(".")[2] if found and closed else None
