"""Modelos Gold: qué contiene cada tabla, a partir de qué tablas Silver y cómo se calcula.

Están desnormalizados: los nombres de cliente, producto y comercial se repiten en los hechos para
que una pregunta se resuelva casi siempre sin JOIN. Y cada tabla responde por sí sola, sin filtros
obligatorios: los pedidos no incluyen presupuestos ni las facturas, abonos. Las descripciones
llegan al prompt de Text-to-SQL a través de Unity Catalog: indican los valores posibles y cuándo
usar cada columna.
"""

from collections.abc import Callable
from dataclasses import dataclass

import polars as pl

Frames = dict[str, pl.DataFrame]
ModelColumn = tuple[str, pl.DataType, str]

INT = pl.Int32()
BIGINT = pl.Int64()
TEXT = pl.String()
BOOL = pl.Boolean()
DATE = pl.Date()
MONEY = pl.Decimal(18, 2)
QUANTITY = pl.Decimal(18, 3)
PERCENT = pl.Decimal(5, 2)


@dataclass(frozen=True, slots=True)
class Model:
    name: str
    domain: str
    description: str
    columns: tuple[ModelColumn, ...]
    sources: tuple[str, ...]
    build: Callable[[Frames], pl.DataFrame]
    # Se incrementa si cambia el cálculo: obliga a reconstruir aunque Silver no cambie.
    revision: int = 1

    @property
    def schema(self) -> dict[str, pl.DataType]:
        return {name: dtype for name, dtype, _ in self.columns}


# --- Piezas comunes -----------------------------------------------------------------------------


def _label(column: str, values: dict[str, str]) -> pl.Expr:
    """Traduce códigos de Odoo. Un código nuevo se conserva tal cual, no se pierde."""
    return pl.col(column).replace(values)


def _day(column: str) -> pl.Expr:
    return pl.col(column).dt.date()


def _partners(frames: Frames, key: str) -> pl.DataFrame:
    return frames["res_partner"].select(
        pl.col("id").alias(key),
        pl.col("name").alias("customer_name"),
        pl.col("city").alias("customer_city"),
    )


def _salespeople(frames: Frames, key: str) -> pl.DataFrame:
    people = frames["res_partner"].select(
        pl.col("id").alias("partner_id"), pl.col("name").alias("salesperson_name")
    )
    users = frames["res_users"].select(pl.col("id").alias(key), "partner_id")
    return users.join(people, on="partner_id", how="left").drop("partner_id")


def _products(frames: Frames) -> pl.DataFrame:
    templates = frames["product_template"].select(
        pl.col("id").alias("product_tmpl_id"),
        pl.col("name").alias("product_name"),
        pl.col("default_code").alias("template_code"),
        _label(
            "detailed_type", {"product": "storable", "consu": "consumable", "service": "service"}
        ).alias("product_type"),
        "list_price",
        "sale_ok",
        pl.col("active").alias("template_active"),
    )
    return (
        frames["product_product"]
        .select(pl.col("id").alias("product_id"), "product_tmpl_id", "default_code", "active")
        .join(templates, on="product_tmpl_id", how="left")
        .with_columns(
            pl.coalesce("default_code", "template_code").alias("sku"),
            (pl.col("active") & pl.col("template_active")).alias("is_active"),
        )
    )


def _in_company_currency(column: str) -> pl.Expr:
    """Odoo guarda el tipo de cambio del pedido: importe de la empresa = importe / tipo."""
    rate = pl.col("currency_rate")
    return (
        pl.when(rate.is_null() | (rate == 0) | (rate == 1))
        .then(pl.col(column))
        .otherwise((pl.col(column) / rate).cast(pl.Decimal(20, 6)))
    )


CONFIRMED = ("sale", "done")
PRIORITY = {"0": "baja", "1": "media", "2": "alta", "3": "muy alta"}
QUOTATION_STATUS = {"draft": "open", "sent": "open", "cancel": "cancelled"}


# --- Maestros -----------------------------------------------------------------------------------


