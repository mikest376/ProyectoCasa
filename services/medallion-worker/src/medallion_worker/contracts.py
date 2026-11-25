"""Contratos de Silver: qué columnas de Odoo se esperan, con qué tipo y qué significan (TD-011).

Un contrato es una selección, no una copia: correos, teléfonos, direcciones, identificadores
fiscales y textos libres no pasan de Bronze, porque el análisis no los necesita (TD-004).
"""

from dataclasses import dataclass
from typing import Literal

import polars as pl

Kind = Literal["integer", "decimal", "double", "boolean", "text", "date", "timestamp", "translated"]

# Escala suficiente para importes, cantidades y precios de Odoo, sin coma flotante.
DECIMAL = pl.Decimal(20, 6)
POLARS_TYPES: dict[str, pl.DataType] = {
    "integer": pl.Int32(),
    "decimal": DECIMAL,
    "double": pl.Float64(),
    "boolean": pl.Boolean(),
    "text": pl.String(),
    "translated": pl.String(),
    "date": pl.Date(),
    "timestamp": pl.Datetime("us", "UTC"),
}
# Tipo de information_schema.columns que corresponde a cada clase (scripts/check_contracts.py).
POSTGRES_TYPES: dict[str, tuple[str, ...]] = {
    "integer": ("integer",),
    "decimal": ("numeric",),
    "double": ("double precision",),
    "boolean": ("boolean",),
    "text": ("character varying", "text"),
    "translated": ("jsonb",),
    "date": ("date",),
    "timestamp": ("timestamp without time zone",),
}


@dataclass(frozen=True, slots=True)
class Column:
    name: str
    kind: Kind
    description: str
    required: bool = False


@dataclass(frozen=True, slots=True)
class Contract:
    table: str
    domain: str
    description: str
    columns: tuple[Column, ...]

    @property
    def names(self) -> list[str]:
        return [column.name for column in self.columns]

    @property
    def schema(self) -> dict[str, pl.DataType]:
        return {column.name: POLARS_TYPES[column.kind] for column in self.columns}


# Columnas técnicas de Silver. Todas las tablas las llevan.
METADATA: tuple[tuple[str, pl.DataType, str], ...] = (
    ("_is_deleted", pl.Boolean(), "Verdadero si la fila se borró en Odoo"),
    ("_op", pl.String(), "Última operación: c alta, u cambio, d borrado, r carga inicial"),
    ("_source_lsn", pl.Int64(), "Posición en el WAL del último cambio: ordena los eventos"),
    ("_source_ts", pl.Datetime("us", "UTC"), "Momento del último cambio en Odoo"),
    ("_bronze_offset", pl.Int64(), "Offset de Kafka del último evento aplicado"),
    ("_updated_at", pl.Datetime("us", "UTC"), "Momento en que Silver aplicó el cambio"),
)


def _c(name: str, kind: Kind, description: str, required: bool = False) -> Column:
    return Column(name, kind, description, required)


_ID = _c("id", "integer", "Clave primaria en Odoo", True)
_CREATED = _c("create_date", "timestamp", "Fecha de creación del registro, en UTC")
_WRITTEN = _c("write_date", "timestamp", "Fecha de la última modificación, en UTC")

