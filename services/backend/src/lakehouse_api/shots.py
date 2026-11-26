"""Ejemplos resueltos: se eligen los más parecidos a cada pregunta.

Ninguno forma parte del conjunto de evaluación (hay una prueba que lo comprueba).
"""

import json
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class Shot:
    question: str
    explanation: str
    sql: str
    chart_type: str
    x_axis: str | None
    y_axis: str | None
    title: str

    def answer(self) -> str:
        return json.dumps(
            {
                "explanation": self.explanation,
                "sql": self.sql,
                "chart_type": self.chart_type,
                "x_axis": self.x_axis,
                "y_axis": self.y_axis,
                "title": self.title,
            },
            ensure_ascii=False,
        )


SHOTS = (
    Shot(
        "¿Cuánto hemos vendido a cada ciudad?",
        "Suma de los pedidos confirmados por ciudad del cliente.",
        "SELECT city, sum(amount_total) AS total_sales FROM gold.fact_sales_orders "
        "GROUP BY city ORDER BY total_sales DESC",
        "bar",
        "city",
        "total_sales",
        "Ventas por ciudad",
    ),
    Shot(
        "Ventas de este año a Azure Interior",
        "Suma de los pedidos confirmados de ese cliente este año.",
        "SELECT sum(amount_total) AS total_sales FROM gold.fact_sales_orders "
        "WHERE lower(customer_name) LIKE '%azure interior%' "
        "AND year(order_date) = year(current_date)",
        "kpi",
        None,
        "total_sales",
        "Ventas a Azure Interior este año",
    ),
    Shot(
        "Ventas de los últimos 30 días",
        "Suma de los pedidos de los últimos 30 días.",
        "SELECT sum(amount_total) AS total_sales FROM gold.fact_sales_orders "
        "WHERE order_date >= date_add('day', -30, current_date)",
        "kpi",
        None,
        "total_sales",
        "Ventas de los últimos 30 días",
    ),
    Shot(
        "¿Cuántos pedidos ha hecho cada cliente este mes?",
        "Cuenta de pedidos confirmados del mes en curso por cliente.",
        "SELECT customer_name, count(*) AS orders FROM gold.fact_sales_orders "
        "WHERE "
        "date_trunc('month', order_date) = date_trunc('month', current_date) "
        "GROUP BY customer_name ORDER BY orders DESC",
        "bar",
        "customer_name",
        "orders",
        "Pedidos por cliente este mes",
    ),
    Shot(
        "Los 3 clientes que más compran",
        "Suma de los pedidos confirmados por cliente; los tres mayores.",
        "SELECT customer_name, sum(amount_total) AS total_sales FROM gold.fact_sales_orders "
        "GROUP BY customer_name ORDER BY total_sales DESC LIMIT 3",
        "bar",
        "customer_name",
        "total_sales",
        "Clientes que más compran",
    ),
    Shot(
        "Presupuestos que caducan este mes",
        "Presupuestos abiertos con validez en el mes en curso.",
        "SELECT quotation_number, valid_until, customer_name, amount_total "
        "FROM gold.fact_quotations WHERE quotation_status = 'open' "
        "AND date_trunc('month', valid_until) = date_trunc('month', current_date)",
        "table",
        None,
        None,
        "Presupuestos que caducan este mes",
    ),
    Shot(
        "¿Qué pedidos están pendientes de entregar?",
        "Pedidos con la entrega pendiente.",
        "SELECT order_number, order_date, customer_name, amount_total "
        "FROM gold.fact_sales_orders WHERE is_pending_delivery = true",
        "table",
        None,
        None,
        "Pedidos pendientes de entregar",
    ),
    Shot(
        "¿Qué descuento medio aplicamos por producto?",
        "Media del descuento de las líneas confirmadas por producto.",
        "SELECT product_name, avg(discount_percent) AS average_discount "
        "FROM gold.fact_sales_order_lines "
        "GROUP BY product_name ORDER BY average_discount DESC",
        "bar",
        "product_name",
        "average_discount",
        "Descuento medio por producto",
    ),
    Shot(
        "Productos que más ha comprado Deco Addict",
        "Unidades de las líneas confirmadas de ese cliente por producto.",
        "SELECT product_name, sum(quantity) AS units FROM gold.fact_sales_order_lines "
        "WHERE lower(customer_name) LIKE '%deco addict%' "
        "GROUP BY product_name ORDER BY units DESC LIMIT 10",
        "bar",
        "product_name",
        "units",
        "Productos comprados por Deco Addict",
    ),
    Shot(
        "¿Cuántos clientes distintos compraron cada mes?",
        "Clientes distintos por mes, ya agregados.",
        "SELECT month, customers FROM gold.agg_sales_monthly ORDER BY month",
        "line",
        "month",
        "customers",
        "Clientes por mes",
    ),
    Shot(
        "Facturas emitidas a cada ciudad",
        "Cuenta de facturas por ciudad del cliente.",
        "SELECT city, count(*) AS invoices FROM gold.fact_customer_invoices "
        "GROUP BY city ORDER BY invoices DESC",
        "bar",
        "city",
        "invoices",
        "Facturas por ciudad",
    ),
    Shot(
        "¿Qué clientes nos deben más dinero?",
        "Importe pendiente de las facturas por cliente.",
        "SELECT customer_name, sum(amount_due) AS pending FROM gold.fact_customer_invoices "
        "WHERE amount_due > 0 "
        "GROUP BY customer_name ORDER BY pending DESC",
        "bar",
        "customer_name",
        "pending",
        "Pendiente de cobro por cliente",
    ),
    Shot(
        "¿Cuánto queda por cobrar de las facturas que vencen este mes?",
        "Suma del importe pendiente de las facturas que vencen este mes.",
        "SELECT sum(amount_due) AS pending FROM gold.fact_customer_invoices "
        "WHERE "
        "date_trunc('month', due_date) = date_trunc('month', current_date)",
        "kpi",
        None,
        "pending",
        "Por cobrar este mes",
    ),
    Shot(
        "Importe de los abonos por cliente",
        "Suma de los abonos por cliente.",
        "SELECT customer_name, sum(amount_total) AS credited FROM gold.fact_customer_credit_notes "
        "GROUP BY customer_name ORDER BY credited DESC",
        "bar",
        "customer_name",
        "credited",
        "Abonos por cliente",
    ),
    Shot(
        "¿Cuánto hemos facturado de cada producto?",
        "Suma de las líneas de factura por producto.",
        "SELECT product_name, sum(amount_untaxed) AS invoiced "
        "FROM gold.fact_customer_invoice_lines "
        "GROUP BY product_name ORDER BY invoiced DESC",
        "bar",
        "product_name",
        "invoiced",
        "Facturación por producto",
    ),
    Shot(
        "Evolución mensual de la facturación neta",
        "Facturación neta por mes, ya agregada.",
        "SELECT month, net_amount FROM gold.agg_invoicing_monthly ORDER BY month",
        "area",
        "month",
        "net_amount",
        "Facturación neta mensual",
    ),
    Shot(
        "¿Cuánto hemos comprado a cada proveedor?",
        "Suma de las facturas de proveedor.",
        "SELECT vendor_name, sum(amount_total) AS purchases FROM gold.fact_vendor_bills "
        "GROUP BY vendor_name ORDER BY purchases DESC",
        "bar",
        "vendor_name",
        "purchases",
        "Compras por proveedor",
    ),
    Shot(
        "¿Qué productos tienen menos de 10 unidades en stock?",
        "Productos con pocas existencias.",
        "SELECT product_name, quantity_on_hand FROM gold.agg_stock_by_product "
        "WHERE quantity_on_hand < 10 ORDER BY quantity_on_hand",
        "table",
        "product_name",
        "quantity_on_hand",
        "Productos con pocas existencias",
    ),
    Shot(
        "Los 10 productos con más valor en el almacén",
        "Productos ordenados por el valor de su stock.",
        "SELECT product_name, stock_value FROM gold.agg_stock_by_product "
        "ORDER BY stock_value DESC LIMIT 10",
        "bar",
        "product_name",
        "stock_value",
        "Productos con más valor en stock",
    ),
    Shot(
        "Existencias del producto Desk",
        "Stock del producto indicado.",
        "SELECT product_name, quantity_on_hand, quantity_reserved, quantity_available "
        "FROM gold.agg_stock_by_product WHERE lower(product_name) LIKE '%desk%'",
        "table",
        "product_name",
        "quantity_on_hand",
        "Existencias de Desk",
    ),
    Shot(
        "Unidades recibidas en el almacén por producto",
        "Suma de las entradas realizadas por producto.",
        "SELECT product_name, sum(quantity_done) AS units FROM gold.fact_stock_moves "
        "WHERE move_type = 'incoming' AND move_status = 'done' "
        "GROUP BY product_name ORDER BY units DESC",
        "bar",
        "product_name",
        "units",
        "Unidades recibidas por producto",
    ),
    Shot(
        "¿Cuántos movimientos de almacén hay de cada tipo?",
        "Cuenta de movimientos por tipo.",
        "SELECT move_type, count(*) AS moves FROM gold.fact_stock_moves "
        "GROUP BY move_type ORDER BY moves DESC",
        "bar",
        "move_type",
        "moves",
        "Movimientos por tipo",
    ),
    Shot(
        "¿Cuántas oportunidades hemos perdido?",
        "Cuenta de oportunidades perdidas.",
        "SELECT count(*) AS lost_opportunities FROM gold.fact_opportunities WHERE status = 'lost'",
        "kpi",
        None,
        "lost_opportunities",
        "Oportunidades perdidas",
    ),
    Shot(
        "Listado de oportunidades ganadas con su importe",
        "Oportunidades ganadas.",
        "SELECT title, customer_name, salesperson_name, expected_revenue "
        "FROM gold.fact_opportunities WHERE status = 'won' ORDER BY expected_revenue DESC",
        "table",
        "title",
        "expected_revenue",
        "Oportunidades ganadas",
    ),
    Shot(
        "¿Qué oportunidades se cierran este mes?",
        "Oportunidades abiertas con cierre previsto en el mes en curso.",
        "SELECT title, customer_name, expected_close_date, expected_revenue "
        "FROM gold.fact_opportunities WHERE status = 'open' "
        "AND date_trunc('month', expected_close_date) = date_trunc('month', current_date)",
        "table",
        "title",
        "expected_revenue",
        "Cierres previstos este mes",
    ),
    Shot(
        "Ingreso ponderado de las oportunidades por ciudad",
        "Suma del ingreso ponderado de las oportunidades abiertas por ciudad.",
        "SELECT city, sum(weighted_revenue) AS weighted FROM gold.fact_opportunities "
        "WHERE status = 'open' GROUP BY city ORDER BY weighted DESC",
        "bar",
        "city",
        "weighted",
        "Ingreso ponderado por ciudad",
    ),
    Shot(
        "Iniciativas creadas por mes",
        "Cuenta de iniciativas por mes de creación.",
        "SELECT date_trunc('month', created_date) AS month, count(*) AS leads "
        "FROM gold.fact_leads GROUP BY date_trunc('month', created_date) ORDER BY month",
        "line",
        "month",
        "leads",
        "Iniciativas por mes",
    ),
    Shot(
        "Clientes activos de Fremont",
        "Clientes activos de esa ciudad.",
        "SELECT customer_name FROM gold.dim_customer "
        "WHERE is_customer = true AND is_active = true AND lower(city) LIKE '%fremont%'",
        "table",
        None,
        None,
        "Clientes activos de Fremont",
    ),
    Shot(
        "¿Cuántos contactos archivados hay?",
        "Cuenta de contactos que no están activos.",
        "SELECT count(*) AS archived FROM gold.dim_customer WHERE is_active = false",
        "kpi",
        None,
        "archived",
        "Contactos archivados",
    ),
    Shot(
        "Productos con un precio superior a 500",
        "Productos del catálogo por encima de ese precio.",
        "SELECT product_name, list_price FROM gold.dim_product "
        "WHERE list_price > 500 ORDER BY list_price DESC",
        "bar",
        "product_name",
        "list_price",
        "Productos de más de 500",
    ),
    Shot(
        "Nombres de los comerciales",
        "Usuarios que actúan como comerciales.",
        "SELECT salesperson_name FROM gold.dim_salesperson WHERE is_external = false",
        "table",
        None,
        None,
        "Comerciales",
    ),
)
