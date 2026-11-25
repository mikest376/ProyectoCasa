// Solo para componentes de servidor: usa la red interna de Docker.
import {
  type ExampleGroup,
  type LlmInfo,
  parseExamples,
  parseLlm,
  parseTables,
  type TableInfo,
} from "./api.ts";

async function get(path: string): Promise<unknown> {
  const base = process.env.BACKEND_INTERNAL_URL ?? "http://backend:8000";
  try {
    const response = await fetch(`${base}${path}`, {
      cache: "no-store",
      signal: AbortSignal.timeout(4000),
    });
    return response.ok ? await response.json() : null;
  } catch {
    return null;
  }
}

export type Context = {
  llm: LlmInfo | null;
  examples: ExampleGroup[];
  tables: TableInfo[];
};

/** Qué modelo responde, qué se puede preguntar y ejemplos. Vacío si la API no está. */
export async function loadContext(): Promise<Context> {
  const [llm, examples, tables] = await Promise.all([
    get("/api/llm"),
    get("/api/examples"),
    get("/api/tables"),
  ]);
  return {
    llm: parseLlm(llm),
    examples: parseExamples(examples),
    tables: parseTables(tables),
  };
}