CONTRACTS: tuple[Contract, ...] = (
    Contract(
        "sale_order",
        "ventas",
        "Cabecera de presupuestos y pedidos de venta",
        (
            _ID,
            _c("name", "text", "Número del pedido", True),
            _c("state", "text", "Estado: draft, sent, sale o cancel"),
            _c("date_order", "timestamp", "Fecha del pedido", True),
            _c("validity_date", "date", "Fecha de validez del presupuesto"),
            _c("commitment_date", "timestamp", "Fecha de entrega comprometida"),
            _c("company_id", "integer", "Empresa", True),
            _c("partner_id", "integer", "Cliente (res_partner)", True),
            _c("user_id", "integer", "Comercial (res_users)"),
            _c("team_id", "integer", "Equipo de ventas"),
            _c("currency_id", "integer", "Moneda del pedido"),
            _c("currency_rate", "decimal", "Tipo de cambio respecto a la moneda de la empresa"),
            _c("amount_untaxed", "decimal", "Base imponible"),
            _c("amount_tax", "decimal", "Impuestos"),
            _c("amount_total", "decimal", "Total con impuestos"),
            _c("invoice_status", "text", "Estado de facturación"),
            _c("delivery_status", "text", "Estado de entrega"),
            _c("locked", "boolean", "Pedido bloqueado"),
            _c("opportunity_id", "integer", "Oportunidad de origen (crm_lead)"),
            _CREATED,
            _WRITTEN,
        ),
    ),
    Contract(
        "sale_order_line",
        "ventas",
        "Líneas de los pedidos de venta",
        (
            _ID,
            _c("order_id", "integer", "Pedido (sale_order)", True),
            _c("sequence", "integer", "Orden dentro del pedido"),
            _c("display_type", "text", "Secciones y notas: nulo en las líneas con producto"),
            _c("state", "text", "Estado del pedido"),
            _c("product_id", "integer", "Producto (product_product)"),
            _c("product_uom_qty", "decimal", "Cantidad pedida", True),
            _c("qty_delivered", "decimal", "Cantidad entregada"),
            _c("qty_invoiced", "decimal", "Cantidad facturada"),
            _c("price_unit", "decimal", "Precio unitario", True),
            _c("discount", "decimal", "Descuento en porcentaje"),
            _c("price_subtotal", "decimal", "Importe sin impuestos"),
            _c("price_total", "decimal", "Importe con impuestos"),
            _c("salesman_id", "integer", "Comercial (res_users)"),
            _c("order_partner_id", "integer", "Cliente (res_partner)"),
            _c("currency_id", "integer", "Moneda"),
            _c("company_id", "integer", "Empresa"),
            _c("is_downpayment", "boolean", "Línea de anticipo"),
            _CREATED,
            _WRITTEN,
        ),
    ),
    Contract(
        "account_move",
        "facturacion",
        "Asientos contables: facturas, abonos y otros apuntes",
        (
            _ID,
            _c("name", "text", "Número del asiento"),
            _c("move_type", "text", "Tipo: out_invoice, out_refund, in_invoice...", True),
            _c("state", "text", "Estado: draft, posted o cancel", True),
            _c("payment_state", "text", "Estado de cobro o pago"),
            _c("date", "date", "Fecha contable", True),
            _c("invoice_date", "date", "Fecha de factura"),
            _c("invoice_date_due", "date", "Fecha de vencimiento"),
            _c("journal_id", "integer", "Diario", True),
            _c("company_id", "integer", "Empresa"),
            _c("partner_id", "integer", "Cliente o proveedor (res_partner)"),
            _c("commercial_partner_id", "integer", "Entidad comercial (res_partner)"),
            _c("currency_id", "integer", "Moneda de la factura", True),
            _c("invoice_user_id", "integer", "Comercial (res_users)"),
            _c("invoice_origin", "text", "Documento de origen"),
            _c("reversed_entry_id", "integer", "Asiento que rectifica"),
            _c("amount_untaxed", "decimal", "Base imponible"),
            _c("amount_tax", "decimal", "Impuestos"),
            _c("amount_total", "decimal", "Total con impuestos"),
            _c("amount_residual", "decimal", "Importe pendiente"),
            _c("amount_untaxed_signed", "decimal", "Base con signo, en moneda de la empresa"),
            _c("amount_tax_signed", "decimal", "Impuestos con signo, en moneda de la empresa"),
            _c("amount_total_signed", "decimal", "Total con signo, en moneda de la empresa"),
            _c("amount_residual_signed", "decimal", "Pendiente con signo, en moneda de empresa"),
            _CREATED,
            _WRITTEN,
        ),
    ),
    Contract(
        "account_move_line",
        "facturacion",
        "Apuntes contables de cada asiento",
        (
            _ID,
            _c("move_id", "integer", "Asiento (account_move)", True),
            _c("display_type", "text", "Tipo: product, tax, payment_term...", True),
            _c("parent_state", "text", "Estado del asiento"),
            _c("product_id", "integer", "Producto (product_product)"),
            _c("account_id", "integer", "Cuenta contable"),
            _c("partner_id", "integer", "Cliente o proveedor (res_partner)"),
            _c("quantity", "decimal", "Cantidad"),
            _c("price_unit", "decimal", "Precio unitario"),
            _c("discount", "decimal", "Descuento en porcentaje"),
            _c("price_subtotal", "decimal", "Importe sin impuestos"),
            _c("price_total", "decimal", "Importe con impuestos"),
            _c("debit", "decimal", "Debe"),
            _c("credit", "decimal", "Haber"),
            _c("balance", "decimal", "Saldo, en moneda de la empresa"),
            _c("amount_currency", "decimal", "Saldo, en la moneda del apunte"),
            _c("amount_residual", "decimal", "Importe pendiente"),
            _c("currency_id", "integer", "Moneda", True),
            _c("date", "date", "Fecha contable"),
            _c("date_maturity", "date", "Vencimiento"),
            _c("reconciled", "boolean", "Conciliado"),
            _CREATED,
            _WRITTEN,
        ),
    ),
    Contract(
        "stock_move",
        "inventario",
        "Movimientos de existencias",
        (
            _ID,
            _c("state", "text", "Estado: draft, confirmed, assigned, done o cancel"),
            _c("product_id", "integer", "Producto (product_product)", True),
            _c("product_uom_qty", "decimal", "Cantidad solicitada", True),
            _c("product_qty", "decimal", "Cantidad en la unidad del producto"),
            _c("quantity", "decimal", "Cantidad realizada o reservada"),
            _c("location_id", "integer", "Ubicación de origen", True),
            _c("location_dest_id", "integer", "Ubicación de destino", True),
            _c("picking_id", "integer", "Albarán"),
            _c("partner_id", "integer", "Contacto (res_partner)"),
            _c("origin", "text", "Documento de origen"),
            _c("reference", "text", "Referencia del movimiento"),
            _c("sale_line_id", "integer", "Línea de venta (sale_order_line)"),
            _c("is_inventory", "boolean", "Ajuste de inventario"),
            _c("scrapped", "boolean", "Desecho"),
            _c("date", "timestamp", "Fecha prevista o de realización", True),
            _c("company_id", "integer", "Empresa", True),
            _c("warehouse_id", "integer", "Almacén"),
            _CREATED,
            _WRITTEN,
        ),
    ),
    Contract(
        "stock_quant",
        "inventario",
        "Existencias por producto y ubicación",
        (
            _ID,
            _c("product_id", "integer", "Producto (product_product)", True),
            _c("company_id", "integer", "Empresa"),
            _c("location_id", "integer", "Ubicación", True),
            _c("lot_id", "integer", "Lote"),
            _c("quantity", "decimal", "Cantidad"),
            _c("reserved_quantity", "decimal", "Cantidad reservada", True),
            _c("inventory_date", "date", "Próximo recuento: solo en ubicaciones internas"),
            _c("in_date", "timestamp", "Fecha de entrada", True),
            _CREATED,
            _WRITTEN,
        ),
    ),
    Contract(
        "product_product",
        "inventario",
        "Variantes de producto",
        (
            _ID,
            _c("product_tmpl_id", "integer", "Plantilla (product_template)", True),
            _c("default_code", "text", "Referencia interna"),
            _c("active", "boolean", "Falso si está archivado"),
            _c("volume", "decimal", "Volumen"),
            _c("weight", "decimal", "Peso"),
            _CREATED,
            _WRITTEN,
        ),
    ),
    Contract(
        "product_template",
        "inventario",
        "Plantillas de producto",
        (
            _ID,
            _c("name", "translated", "Nombre, en el idioma configurado", True),
            _c("default_code", "text", "Referencia interna"),
            _c("detailed_type", "text", "Tipo: product, consu o service", True),
            _c("categ_id", "integer", "Categoría", True),
            _c("uom_id", "integer", "Unidad de medida", True),
            _c("list_price", "decimal", "Precio de venta"),
            _c("sale_ok", "boolean", "Se puede vender"),
            _c("purchase_ok", "boolean", "Se puede comprar"),
            _c("active", "boolean", "Falso si está archivado"),
            _c("invoice_policy", "text", "Política de facturación"),
            _CREATED,
            _WRITTEN,
        ),
    ),
    Contract(
        "crm_lead",
        "crm",
        "Iniciativas y oportunidades",
        (
            _ID,
            _c("name", "text", "Título", True),
            _c("type", "text", "Tipo: lead u opportunity", True),
            _c("stage_id", "integer", "Etapa"),
            _c("team_id", "integer", "Equipo de ventas"),
            _c("user_id", "integer", "Comercial (res_users)"),
            _c("partner_id", "integer", "Cliente (res_partner)"),
            _c("partner_name", "text", "Empresa indicada en la iniciativa"),
            _c("city", "text", "Ciudad"),
            _c("country_id", "integer", "País"),
            _c("priority", "text", "Prioridad, de 0 a 3"),
            _c("probability", "double", "Probabilidad de cierre, de 0 a 100"),
            _c("expected_revenue", "decimal", "Ingreso esperado"),
            _c("prorated_revenue", "decimal", "Ingreso ponderado por la probabilidad"),
            _c("active", "boolean", "Falso si está archivada o perdida"),
            _c("date_open", "timestamp", "Fecha de asignación"),
            _c("date_closed", "timestamp", "Fecha de cierre"),
            _c("date_deadline", "date", "Cierre previsto"),
            _c("date_conversion", "timestamp", "Conversión a oportunidad"),
            _c("lost_reason_id", "integer", "Motivo de pérdida"),
            _CREATED,
            _WRITTEN,
        ),
    ),
    Contract(
        "res_partner",
        "maestros",
        "Contactos: clientes, proveedores y empresas",
        (
            _ID,
            _c("name", "text", "Nombre"),
            _c("is_company", "boolean", "Es una empresa"),
            _c("parent_id", "integer", "Empresa a la que pertenece"),
            _c("commercial_partner_id", "integer", "Entidad comercial"),
            _c("city", "text", "Ciudad"),
            _c("country_id", "integer", "País"),
            _c("lang", "text", "Idioma"),
            _c("customer_rank", "integer", "Mayor que 0 si ha sido cliente"),
            _c("supplier_rank", "integer", "Mayor que 0 si ha sido proveedor"),
            _c("employee", "boolean", "Es empleado"),
            _c("active", "boolean", "Falso si está archivado"),
            _c("company_id", "integer", "Empresa"),
            _c("user_id", "integer", "Comercial (res_users)"),
            _CREATED,
            _WRITTEN,
        ),
    ),
    Contract(
        "res_users",
        "maestros",
        "Usuarios de Odoo, sin credenciales",
        (
            _ID,
            _c("partner_id", "integer", "Contacto (res_partner)", True),
            _c("active", "boolean", "Falso si está archivado"),
            _c("company_id", "integer", "Empresa", True),
            _c("share", "boolean", "Usuario externo (portal)"),
            _c("sale_team_id", "integer", "Equipo de ventas"),
            _CREATED,
            _WRITTEN,
        ),
    ),
)

BY_TABLE: dict[str, Contract] = {contract.table: contract for contract in CONTRACTS}
