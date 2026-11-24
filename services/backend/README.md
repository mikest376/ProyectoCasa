# backend

API de Text-to-SQL: convierte una pregunta en SQL, lo valida y lo ejecuta en Trino.

| | |
|---|---|
| Tecnología | Python 3.11+, FastAPI; `sqlglot` y cliente de Trino desde la Fase 4 |
| Entrada | Preguntas en lenguaje natural desde el frontend |
| Salida | Filas, SQL ejecutado y tipo de gráfico sugerido |
| Memoria | 512 MiB |

**Estado (Fase 1).** Esqueleto funcional con los proveedores de LLM. Todavía no genera ni ejecuta SQL: eso llega en la Fase 4, junto con los controles obligatorios de [CLAUDE.md](../../CLAUDE.md#reglas-de-arquitectura).

| Ruta | Respuesta |
|---|---|
| `GET /api/health` | Estado del proceso |
| `GET /api/llm` | Proveedor, modelo y si el prompt sale del host |
| `GET /docs` | Documentación, solo en `127.0.0.1`: Caddy únicamente publica `/api` |

## Proveedores de LLM

Se eligen con `LLM_PROVIDER`:

| Valor | Modelo por defecto | El prompt sale del host |
|---|---|---|
| `ollama` (por defecto) | `qwen2.5-coder:3b-instruct-q4_K_M` | No |
| `nim` | `nvidia/nemotron-3.5-lightning-30b-a3b` | **Sí**, a la API de NVIDIA ([TD-021](../../docs/TECHNICAL_DEBT.md#td-021)) |

`nim` necesita `NIM_API_KEY`; sin ella la API no arranca. La clave no aparece en logs, errores ni respuestas.

En la Fase 4 las llamadas al modelo se trazarán en Langfuse con el SDK de Python v2, la versión compatible con el servidor instalado ([TD-023](../../docs/TECHNICAL_DEBT.md#td-023)).

Lint, tipos y pruebas:

```bash
docker build --target test services/backend
```
