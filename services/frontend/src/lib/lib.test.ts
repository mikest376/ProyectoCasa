// node --test: Node ejecuta TypeScript sin compilar.
import assert from "node:assert/strict";
import { test } from "node:test";

import { apiBase, parseAnswer, parseExamples, parseProblem } from "./api.ts";
import {
  columnDecimals,
  columnLabel,
  EMPTY,
  formatCell,
  formatDay,
  formatNumber,
  formatSql,
  isMonthly,
  modelName,
  toNumber,
} from "./format.ts";

const valid = {
  question: "¿Ventas?",
  title: "Ventas",
  explanation: "Suma.",
  sql: "SELECT 1",
  tables: ["fact_sales_orders"],
  chart: { type: "bar", x_axis: "customer_name", y_axis: "total" },
  columns: [
    { name: "customer_name", kind: "text" },
    { name: "total", kind: "number" },
  ],
  rows: [["Azure Interior", "1250.10"]],
  truncated: false,
  attempts: 1,
  provider: "ollama",
  model: "qwen2.5-coder:3b-instruct-q4_K_M",
  external: false,
  timings: { llm_ms: 3000, query_ms: 100, total_ms: 3150 },
};

test("una respuesta válida se acepta entera", () => {
  const answer = parseAnswer(valid);
  assert.ok(answer);
  assert.deepEqual(answer.chart, { type: "bar", xAxis: "customer_name", yAxis: "total" });
  assert.equal(answer.timings.totalMs, 3150);
  assert.equal(answer.llm.external, false);
});

test("una respuesta incompleta o extraña no llega a la interfaz", () => {
  for (const broken of [null, [], "texto", {}, { ...valid, sql: 5 }, { ...valid, rows: "x" }]) {
    assert.equal(parseAnswer(broken), null);
  }
  const odd = parseAnswer({ ...valid, chart: { type: "pastel" }, rows: [[{ a: 1 }, null]] });
  assert.equal(odd?.chart.type, "table");
  assert.deepEqual(odd?.rows, [['{"a":1}', null]]);
});

test("los errores de la API se entienden, también los inesperados", () => {
  assert.deepEqual(parseProblem({ code: "busy", message: "Espera" }, 429), {
    code: "busy",
    message: "Espera",
    sql: null,
  });
  assert.match(parseProblem({ detail: [] }, 422).message, /3 y 300/);
  assert.match(parseProblem(null, 500).message, /500/);
  assert.deepEqual(parseExamples({ nada: 1 }), []);
});

test("tras Caddy se usa el mismo origen; en desarrollo, el puerto de la API", () => {
  assert.equal(apiBase({ protocol: "https:", hostname: "tfg.example.org", port: "" }), "");
  assert.equal(
    apiBase({ protocol: "http:", hostname: "localhost", port: "3000" }),
    "http://localhost:8000",
  );
});

test("números y decimales exactos", () => {
  assert.equal(toNumber("1250.10"), 1250.1);
  assert.equal(toNumber("abc"), null);
  assert.equal(toNumber(""), null);
  assert.equal(formatNumber("31809.61"), "31.809,61");
  assert.equal(formatNumber(null), EMPTY);
  // Una columna con céntimos los lleva en todas sus filas; una de unidades, en ninguna.
  assert.equal(columnDecimals(["143175.00", "36512.50"]), 2);
  assert.equal(columnDecimals(["1388.000", 5, null]), 0);
  assert.equal(formatNumber("143175.00", 2), "143.175,00");
});

test("las fechas se leen en UTC y los meses se reconocen", () => {
  assert.equal(isMonthly(["2026-10-01", "2026-11-01 00:00:00.000 UTC"]), true);
  assert.equal(isMonthly(["2026-10-01", "2026-11-02"]), false);
  assert.equal(isMonthly([]), false);
  assert.match(formatDay("2026-11-01", true), /nov.* 2026/);
  assert.match(formatDay("2026-11-25", false), /25 nov.* 2026/);
  assert.equal(formatCell(true, "boolean"), "Sí");
  assert.equal(formatCell(null, "text"), EMPTY);
});

test("etiquetas y modelo", () => {
  assert.equal(columnLabel("customer_name"), "Cliente");
  assert.equal(columnLabel("lost_opportunities"), "Lost opportunities");
  assert.equal(modelName("ollama", valid.model), "Ollama · qwen2.5-coder:3b");
  assert.equal(modelName("nim", "nvidia/x"), "NVIDIA NIM");
});

test("el SQL se parte por cláusulas sin tocar los textos", () => {
  assert.equal(
    formatSql("SELECT a FROM t WHERE b LIKE '%x FROM y%' GROUP BY a LIMIT 10"),
    "SELECT a\nFROM t\nWHERE b LIKE '%x FROM y%'\nGROUP BY a\nLIMIT 10",
  );
});
