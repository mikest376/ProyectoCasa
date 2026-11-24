# backend

API de Text-to-SQL: convierte una pregunta en SQL, lo valida y lo ejecuta en Trino.

| | |
|---|---|
| Tecnología | Python 3.11+, FastAPI, `sqlglot`, clientes de Ollama y Trino |
| Entrada | Preguntas en lenguaje natural desde el frontend |
| Salida | Filas, SQL ejecutado y tipo de gráfico sugerido |
| Memoria | 512 MiB |
| Fase | 4 |

Solo se ejecutan sentencias `SELECT` o `WITH` sobre `lakehouse.gold`. Los controles obligatorios están en [CLAUDE.md](../../CLAUDE.md#reglas-de-arquitectura) y el flujo, en [ARCHITECTURE.md](../../docs/ARCHITECTURE.md#text-to-sql). Todavía no hay código ni Dockerfile.
