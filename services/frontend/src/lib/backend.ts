// Solo para componentes de servidor: usa la red interna de Docker.
export type LlmInfo = {
  provider: string;
  model: string;
  external: boolean;
};

function isLlmInfo(value: unknown): value is LlmInfo {
  if (typeof value !== "object" || value === null) return false;
  const candidate = value as Record<string, unknown>;
  return (
    typeof candidate.provider === "string" &&
    typeof candidate.model === "string" &&
    typeof candidate.external === "boolean"
  );
}

/** Consulta la API por la red interna. Devuelve null si no responde. */
export async function loadLlmInfo(): Promise<LlmInfo | null> {
  const base = process.env.BACKEND_INTERNAL_URL ?? "http://backend:8000";
  try {
    const response = await fetch(`${base}/api/llm`, {
      cache: "no-store",
      signal: AbortSignal.timeout(3000),
    });
    if (!response.ok) return null;
    const data: unknown = await response.json();
    return isLlmInfo(data) ? data : null;
  } catch {
    return null;
  }
}
