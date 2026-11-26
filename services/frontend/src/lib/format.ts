// Presentación de valores en español. Los decimales llegan como texto exacto desde la API.
import type { Cell, ColumnKind } from "./api.ts";

const plain = new Intl.NumberFormat("es-ES", { maximumFractionDigits: 2 });
const compact = new Intl.NumberFormat("es-ES", { notation: "compact", maximumFractionDigits: 1 });
const seconds = new Intl.NumberFormat("es-ES", {
  minimumFractionDigits: 1,
  maximumFractionDigits: 1,
});
const month = new Intl.DateTimeFormat("es-ES", {
  month: "short",
  year: "numeric",
  timeZone: "UTC",
});
const day = new Intl.DateTimeFormat("es-ES", {
  day: "numeric",
  month: "short",
  year: "numeric",
  timeZone: "UTC",
});

export const EMPTY = "—";

export function toNumber(value: Cell | undefined): number | null {
  if (typeof value === "number") return Number.isFinite(value) ? value : null;
  if (typeof value !== "string" || value.trim() === "") return null;
  const parsed = Number(value);
  return Number.isFinite(parsed) ? parsed : null;
}

export function formatNumber(value: Cell | undefined, decimals?: number): string {
  const parsed = toNumber(value);
  if (parsed === null) return EMPTY;
  return decimals === undefined
    ? plain.format(parsed)
    : parsed.toLocaleString("es-ES", {
        minimumFractionDigits: decimals,
        maximumFractionDigits: decimals,
      });
}

/** Decimales de una columna: ninguno si todos los valores son enteros; si no, dos para todos. */
export function columnDecimals(values: readonly (Cell | undefined)[]): number {
  return values.some((value) => {
    const parsed = toNumber(value);
    return parsed !== null && !Number.isInteger(parsed);
  })
    ? 2
    : 0;
}

/** Para los ejes: cifras redondas y cortas. */
export function formatTick(value: number): string {
  return Math.abs(value) < 10_000 ? plain.format(value) : compact.format(value);
}

/** Trino escribe 2026-11-01 o 2026-11-01 00:00:00.000 UTC: se lee como UTC, sin desfases. */
export function parseDay(value: Cell | undefined): Date | null {
  if (typeof value !== "string") return null;
  const match = /^(\d{4})-(\d{2})-(\d{2})/.exec(value);
  if (match === null) return null;
  return new Date(Date.UTC(Number(match[1]), Number(match[2]) - 1, Number(match[3])));
}

export function isMonthly(values: readonly (Cell | undefined)[]): boolean {
  const days = values.map(parseDay).filter((date) => date !== null);
  return days.length > 0 && days.every((date) => date.getUTCDate() === 1);
}

export function formatDay(value: Cell | undefined, monthly: boolean): string {
  const date = parseDay(value);
  return date === null ? EMPTY : (monthly ? month : day).format(date);
}

export type CellStyle = { monthly?: boolean; decimals?: number };

export function formatCell(
  value: Cell | undefined,
  kind: ColumnKind,
  { monthly = false, decimals }: CellStyle = {},
): string {
  if (value === null || value === undefined || value === "") return EMPTY;
  if (kind === "number") return formatNumber(value, decimals);
  if (kind === "date") return formatDay(value, monthly);
  if (kind === "boolean") return value === true || value === "true" ? "Sí" : "No";
  return String(value);
}

export function formatMs(milliseconds: number): string {
  return `${seconds.format(milliseconds / 1000)} s`;
}

// Columnas de Gold y alias habituales del modelo.
const LABELS: Record<string, string> = {
  amount_due: "Pendiente",
  amount_tax: "Impuestos",
  amount_total: "Importe total",
  amount_untaxed: "Importe sin impuestos",
  city: "Ciudad",
  closed_date: "Fecha de cierre",
  company_name: "Empresa",
  created_date: "Fecha de alta",
  credit_notes: "Abonos",
  credited_amount: "Importe abonado",
  customer_name: "Cliente",
  customers: "Clientes",
  due_date: "Vencimiento",
  expected_close_date: "Cierre previsto",
  expected_revenue: "Ingreso esperado",
  invoice_date: "Fecha de factura",
  invoice_number: "Factura",
  invoiced_amount: "Importe facturado",
  invoices: "Facturas",
  is_active: "Activo",
  list_price: "Precio de venta",
  month: "Mes",
  move_status: "Estado del movimiento",
  move_type: "Tipo de movimiento",
  net_amount: "Importe neto",
  opportunities: "Oportunidades",
  order_date: "Fecha del pedido",
  order_number: "Pedido",
  orders: "Pedidos",
  priority: "Prioridad",
  probability: "Probabilidad",
  product_name: "Producto",
  product_type: "Tipo de producto",
  quantity: "Unidades",
  quantity_available: "Disponibles",
  quantity_on_hand: "En stock",
  quantity_reserved: "Reservadas",
  quotation_number: "Presupuesto",
  salesperson_name: "Comercial",
  sku: "Referencia",
  status: "Estado",
  stock_value: "Valor del stock",
  title: "Título",
  total_revenue: "Total facturado",
  total_sales: "Total de ventas",
  units: "Unidades",
  vendor_name: "Proveedor",
  weighted_revenue: "Ingreso ponderado",
};

export function columnLabel(name: string): string {
  const known = LABELS[name.toLowerCase()];
  if (known !== undefined) return known;
  const words = name.replaceAll("_", " ").trim();
  return words === "" ? name : words.charAt(0).toUpperCase() + words.slice(1);
}

/** qwen2.5-coder:3b-instruct-q4_K_M -> Ollama · qwen2.5-coder:3b */
export function modelName(provider: string, model: string): string {
  if (provider === "nim") return "NVIDIA NIM";
  return `Ollama · ${model.split("-instruct")[0] ?? model}`;
}

const CLAUSES = [
  "WITH",
  "SELECT",
  "FROM",
  "WHERE",
  "GROUP BY",
  "HAVING",
  "ORDER BY",
  "LIMIT",
  "UNION ALL",
  "LEFT JOIN",
  "JOIN",
];

/** Una cláusula por línea, sin tocar los textos entre comillas. Solo para leerlo mejor. */
export function formatSql(sql: string): string {
  const parts = sql.split(/('(?:[^']|'')*')/);
  const pattern = new RegExp(`\\s+(${CLAUSES.join("|")})\\b`, "g");
  return parts
    .map((part, index) => (index % 2 === 1 ? part : part.replace(pattern, "\n$1")))
    .join("")
    .trim();
}
