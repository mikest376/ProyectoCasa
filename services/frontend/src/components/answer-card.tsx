"use client";

import { useState } from "react";

import { AnswerChart, hasChart, measures } from "@/components/charts";
import { DataTable } from "@/components/data-table";
import { SqlPanel } from "@/components/sql-panel";
import { Badge, Callout, Card, CloudIcon, LockIcon, Segmented } from "@/components/ui";
import type { Answer, LlmInfo, Problem } from "@/lib/api";
import { columnLabel, formatMs, modelName } from "@/lib/format";

type View = "chart" | "table";
const VIEWS = [
  { value: "chart", label: "Gráfico" },
  { value: "table", label: "Tabla" },
] as const;

export function ModelBadge({ llm }: { llm: LlmInfo }) {
  return (
    <Badge
      tone="accent"
      title={
        llm.external
          ? "La pregunta y el esquema se envían a un proveedor externo"
          : "El modelo se ejecuta en este equipo: los datos no salen de él"
      }
    >
      {llm.external ? <CloudIcon /> : <LockIcon />}
      {modelName(llm.provider, llm.model)}
    </Badge>
  );
}

export function AnswerCard({ answer }: { answer: Answer }) {
  const drawable = hasChart(answer);
  const [view, setView] = useState<View>(answer.chart.type === "table" ? "table" : "chart");
  const [measure, setMeasure] = useState(answer.chart.yAxis);
  const empty = answer.rows.length === 0;
  const { llmMs, queryMs, totalMs } = answer.timings;
  return (
    <Card className="p-5 sm:p-6" aria-label={answer.question}>
      <p className="text-sm text-muted">{answer.question}</p>
      <div className="mt-1 flex flex-wrap items-start justify-between gap-3">
        <div className="min-w-0">
          <h2 className="text-xl font-semibold text-balance">{answer.title}</h2>
          {answer.explanation !== "" && (
            <p className="mt-1 max-w-prose text-sm text-muted">{answer.explanation}</p>
          )}
        </div>
        {drawable && !empty && (
          <Segmented label="Vista del resultado" options={VIEWS} value={view} onChange={setView} />
        )}
      </div>

      <div className="mt-6">
        {empty ? (
          <Callout title="Sin resultados" tone="warning">
            La consulta se ha ejecutado, pero ninguna fila cumple lo que has pedido.
          </Callout>
        ) : drawable && view === "chart" ? (
          <>
            {answer.chart.type !== "kpi" && (
              <MeasurePicker
                options={measures(answer)}
                value={measure ?? ""}
                onChange={setMeasure}
              />
            )}
            <AnswerChart answer={answer} measure={measure} />
          </>
        ) : (
          <DataTable columns={answer.columns} rows={answer.rows} />
        )}
        {answer.truncated && (
          <p className="mt-2 text-sm text-muted">
            El resultado está cortado en {answer.rows.length} filas.
          </p>
        )}
      </div>

      <div className="mt-6 flex flex-wrap items-center gap-2">
        <ModelBadge llm={answer.llm} />
        <Badge title={`Modelo ${formatMs(llmMs)} · Trino ${formatMs(queryMs)}`}>
          {formatMs(totalMs)} en total · Trino {formatMs(queryMs)}
        </Badge>
        <Badge>
          {answer.rows.length} {answer.rows.length === 1 ? "fila" : "filas"}
        </Badge>
        {answer.attempts > 1 && <Badge>SQL corregido en el segundo intento</Badge>}
      </div>
      <SqlPanel title="SQL ejecutado en Trino" sql={answer.sql} />
    </Card>
  );
}

/** Dice qué se dibuja. Con varias medidas, además permite cambiarla. */
function MeasurePicker({
  options,
  value,
  onChange,
}: {
  options: string[];
  value: string;
  onChange: (value: string) => void;
}) {
  if (options.length < 2) {
    return <p className="mb-2 text-sm font-medium text-muted">{columnLabel(value)}</p>;
  }
  return (
    <label className="mb-2 flex items-center gap-2 text-sm text-muted">
      Medida
      <select
        value={value}
        onChange={(event) => onChange(event.target.value)}
        className="rounded-md border border-hairline bg-surface px-2 py-1 text-ink"
      >
        {options.map((option) => (
          <option key={option} value={option}>
            {columnLabel(option)}
          </option>
        ))}
      </select>
    </label>
  );
}

const TITLES: Record<string, string> = {
  sql_rejected: "Consulta rechazada",
  query_failed: "La consulta no se puede ejecutar",
  bad_output: "El modelo no ha entendido la pregunta",
  timeout: "Tiempo agotado",
  busy: "Hay otras preguntas en curso",
  llm_unavailable: "El modelo no está disponible",
  catalog_unavailable: "El catálogo no está disponible",
  trino_unavailable: "Trino no está disponible",
  offline: "Sin conexión con la API",
};
const ADVICE: Record<string, string> = {
  sql_rejected: "Solo se permite leer la capa Gold. No se ha ejecutado nada.",
  query_failed: "Prueba a formularla de otra manera, o con uno de los ejemplos.",
  bad_output: "Prueba con una pregunta más concreta.",
};

export function ProblemCard({ question, problem }: { question: string; problem: Problem }) {
  const advice = ADVICE[problem.code];
  return (
    <Card className="p-5 sm:p-6" aria-label={question}>
      <p className="text-sm text-muted">{question}</p>
      <div className="mt-3">
        <Callout title={TITLES[problem.code] ?? "No se ha podido responder"} tone="critical">
          <p>{problem.message}</p>
          {advice !== undefined && <p className="mt-1">{advice}</p>}
        </Callout>
      </div>
      {problem.sql !== null && (
        <SqlPanel title="SQL propuesto por el modelo, sin ejecutar" sql={problem.sql} />
      )}
    </Card>
  );
}