def _dim_customer(frames: Frames) -> pl.DataFrame:
    return frames["res_partner"].select(
        pl.col("id").alias("customer_id"),
        pl.col("name").alias("customer_name"),
        pl.col("is_company").fill_null(False),
        "city",
        (pl.col("customer_rank").fill_null(0) > 0).alias("is_customer"),
        (pl.col("supplier_rank").fill_null(0) > 0).alias("is_supplier"),
        pl.col("active").fill_null(False).alias("is_active"),
        _day("create_date").alias("created_date"),
    )


def _dim_salesperson(frames: Frames) -> pl.DataFrame:
    return (
        frames["res_users"]
        .join(_salespeople(frames, "id"), on="id", how="left")
        .select(
            pl.col("id").alias("salesperson_id"),
            "salesperson_name",
            pl.col("active").fill_null(False).alias("is_active"),
            pl.col("share").fill_null(False).alias("is_external"),
        )
    )


# --- Ventas -------------------------------------------------------------------------------------


def _orders(frames: Frames, *, confirmed: bool) -> pl.DataFrame:
    state = pl.col("state").is_in(CONFIRMED)
    return (
        frames["sale_order"]
        .filter(state if confirmed else ~state)
        .join(_partners(frames, "partner_id"), on="partner_id", how="left")
        .join(_salespeople(frames, "user_id"), on="user_id", how="left")
        .with_columns(_day("date_order").alias("order_date"))
    )


def _fact_sales_orders(frames: Frames) -> pl.DataFrame:
    return _orders(frames, confirmed=True).select(
        pl.col("id").alias("order_id"),
        pl.col("name").alias("order_number"),
        "order_date",
        pl.col("partner_id").alias("customer_id"),
        "customer_name",
        pl.col("customer_city").alias("city"),
        "salesperson_name",
        _in_company_currency("amount_untaxed").alias("amount_untaxed"),
        _in_company_currency("amount_tax").alias("amount_tax"),
        _in_company_currency("amount_total").alias("amount_total"),
        # Siempre cierto: si el modelo filtra por pedidos confirmados, el resultado no cambia.
        pl.lit(True).alias("is_confirmed"),
        (pl.col("invoice_status") == "to invoice").fill_null(False).alias("is_pending_invoicing"),
        pl.col("delivery_status")
        .is_in(["pending", "partial"])
        .fill_null(False)
        .alias("is_pending_delivery"),
    )


def _fact_quotations(frames: Frames) -> pl.DataFrame:
    return _orders(frames, confirmed=False).select(
        pl.col("id").alias("quotation_id"),
        pl.col("name").alias("quotation_number"),
        pl.col("order_date").alias("quotation_date"),
        pl.col("validity_date").alias("valid_until"),
        _label("state", QUOTATION_STATUS).alias("quotation_status"),
        pl.col("partner_id").alias("customer_id"),
        "customer_name",
        "salesperson_name",
        _in_company_currency("amount_untaxed").alias("amount_untaxed"),
        _in_company_currency("amount_total").alias("amount_total"),
    )


def _fact_sales_order_lines(frames: Frames) -> pl.DataFrame:
    orders = _orders(frames, confirmed=True).select(
        pl.col("id").alias("order_id"),
        pl.col("name").alias("order_number"),
        "order_date",
        pl.col("partner_id").alias("customer_id"),
        "customer_name",
        "salesperson_name",
        "currency_rate",
    )
    products = _products(frames).select("product_id", "product_name", "sku", "product_type")
    return (
        frames["sale_order_line"]
        .filter(pl.col("display_type").is_null() & pl.col("product_id").is_not_null())
        .join(orders, on="order_id", how="inner")
        .join(products, on="product_id", how="left")
        .select(
            pl.col("id").alias("line_id"),
            "order_id",
            "order_number",
            "order_date",
            "customer_id",
            "customer_name",
            "salesperson_name",
            "product_id",
            "product_name",
            "sku",
            "product_type",
            pl.col("product_uom_qty").alias("quantity"),
            _in_company_currency("price_unit").alias("unit_price"),
            pl.col("discount").alias("discount_percent"),
            _in_company_currency("price_subtotal").alias("amount_untaxed"),
            _in_company_currency("price_total").alias("amount_total"),
        )
    )


