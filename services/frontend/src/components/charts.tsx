"use client";

// Gráficos de una sola serie: el título dice qué se dibuja, así que no hay leyenda. Los valores de
// los globos también están siempre en la tabla.
import {
  Area,
  AreaChart,
  Bar,
  BarChart,
  CartesianGrid,
  LabelList,
  Line,
  LineChart,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";

import type { Answer, Cell } from "@/lib/api";
import {
  columnDecimals,
  columnLabel,
  EMPTY,
  formatCell,
  formatNumber,
  formatTick,
  isMonthly,
  toNumber,
} from "@/lib/format";

const MAX_BARS = 20;
const BAR = 18;
const ROW = 34;
const PLOT = 300;

type Point = { label: string; value: number };
type Series = { name: string; points: Point[]; hidden: number; decimals: number };
type Shape = "bar" | "line" | "area";

function columnIndex(answer: Answer, name: string | null): number {
  return name === null ? -1 : answer.columns.findIndex((column) => column.name === name);
}

/** Columnas numéricas que se pueden dibujar. Un identificador no es una medida. */
export function measures(answer: Answer): string[] {
  return answer.columns
    .filter((column) => column.kind === "number" && !column.name.toLowerCase().endsWith("_id"))
    .map((column) => column.name);
}

/** Forma del gráfico. Si lo propuesto es una tabla, la que corresponde a sus datos. */
function shapeOf(answer: Answer): Shape {
  const { type, xAxis } = answer.chart;
  if (type === "bar" || type === "line" || type === "area") return type;
  return answer.columns[columnIndex(answer, xAxis)]?.kind === "date" ? "line" : "bar";
}

/** Pares etiqueta-valor del gráfico. Las filas sin valor numérico no se pueden dibujar. */
export function toSeries(answer: Answer, measure: string | null): Series | null {
  const x = columnIndex(answer, answer.chart.xAxis);
  const y = columnIndex(answer, measure);
  const category = answer.columns[x];
  const value = answer.columns[y];
  if (category === undefined || value === undefined) return null;
  const temporal = category.kind === "date";
  const rows = temporal
    ? [...answer.rows].sort((a, b) => String(a[x]).localeCompare(String(b[x])))
    : answer.rows;
  const monthly = temporal && isMonthly(rows.map((row) => row[x]));
  const points = rows.flatMap((row): Point[] => {
    const number = toNumber(row[y]);
    if (number === null) return [];
    const label = formatCell(row[x], category.kind, { monthly });
    return [{ label: label === EMPTY ? "Sin dato" : label, value: number }];
  });
  if (points.length === 0) return null;
  const ranked = shapeOf(answer) === "bar" && !temporal;
  if (ranked) points.sort((a, b) => b.value - a.value);
  const limit = ranked ? MAX_BARS : points.length;
  return {
    name: columnLabel(value.name),
    points: points.slice(0, limit),
    hidden: Math.max(0, points.length - limit),
    decimals: columnDecimals(points.map((point) => point.value)),
  };
}

type TooltipEntry = { value?: unknown };
type TooltipProps = {
  active?: boolean;
  label?: unknown;
  payload?: readonly TooltipEntry[];
  series: Series;
};

function ChartTooltip({ active, label, payload, series }: TooltipProps) {
  const value = payload?.[0]?.value;
  if (!active || value === undefined) return null;
  return (
    <div className="rounded-md border border-hairline bg-surface px-3 py-2 text-sm shadow-md">
      <p className="text-muted">{String(label)}</p>
      <p className="mt-1 flex items-center gap-2">
        <span aria-hidden="true" className="h-0.5 w-3 rounded-full bg-series" />
        <strong className="font-semibold">{formatNumber(value as Cell, series.decimals)}</strong>
        <span className="text-muted">{series.name}</span>
      </p>
    </div>
  );
}

const tick = { fill: "var(--muted)", fontSize: 12 };
const cursorFill = { fill: "var(--surface-raised)" };
const cursorLine = { stroke: "var(--axis)", strokeWidth: 1 };

function HorizontalBars({ series }: { series: Series }) {
  const longest = Math.max(...series.points.map((point) => point.label.length));
  const labelWidth = Math.min(240, Math.max(80, longest * 7 + 20));
  return (
    <ResponsiveContainer width="100%" height={series.points.length * ROW + 16}>
      <BarChart
        data={series.points}
        layout="vertical"
        margin={{ top: 4, right: 84, bottom: 4, left: 0 }}
      >
        <XAxis type="number" hide domain={[(min: number) => Math.min(0, min), "dataMax"]} />
        <YAxis
          type="category"
          dataKey="label"
          width={labelWidth}
          tick={tick}
          tickLine={false}
          axisLine={{ stroke: "var(--axis)" }}
          interval={0}
        />
        <Tooltip cursor={cursorFill} content={<ChartTooltip series={series} />} />
        <Bar
          dataKey="value"
          fill="var(--series)"
          barSize={BAR}
          radius={[0, 4, 4, 0]}
          isAnimationActive={false}
        >
          <LabelList
            dataKey="value"
            position="right"
            formatter={(value: unknown) => formatNumber(value as Cell, series.decimals)}
            fill="var(--ink)"
            fontSize={12}
          />
        </Bar>
      </BarChart>
    </ResponsiveContainer>
  );
}

const axes = (
  <>
    <CartesianGrid vertical={false} stroke="var(--grid)" />
    <XAxis
      dataKey="label"
      tick={tick}
      tickLine={false}
      axisLine={{ stroke: "var(--axis)" }}
      minTickGap={16}
    />
    <YAxis
      tick={tick}
      tickLine={false}
      axisLine={false}
      width={64}
      tickFormatter={formatTick}
    />
  </>
);

const margin = { top: 12, right: 24, bottom: 4, left: 0 };
const dot = { r: 4, fill: "var(--series)", stroke: "var(--surface)", strokeWidth: 2 };
const activeDot = { r: 6, fill: "var(--series)", stroke: "var(--surface)", strokeWidth: 2 };

function TimeSeries({ series, type }: { series: Series; type: Shape }) {
  const tooltip = (
    <Tooltip
      cursor={type === "bar" ? cursorFill : cursorLine}
      content={<ChartTooltip series={series} />}
    />
  );
  const dots = series.points.length <= 24 ? dot : false;
  return (
    <ResponsiveContainer width="100%" height={PLOT}>
      {type === "bar" ? (
        <BarChart data={series.points} margin={margin}>
          {axes}
          {tooltip}
          <Bar
            dataKey="value"
            fill="var(--series)"
            maxBarSize={24}
            radius={[4, 4, 0, 0]}
            isAnimationActive={false}
          />
        </BarChart>
      ) : type === "area" ? (
        <AreaChart data={series.points} margin={margin}>
          {axes}
          {tooltip}
          <Area
            dataKey="value"
            type="linear"
            stroke="var(--series)"
            strokeWidth={2}
            fill="var(--series)"
            fillOpacity={0.1}
            dot={dots}
            activeDot={activeDot}
            isAnimationActive={false}
          />
        </AreaChart>
      ) : (
        <LineChart data={series.points} margin={margin}>
          {axes}
          {tooltip}
          <Line
            dataKey="value"
            type="linear"
            stroke="var(--series)"
            strokeWidth={2}
            strokeLinecap="round"
            dot={dots}
            activeDot={activeDot}
            isAnimationActive={false}
          />
        </LineChart>
      )}
    </ResponsiveContainer>
  );
}

function Kpis({ answer }: { answer: Answer }) {
  const row = answer.rows[0] ?? [];
  const main = columnIndex(answer, answer.chart.yAxis);
  return (
    <dl className="flex flex-wrap gap-x-12 gap-y-6">
      {answer.columns.map((column, index) => {
        const hero = index === main;
        const decimals = column.kind === "number" ? columnDecimals([row[index]]) : undefined;
        return (
          <div key={column.name} className={hero ? "order-first" : undefined}>
            <dt className="text-sm text-muted">{columnLabel(column.name)}</dt>
            <dd
              className={
                hero ? "mt-1 text-5xl font-semibold tracking-tight" : "mt-1 text-2xl font-semibold"
              }
            >
              {formatCell(row[index], column.kind, { decimals })}
            </dd>
          </div>
        );
      })}
    </dl>
  );
}

/** Gráfico de una respuesta, para una de sus medidas. Null si no se puede dibujar. */
export function AnswerChart({ answer, measure }: { answer: Answer; measure: string | null }) {
  if (answer.chart.type === "kpi") return <Kpis answer={answer} />;
  const series = toSeries(answer, measure);
  if (series === null) return null;
  const temporal = answer.columns[columnIndex(answer, answer.chart.xAxis)]?.kind === "date";
  const shape = shapeOf(answer);
  const description = `${series.name}: ${series.points.length} valores. El detalle está en la tabla.`;
  return (
    <figure>
      <div role="img" aria-label={description}>
        {shape === "bar" && !temporal ? (
          <HorizontalBars series={series} />
        ) : (
          <TimeSeries series={series} type={shape} />
        )}
      </div>
      {series.hidden > 0 && (
        <figcaption className="mt-2 text-sm text-muted">
          Se muestran {series.points.length} de {series.points.length + series.hidden}. El resto, en
          la tabla.
        </figcaption>
      )}
    </figure>
  );
}

export function hasChart(answer: Answer): boolean {
  return answer.chart.type === "kpi" || toSeries(answer, answer.chart.yAxis) !== null;
}
