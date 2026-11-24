# frontend

Dashboard conversacional: una pregunta, su respuesta y el gráfico adecuado.

| | |
|---|---|
| Tecnología | Next.js (App Router) y TypeScript; Tailwind CSS y Tremor desde la Fase 5 |
| Entrada | API de `backend` |
| Memoria | 512 MiB (`--max-old-space-size=384`) |

**Estado (Fase 1).** Esqueleto funcional: una página que muestra el estado de la API y qué modelo responde, y `GET /healthz`.

- El navegador solo habla con FastAPI, por la ruta relativa `/api` que publica Caddy. Nunca con Trino ni con Ollama.
- El servidor de Next.js consulta la API por la red interna (`BACKEND_INTERNAL_URL`).
- Desde la Fase 4, cada respuesta mostrará el SQL ejecutado.
- La variante de Tremor se decide al empezar la Fase 5 ([TD-019](../../docs/TECHNICAL_DEBT.md#td-019)). El esqueleto usa React 19.

Lint, tipos y compilación:

```bash
docker build --target build services/frontend
```