def _agg_sales_monthly(frames: Frames) -> pl.DataFrame:
    return (
        _fact_sales_orders(frames)
        .group_by(pl.col("order_date").dt.truncate("1mo").alias("month"))
        .agg(
            pl.len().alias("orders"),
            pl.col("customer_id").n_unique().alias("customers"),
            pl.col("amount_untaxed").sum(),
            pl.col("amount_total").sum(),
        )
        .sort("month")
    )


# --- Facturación --------------------------------------------------------------------------------


def _documents(frames: Frames, move_type: str) -> pl.DataFrame:
    """Documentos contabilizados de un tipo, con importes en positivo y en moneda de la empresa."""
    return (
        frames["account_move"]
        .filter((pl.col("move_type") == move_type) & (pl.col("state") == "posted"))
        .join(_partners(frames, "partner_id"), on="partner_id", how="left")
        .join(_salespeople(frames, "invoice_user_id"), on="invoice_user_id", how="left")
        .with_columns(
            pl.col(f"{name}_signed").abs().alias(name)
            for name in ("amount_untaxed", "amount_tax", "amount_total", "amount_residual")
        )
    )


def _fact_customer_invoices(frames: Frames) -> pl.DataFrame:
    return _documents(frames, "out_invoice").select(
        pl.col("id").alias("invoice_id"),
        pl.col("name").alias("invoice_number"),
        "invoice_date",
        pl.col("invoice_date_due").alias("due_date"),
        pl.col("partner_id").alias("customer_id"),
        "customer_name",
        pl.col("customer_city").alias("city"),
        "salesperson_name",
        "amount_untaxed",
        "amount_tax",
        "amount_total",
        pl.col("amount_residual").alias("amount_due"),
        "payment_state",
    )


def _fact_customer_credit_notes(frames: Frames) -> pl.DataFrame:
    return _documents(frames, "out_refund").select(
        pl.col("id").alias("credit_note_id"),
        pl.col("name").alias("credit_note_number"),
        pl.col("invoice_date").alias("credit_note_date"),
        pl.col("partner_id").alias("customer_id"),
        "customer_name",
        "amount_untaxed",
        "amount_total",
        pl.col("amount_residual").alias("amount_due"),
        "payment_state",
    )


def _fact_customer_invoice_lines(frames: Frames) -> pl.DataFrame:
    invoices = _fact_customer_invoices(frames).select(
        pl.col("invoice_id").alias("move_id"),
        "invoice_number",
        "invoice_date",
        "customer_id",
        "customer_name",
    )
    products = _products(frames).select("product_id", "product_name", "product_type")
    return (
        frames["account_move_line"]
        .filter(pl.col("display_type") == "product")
        .join(invoices, on="move_id", how="inner")
        .join(products, on="product_id", how="left")
        .select(
            pl.col("id").alias("line_id"),
            pl.col("move_id").alias("invoice_id"),
            "invoice_number",
            "invoice_date",
            "customer_id",
            "customer_name",
            "product_id",
            "product_name",
            "product_type",
            "quantity",
            pl.col("price_unit").alias("unit_price"),
            # El haber de una venta es negativo en el saldo contable.
            (-pl.col("balance")).alias("amount_untaxed"),
        )
    )


def _fact_vendor_bills(frames: Frames) -> pl.DataFrame:
    return _documents(frames, "in_invoice").select(
        pl.col("id").alias("bill_id"),
        pl.col("name").alias("bill_number"),
        pl.col("invoice_date").alias("bill_date"),
        pl.col("invoice_date_due").alias("due_date"),
        pl.col("partner_id").alias("vendor_id"),
        pl.col("customer_name").alias("vendor_name"),
        "amount_untaxed",
        "amount_total",
        pl.col("amount_residual").alias("amount_due"),
        "payment_state",
    )


def _agg_invoicing_monthly(frames: Frames) -> pl.DataFrame:
    invoices = _fact_customer_invoices(frames).select(
        pl.col("invoice_date").alias("day"),
        pl.col("amount_total").alias("invoiced"),
        pl.lit(None, pl.Decimal(20, 6)).alias("credited"),
    )
    credits = _fact_customer_credit_notes(frames).select(
        pl.col("credit_note_date").alias("day"),
        pl.lit(None, pl.Decimal(20, 6)).alias("invoiced"),
        pl.col("amount_total").alias("credited"),
    )
    return (
        pl.concat([invoices, credits])
        .filter(pl.col("day").is_not_null())
        .group_by(pl.col("day").dt.truncate("1mo").alias("month"))
        .agg(
            pl.col("invoiced").count().alias("invoices"),
            pl.col("credited").count().alias("credit_notes"),
            pl.col("invoiced").sum().alias("invoiced_amount"),
            pl.col("credited").sum().alias("credited_amount"),
        )
        .with_columns((pl.col("invoiced_amount") - pl.col("credited_amount")).alias("net_amount"))
        .sort("month")
    )


