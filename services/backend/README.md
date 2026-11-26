# backend

API de Text-to-SQL: convierte una pregunta en SQL, lo valida y lo ejecuta en Trino.

| | |
|---|---|
| Tecnología | Python 3.11+, FastAPI, `sqlglot` |
| Entrada | Preguntas en lenguaje natural desde el frontend |
| Salida | Filas, SQL ejecutado, gráfico sugerido, tiempos y modelo que ha respondido |
| Memoria | 384 MiB |

| Módulo | Función |
|---|---|
| `gold_catalog.py` | Esquema de Gold, leído de Unity Catalog |
| `retrieval.py`, `shots.py`, `prompt.py` | Tabla probable, ejemplos parecidos y prompt |
| `sql_guard.py` | Validación del SQL como AST: lo que se ejecuta sale del árbol validado |
| `trino_client.py` | Ejecución de solo lectura, con límite de filas y cancelación por tiempo |
| `charts.py` | Gráfico que corresponde al resultado |
| `pipeline.py` | El recorrido completo, con un intento de corrección |
| `tracing.py` | Trazas en Langfuse, sin filas |
| `evals/` | 100 preguntas con su SQL de referencia |

| Ruta | Respuesta |
|---|---|
| `GET /api/health` | Estado del proceso |
| `GET /api/llm` | Proveedor, modelo y si el prompt sale del host |
| `GET /api/examples` | Preguntas de ejemplo por dominio |
| `GET /api/tables` | Tablas y columnas de Gold, sin datos |
| `POST /api/query` | Responde una pregunta: `{"question": "..."}` |
| `GET /docs` | Documentación, solo en `127.0.0.1`: Caddy únicamente publica `/api` |

## Proveedores de LLM

Se eligen con `LLM_PROVIDER`:

| Valor | Modelo por defecto | El prompt sale del host |
|---|---|---|
| `ollama` (por defecto) | `qwen2.5-coder:3b-instruct-q4_K_M` | No |
| `nim` | `nvidia/nemotron-3.5-lightning-30b-a3b` | **Sí**, a la API de NVIDIA ([TD-021](../../docs/TECHNICAL_DEBT.md#td-021)) |

`nim` necesita `NIM_API_KEY`; sin ella la API no arranca. La clave no aparece en logs, errores ni respuestas.

Los errores de `POST /api/query` llevan `code`, `message` y, si lo hubo, el SQL propuesto: `sql_rejected`, `query_failed`, `bad_output` (422), `busy` (429), `llm_unavailable` (502), `catalog_unavailable` y `trino_unavailable` (503), `timeout` (504).

## Pruebas y evaluación

```bash
docker build --target test services/backend                                        # lint, tipos y pruebas
docker compose exec backend python -m lakehouse_api.evals --split test             # precisión con el modelo real
```
