"""Preguntas de ejemplo para la interfaz, por dominio."""

from pydantic import BaseModel


class ExampleGroup(BaseModel):
    domain: str
    label: str
    questions: list[str]


EXAMPLES = [
    ExampleGroup(
        domain="ventas",
        label="Ventas",
        questions=[
            "¿Cuáles son las ventas de los últimos 3 meses?",
            "Ventas confirmadas por comercial",
            "Top 5 productos más vendidos por importe",
        ],
    ),
    ExampleGroup(
        domain="facturacion",
        label="Facturación",
        questions=[
            "Top 10 clientes por facturación",
            "¿Cuánto tenemos pendiente de cobro?",
            "Facturación por mes",
        ],
    ),
    ExampleGroup(
        domain="inventario",
        label="Inventario",
        questions=[
            "Estado del inventario por producto",
            "¿Qué productos tienen más unidades reservadas?",
            "Movimientos de almacén por tipo",
        ],
    ),
    ExampleGroup(
        domain="crm",
        label="CRM",
        questions=[
            "Oportunidades abiertas en CRM",
            "Ingreso esperado por comercial en oportunidades abiertas",
            "Número de oportunidades por estado",
        ],
    ),
    ExampleGroup(
        domain="maestros",
        label="Clientes y productos",
        questions=[
            "¿Cuántos clientes activos tenemos por ciudad?",
            "Lista de productos con su precio de venta",
            "¿Cuántos comerciales hay?",
        ],
    ),
]