# --- Inventario ---------------------------------------------------------------------------------


def _dim_product(frames: Frames) -> pl.DataFrame:
    return _products(frames).select(
        "product_id",
        "product_name",
        "sku",
        "product_type",
        "list_price",
        pl.col("sale_ok").fill_null(False).alias("can_be_sold"),
        pl.col("is_active").fill_null(False),
    )


def _internal_quants(frames: Frames) -> pl.DataFrame:
    """Odoo solo calcula inventory_date en ubicaciones internas o en tránsito."""
    return frames["stock_quant"].filter(pl.col("inventory_date").is_not_null())


def _agg_stock_by_product(frames: Frames) -> pl.DataFrame:
    products = _products(frames).select(
        "product_id", "product_name", "sku", "product_type", "list_price"
    )
    stock = (
        _internal_quants(frames)
        .group_by("product_id")
        .agg(
            pl.col("quantity").sum().alias("quantity_on_hand"),
            pl.col("reserved_quantity").sum().alias("quantity_reserved"),
        )
    )
    zero = pl.lit("0").cast(pl.Decimal(20, 6))
    return (
        products.filter(pl.col("product_type") != "service")
        .join(stock, on="product_id", how="left")
        .with_columns(
            pl.col("quantity_on_hand").fill_null(zero),
            pl.col("quantity_reserved").fill_null(zero),
        )
        .select(
            "product_id",
            "product_name",
            "sku",
            "product_type",
            "quantity_on_hand",
            "quantity_reserved",
            (pl.col("quantity_on_hand") - pl.col("quantity_reserved")).alias("quantity_available"),
            "list_price",
            (pl.col("quantity_on_hand") * pl.col("list_price")).alias("stock_value"),
        )
    )


def _fact_stock_moves(frames: Frames) -> pl.DataFrame:
    internal = _internal_quants(frames)["location_id"].unique().to_list()
    source = pl.col("location_id").is_in(internal)
    target = pl.col("location_dest_id").is_in(internal)
    products = _products(frames).select("product_id", "product_name", "sku")
    return (
        frames["stock_move"]
        .join(products, on="product_id", how="left")
        .select(
            pl.col("id").alias("move_id"),
            "reference",
            _day("date").alias("move_date"),
            _label(
                "state",
                {
                    "draft": "draft",
                    "waiting": "waiting",
                    "confirmed": "waiting",
                    "partially_available": "partially_reserved",
                    "assigned": "reserved",
                    "done": "done",
                    "cancel": "cancelled",
                },
            ).alias("move_status"),
            pl.when(pl.col("is_inventory").fill_null(False))
            .then(pl.lit("adjustment"))
            .when(target & ~source)
            .then(pl.lit("incoming"))
            .when(source & ~target)
            .then(pl.lit("outgoing"))
            .when(source & target)
            .then(pl.lit("internal"))
            .otherwise(pl.lit("other"))
            .alias("move_type"),
            (~pl.col("state").is_in(["done", "cancel"])).alias("is_pending"),
            "product_id",
            "product_name",
            "sku",
            pl.col("product_uom_qty").alias("quantity_requested"),
            pl.when(pl.col("state") == "done")
            .then(pl.col("quantity"))
            .otherwise(pl.lit("0").cast(pl.Decimal(20, 6)))
            .alias("quantity_done"),
        )
    )


# --- CRM ----------------------------------------------------------------------------------------


