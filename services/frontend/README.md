# frontend

Dashboard conversacional: una pregunta, su respuesta y el gráfico adecuado.

| | |
|---|---|
| Tecnología | Next.js (App Router), TypeScript, Tailwind CSS y Tremor |
| Entrada | API de `backend` |
| Memoria | 512 MiB (`--max-old-space-size=384`) |
| Fase | 5 |

- El navegador solo habla con FastAPI, nunca con Trino ni con Ollama.
- Cada respuesta muestra el SQL ejecutado.
- La variante de Tremor se decide al empezar la fase ([TD-019](../../docs/TECHNICAL_DEBT.md#td-019)).

Todavía no hay código ni Dockerfile.
