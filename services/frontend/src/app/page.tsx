import { loadLlmInfo } from "@/lib/backend";

// El estado se consulta en cada visita, nunca al compilar.
export const dynamic = "force-dynamic";

export default async function Home() {
  const llm = await loadLlmInfo();

  return (
    <main>
      <h1>Odoo Data Lakehouse</h1>
      <p>Esqueleto de la Fase 1. El dashboard conversacional llega en la Fase 5.</p>
      <dl>
        <dt>API</dt>
        <dd>{llm ? "Disponible" : "Sin respuesta"}</dd>
        {llm && (
          <>
            <dt>Modelo</dt>
            <dd>
              {llm.model} ({llm.provider})
            </dd>
            <dt>Privacidad</dt>
            <dd>
              {llm.external
                ? "El esquema y la pregunta se envían a un proveedor externo"
                : "Los datos no salen del host"}
            </dd>
          </>
        )}
      </dl>
    </main>
  );
}