def _leads(frames: Frames, kind: str) -> pl.DataFrame:
    return (
        frames["crm_lead"]
        .filter(pl.col("type") == kind)
        .join(_partners(frames, "partner_id"), on="partner_id", how="left")
        .join(_salespeople(frames, "user_id"), on="user_id", how="left")
        .with_columns(
            pl.when(~pl.col("active").fill_null(True))
            .then(pl.lit("lost"))
            .when(pl.col("probability") >= 100)
            .then(pl.lit("won"))
            .otherwise(pl.lit("open"))
            .alias("status"),
            pl.coalesce("customer_name", "partner_name").alias("company_name"),
            pl.coalesce("city", "customer_city").alias("lead_city"),
        )
    )


def _fact_opportunities(frames: Frames) -> pl.DataFrame:
    return _leads(frames, "opportunity").select(
        pl.col("id").alias("opportunity_id"),
        pl.col("name").alias("title"),
        "status",
        pl.col("company_name").alias("customer_name"),
        pl.col("lead_city").alias("city"),
        "salesperson_name",
        _label("priority", PRIORITY).alias("priority"),
        pl.col("probability").round(2).cast(pl.String).cast(PERCENT).alias("probability"),
        "expected_revenue",
        pl.col("prorated_revenue").alias("weighted_revenue"),
        _day("create_date").alias("created_date"),
        pl.col("date_deadline").alias("expected_close_date"),
        _day("date_closed").alias("closed_date"),
    )


def _fact_leads(frames: Frames) -> pl.DataFrame:
    return _leads(frames, "lead").select(
        pl.col("id").alias("lead_id"),
        pl.col("name").alias("title"),
        pl.when(pl.col("status") == "lost")
        .then(pl.lit("lost"))
        .otherwise(pl.lit("open"))
        .alias("status"),
        "company_name",
        pl.col("lead_city").alias("city"),
        "salesperson_name",
        "expected_revenue",
        _day("create_date").alias("created_date"),
    )


# --- Catálogo -----------------------------------------------------------------------------------

_PARTY = ("res_partner", "res_users")
_PRODUCT = ("product_product", "product_template")

