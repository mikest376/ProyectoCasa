// Contrato con FastAPI. Las respuestas se comprueban: nada entra en la interfaz sin validar.

export type Cell = string | number | boolean | null;
export type ColumnKind = "number" | "date" | "boolean" | "text";
export type ChartType = "kpi" | "bar" | "line" | "area" | "table";

export type Column = { name: string; kind: ColumnKind };
export type LlmInfo = { provider: string; model: string; external: boolean };
export type ExampleGroup = { domain: string; label: string; questions: string[] };
export type TableInfo = { name: string; domain: string; description: string };

export type Answer = {
  question: string;
  title: string;
  explanation: string;
  sql: string;
  tables: string[];
  chart: { type: ChartType; xAxis: string | null; yAxis: string | null };
  columns: Column[];
  rows: Cell[][];
  truncated: boolean;
  attempts: number;
  llm: LlmInfo;
  timings: { llmMs: number; queryMs: number; totalMs: number };
};

export type Problem = { code: string; message: string; sql: string | null };

type Json = Record<string, unknown>;

function isObject(value: unknown): value is Json {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function isString(value: unknown): value is string {
  return typeof value === "string";
}

function textOrNull(value: unknown): string | null {
  return isString(value) ? value : null;
}

function numberOr(value: unknown, fallback: number): number {
  return typeof value === "number" && Number.isFinite(value) ? value : fallback;
}

const KINDS: readonly string[] = ["number", "date", "boolean", "text"];
const CHARTS: readonly string[] = ["kpi", "bar", "line", "area", "table"];

function isColumn(value: unknown): value is Column {
  return isObject(value) && isString(value.name) && isString(value.kind) && KINDS.includes(value.kind);
}

function isCell(value: unknown): value is Cell {
  return value === null || ["string", "number", "boolean"].includes(typeof value);
}

function toCells(row: unknown): Cell[] {
  if (!Array.isArray(row)) return [];
  return row.map((value: unknown) => (isCell(value) ? value : JSON.stringify(value)));
}

export function parseLlm(value: unknown): LlmInfo | null {
  if (!isObject(value) || !isString(value.provider) || !isString(value.model)) return null;
  return { provider: value.provider, model: value.model, external: value.external === true };
}

export function parseExamples(value: unknown): ExampleGroup[] {
  if (!Array.isArray(value)) return [];
  return value.flatMap((group: unknown) => {
    if (!isObject(group) || !isString(group.domain) || !isString(group.label)) return [];
    const questions = Array.isArray(group.questions) ? group.questions.filter(isString) : [];
    return [{ domain: group.domain, label: group.label, questions }];
  });
}

export function parseTables(value: unknown): TableInfo[] {
  if (!Array.isArray(value)) return [];
  return value.flatMap((table: unknown) => {
    if (!isObject(table) || !isString(table.name)) return [];
    return [
      {
        name: table.name,
        domain: textOrNull(table.domain) ?? "",
        description: textOrNull(table.description) ?? "",
      },
    ];
  });
}

export function parseAnswer(value: unknown): Answer | null {
  if (!isObject(value) || !isString(value.sql) || !isString(value.question)) return null;
  const llm = parseLlm(value);
  if (llm === null || !Array.isArray(value.columns) || !Array.isArray(value.rows)) return null;
  const chart = isObject(value.chart) ? value.chart : {};
  const type = isString(chart.type) && CHARTS.includes(chart.type) ? chart.type : "table";
  const timings = isObject(value.timings) ? value.timings : {};
  return {
    question: value.question,
    title: textOrNull(value.title) ?? "Resultado",
    explanation: textOrNull(value.explanation) ?? "",
    sql: value.sql,
    tables: Array.isArray(value.tables) ? value.tables.filter(isString) : [],
    chart: {
      type: type as ChartType,
      xAxis: textOrNull(chart.x_axis),
      yAxis: textOrNull(chart.y_axis),
    },
    columns: value.columns.filter(isColumn),
    rows: value.rows.map(toCells),
    truncated: value.truncated === true,
    attempts: numberOr(value.attempts, 1),
    llm,
    timings: {
      llmMs: numberOr(timings.llm_ms, 0),
      queryMs: numberOr(timings.query_ms, 0),
      totalMs: numberOr(timings.total_ms, 0),
    },
  };
}

export function parseProblem(value: unknown, status: number): Problem {
  if (isObject(value) && isString(value.code) && isString(value.message)) {
    return { code: value.code, message: value.message, sql: textOrNull(value.sql) };
  }
  return {
    code: "unexpected",
    message:
      status === 422
        ? "La pregunta no es válida: escribe entre 3 y 300 caracteres."
        : `La API ha respondido con un error (${status}).`,
    sql: null,
  };
}

/** Tras Caddy, la interfaz y la API comparten origen. En desarrollo, la API va en su puerto. */
export function apiBase(location: { protocol: string; hostname: string; port: string }): string {
  return location.port === "3000" ? `${location.protocol}//${location.hostname}:8000` : "";
}

export type Outcome = { ok: true; answer: Answer } | { ok: false; problem: Problem };

const TIMEOUT_MS = 180_000;

export async function askQuestion(question: string): Promise<Outcome> {
  let response: Response;
  try {
    response = await fetch(`${apiBase(window.location)}/api/query`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ question }),
      signal: AbortSignal.timeout(TIMEOUT_MS),
    });
  } catch (error) {
    const late = error instanceof DOMException && error.name === "TimeoutError";
    return {
      ok: false,
      problem: {
        code: late ? "timeout" : "offline",
        message: late
          ? "La respuesta ha tardado demasiado."
          : "No se puede conectar con la API. Comprueba que el servicio está en marcha.",
        sql: null,
      },
    };
  }
  const data: unknown = await response.json().catch(() => null);
  if (!response.ok) return { ok: false, problem: parseProblem(data, response.status) };
  const answer = parseAnswer(data);
  return answer === null
    ? { ok: false, problem: parseProblem(null, 500) }
    : { ok: true, answer };
}