MODELS: tuple[Model, ...] = (
    Model(
        "dim_customer",
        "maestros",
        "Clientes, proveedores y contactos. Una fila por contacto.",
        (
            ("customer_id", INT, "Identificador del contacto"),
            ("customer_name", TEXT, "Nombre del cliente o empresa"),
            ("is_company", BOOL, "Verdadero si es una empresa, falso si es una persona"),
            ("city", TEXT, "Ciudad"),
            ("is_customer", BOOL, "Verdadero si ha comprado alguna vez"),
            ("is_supplier", BOOL, "Verdadero si es proveedor"),
            ("is_active", BOOL, "Falso si está archivado"),
            ("created_date", DATE, "Fecha de alta"),
        ),
        ("res_partner",),
        _dim_customer,
    ),
    Model(
        "dim_salesperson",
        "maestros",
        "Usuarios de Odoo que actúan como comerciales. Una fila por usuario.",
        (
            ("salesperson_id", INT, "Identificador del usuario"),
            ("salesperson_name", TEXT, "Nombre del comercial"),
            ("is_active", BOOL, "Falso si está archivado"),
            ("is_external", BOOL, "Verdadero si es un usuario externo del portal"),
        ),
        _PARTY,
        _dim_salesperson,
    ),
    Model(
        "fact_sales_orders",
        "ventas",
        "Pedidos de venta confirmados, sin presupuestos ni cancelados. Una fila por pedido.",
        (
            ("order_id", INT, "Identificador del pedido"),
            ("order_number", TEXT, "Número del pedido"),
            ("order_date", DATE, "Fecha del pedido"),
            ("customer_id", INT, "Identificador del cliente"),
            ("customer_name", TEXT, "Nombre del cliente"),
            ("city", TEXT, "Ciudad del cliente"),
            ("salesperson_name", TEXT, "Nombre del comercial"),
            ("amount_untaxed", MONEY, "Importe sin impuestos"),
            ("amount_tax", MONEY, "Impuestos"),
            ("amount_total", MONEY, "Importe total con impuestos"),
            ("is_confirmed", BOOL, "Siempre verdadero: solo hay pedidos confirmados"),
            ("is_pending_invoicing", BOOL, "Verdadero si falta facturar el pedido"),
            ("is_pending_delivery", BOOL, "Verdadero si falta entregar el pedido"),
        ),
        ("sale_order", *_PARTY),
        _fact_sales_orders,
        revision=5,
    ),
    Model(
        "fact_quotations",
        "ventas",
        "Presupuestos que no se han convertido en pedido. Una fila por presupuesto.",
        (
            ("quotation_id", INT, "Identificador del presupuesto"),
            ("quotation_number", TEXT, "Número del presupuesto"),
            ("quotation_date", DATE, "Fecha del presupuesto"),
            ("valid_until", DATE, "Fecha de validez"),
            ("quotation_status", TEXT, "Estado: 'open' (abierto) o 'cancelled' (cancelado)"),
            ("customer_id", INT, "Identificador del cliente"),
            ("customer_name", TEXT, "Nombre del cliente"),
            ("salesperson_name", TEXT, "Nombre del comercial"),
            ("amount_untaxed", MONEY, "Importe sin impuestos"),
            ("amount_total", MONEY, "Importe total con impuestos"),
        ),
        ("sale_order", *_PARTY),
        _fact_quotations,
    ),
    Model(
        "fact_sales_order_lines",
        "ventas",
        "Líneas de los pedidos confirmados: qué producto se vendió, a quién y por cuánto. "
        "Una fila por línea. Úsala para ventas por producto o unidades vendidas.",
        (
            ("line_id", INT, "Identificador de la línea"),
            ("order_id", INT, "Identificador del pedido"),
            ("order_number", TEXT, "Número del pedido"),
            ("order_date", DATE, "Fecha del pedido"),
            ("customer_id", INT, "Identificador del cliente"),
            ("customer_name", TEXT, "Nombre del cliente"),
            ("salesperson_name", TEXT, "Nombre del comercial"),
            ("product_id", INT, "Identificador del producto"),
            ("product_name", TEXT, "Nombre del producto"),
            ("sku", TEXT, "Referencia del producto"),
            ("product_type", TEXT, "Tipo: 'storable', 'consumable' o 'service'"),
            ("quantity", QUANTITY, "Unidades vendidas"),
            ("unit_price", MONEY, "Precio unitario"),
            ("discount_percent", PERCENT, "Descuento en porcentaje"),
            ("amount_untaxed", MONEY, "Importe de la línea sin impuestos"),
            ("amount_total", MONEY, "Importe de la línea con impuestos"),
        ),
        ("sale_order", "sale_order_line", *_PRODUCT, *_PARTY),
        _fact_sales_order_lines,
        revision=4,
    ),
    Model(
        "agg_sales_monthly",
        "ventas",
        "Ventas confirmadas por mes. Una fila por mes.",
        (
            ("month", DATE, "Primer día del mes"),
            ("orders", BIGINT, "Pedidos confirmados"),
            ("customers", BIGINT, "Clientes distintos"),
            ("amount_untaxed", MONEY, "Ventas sin impuestos"),
            ("amount_total", MONEY, "Ventas con impuestos"),
        ),
        ("sale_order", *_PARTY),
        _agg_sales_monthly,
    ),
    Model(
        "fact_customer_invoices",
        "facturacion",
        "Facturas de clientes contabilizadas, sin abonos. Una fila por factura.",
        (
            ("invoice_id", INT, "Identificador de la factura"),
            ("invoice_number", TEXT, "Número de factura"),
            ("invoice_date", DATE, "Fecha de factura"),
            ("due_date", DATE, "Fecha de vencimiento"),
            ("customer_id", INT, "Identificador del cliente"),
            ("customer_name", TEXT, "Nombre del cliente"),
            ("city", TEXT, "Ciudad del cliente"),
            ("salesperson_name", TEXT, "Nombre del comercial"),
            ("amount_untaxed", MONEY, "Base imponible"),
            ("amount_tax", MONEY, "Impuestos"),
            ("amount_total", MONEY, "Importe facturado, con impuestos"),
            ("amount_due", MONEY, "Importe pendiente de cobro: 0 si está cobrada"),
            (
                "payment_state",
                TEXT,
                "Cobro: 'not_paid', 'in_payment', 'paid', 'partial' o 'reversed' (rectificada)",
            ),
        ),
        ("account_move", *_PARTY),
        _fact_customer_invoices,
        revision=3,
    ),
    Model(
        "fact_customer_credit_notes",
        "facturacion",
        "Abonos (facturas rectificativas) a clientes. Una fila por abono.",
        (
            ("credit_note_id", INT, "Identificador del abono"),
            ("credit_note_number", TEXT, "Número del abono"),
            ("credit_note_date", DATE, "Fecha del abono"),
            ("customer_id", INT, "Identificador del cliente"),
            ("customer_name", TEXT, "Nombre del cliente"),
            ("amount_untaxed", MONEY, "Base imponible, en positivo"),
            ("amount_total", MONEY, "Importe abonado, en positivo"),
            ("amount_due", MONEY, "Importe pendiente de devolver"),
            ("payment_state", TEXT, "Pago: 'not_paid', 'paid', 'partial' o 'reversed'"),
        ),
        ("account_move", *_PARTY),
        _fact_customer_credit_notes,
    ),
    Model(
        "fact_customer_invoice_lines",
        "facturacion",
        "Líneas de las facturas de clientes, sin abonos. Una fila por línea. "
        "Úsala para facturación por producto.",
        (
            ("line_id", INT, "Identificador de la línea"),
            ("invoice_id", INT, "Identificador de la factura"),
            ("invoice_number", TEXT, "Número de factura"),
            ("invoice_date", DATE, "Fecha de factura"),
            ("customer_id", INT, "Identificador del cliente"),
            ("customer_name", TEXT, "Nombre del cliente"),
            ("product_id", INT, "Identificador del producto"),
            ("product_name", TEXT, "Nombre del producto"),
            ("product_type", TEXT, "Tipo: 'storable', 'consumable' o 'service'"),
            ("quantity", QUANTITY, "Unidades facturadas"),
            ("unit_price", MONEY, "Precio unitario"),
            ("amount_untaxed", MONEY, "Importe facturado sin impuestos"),
        ),
        ("account_move", "account_move_line", *_PRODUCT, *_PARTY),
        _fact_customer_invoice_lines,
        revision=2,
    ),
    Model(
        "fact_vendor_bills",
        "facturacion",
        "Facturas de proveedores contabilizadas (compras), sin abonos. Una fila por factura.",
        (
            ("bill_id", INT, "Identificador de la factura de proveedor"),
            ("bill_number", TEXT, "Número de factura"),
            ("bill_date", DATE, "Fecha de factura"),
            ("due_date", DATE, "Fecha de vencimiento"),
            ("vendor_id", INT, "Identificador del proveedor"),
            ("vendor_name", TEXT, "Nombre del proveedor"),
            ("amount_untaxed", MONEY, "Base imponible"),
            ("amount_total", MONEY, "Importe comprado, con impuestos"),
            ("amount_due", MONEY, "Importe pendiente de pago: 0 si está pagada"),
            ("payment_state", TEXT, "Pago: 'not_paid', 'in_payment', 'paid' o 'partial'"),
        ),
        ("account_move", *_PARTY),
        _fact_vendor_bills,
        revision=2,
    ),
    Model(
        "agg_invoicing_monthly",
        "facturacion",
        "Facturación a clientes por mes, con los abonos y el neto. Una fila por mes.",
        (
            ("month", DATE, "Primer día del mes"),
            ("invoices", BIGINT, "Número de facturas"),
            ("credit_notes", BIGINT, "Número de abonos"),
            ("invoiced_amount", MONEY, "Total facturado, sin restar abonos"),
            ("credited_amount", MONEY, "Total abonado, en positivo"),
            ("net_amount", MONEY, "Facturación neta: facturado menos abonado"),
        ),
        ("account_move", *_PARTY),
        _agg_invoicing_monthly,
        revision=2,
    ),
    Model(
        "dim_product",
        "inventario",
        "Productos y servicios. Una fila por variante.",
        (
            ("product_id", INT, "Identificador del producto"),
            ("product_name", TEXT, "Nombre del producto"),
            ("sku", TEXT, "Referencia interna"),
            ("product_type", TEXT, "Tipo: 'storable', 'consumable' o 'service'"),
            ("list_price", MONEY, "Precio de venta"),
            ("can_be_sold", BOOL, "Verdadero si se puede vender"),
            ("is_active", BOOL, "Falso si está archivado"),
        ),
        _PRODUCT,
        _dim_product,
    ),
    Model(
        "agg_stock_by_product",
        "inventario",
        "Existencias actuales por producto, sin servicios. Una fila por producto. "
        "Úsala para el estado del inventario.",
        (
            ("product_id", INT, "Identificador del producto"),
            ("product_name", TEXT, "Nombre del producto"),
            ("sku", TEXT, "Referencia interna"),
            ("product_type", TEXT, "Tipo: 'storable' o 'consumable'"),
            ("quantity_on_hand", QUANTITY, "Unidades en stock"),
            ("quantity_reserved", QUANTITY, "Unidades reservadas"),
            ("quantity_available", QUANTITY, "Unidades disponibles: stock menos reservas"),
            ("list_price", MONEY, "Precio de venta"),
            ("stock_value", MONEY, "Valor del stock a precio de venta"),
        ),
        ("stock_quant", *_PRODUCT),
        _agg_stock_by_product,
    ),
    Model(
        "fact_stock_moves",
        "inventario",
        "Movimientos de almacén. Una fila por movimiento. "
        "Los realizados tienen move_status = 'done'; los pendientes, is_pending = true.",
        (
            ("move_id", INT, "Identificador del movimiento"),
            ("reference", TEXT, "Referencia del albarán"),
            ("move_date", DATE, "Fecha prevista o de realización"),
            (
                "move_status",
                TEXT,
                "Estado: 'draft', 'waiting', 'partially_reserved', 'reserved', 'done' "
                "o 'cancelled'",
            ),
            (
                "move_type",
                TEXT,
                "Tipo: 'incoming' (entrada), 'outgoing' (salida), 'internal', 'adjustment' "
                "(ajuste) u 'other'",
            ),
            ("is_pending", BOOL, "Verdadero si aún no se ha realizado ni cancelado"),
            ("product_id", INT, "Identificador del producto"),
            ("product_name", TEXT, "Nombre del producto"),
            ("sku", TEXT, "Referencia interna"),
            ("quantity_requested", QUANTITY, "Unidades solicitadas"),
            ("quantity_done", QUANTITY, "Unidades movidas"),
        ),
        ("stock_move", "stock_quant", *_PRODUCT),
        _fact_stock_moves,
        revision=2,
    ),
    Model(
        "fact_opportunities",
        "crm",
        "Oportunidades de venta del CRM. Una fila por oportunidad.",
        (
            ("opportunity_id", INT, "Identificador de la oportunidad"),
            ("title", TEXT, "Título"),
            ("status", TEXT, "Estado: 'open' (abierta), 'won' (ganada) o 'lost' (perdida)"),
            ("customer_name", TEXT, "Cliente o empresa"),
            ("city", TEXT, "Ciudad"),
            ("salesperson_name", TEXT, "Nombre del comercial"),
            ("priority", TEXT, "Prioridad: 'baja', 'media', 'alta' o 'muy alta'"),
            ("probability", PERCENT, "Probabilidad de cierre, de 0 a 100"),
            ("expected_revenue", MONEY, "Ingreso esperado"),
            ("weighted_revenue", MONEY, "Ingreso esperado ponderado por la probabilidad"),
            ("created_date", DATE, "Fecha de creación"),
            ("expected_close_date", DATE, "Cierre previsto"),
            ("closed_date", DATE, "Fecha de cierre"),
        ),
        ("crm_lead", *_PARTY),
        _fact_opportunities,
        revision=2,
    ),
    Model(
        "fact_leads",
        "crm",
        "Iniciativas (leads) del CRM que aún no son oportunidades. Una fila por iniciativa.",
        (
            ("lead_id", INT, "Identificador de la iniciativa"),
            ("title", TEXT, "Título"),
            ("status", TEXT, "Estado: 'open' (abierta) o 'lost' (perdida)"),
            ("company_name", TEXT, "Empresa"),
            ("city", TEXT, "Ciudad"),
            ("salesperson_name", TEXT, "Nombre del comercial"),
            ("expected_revenue", MONEY, "Ingreso esperado"),
            ("created_date", DATE, "Fecha de creación"),
        ),
        ("crm_lead", *_PARTY),
        _fact_leads,
    ),
)

DOMAINS = ("ventas", "facturacion", "inventario", "crm", "maestros")
