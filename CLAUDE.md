# CLAUDE.md

Reglas obligatorias para agentes y desarrolladores de este repositorio. Si una tarea exige incumplir una regla, detente y pide aprobación antes de escribir código.

## Proyecto

Lakehouse local sobre Odoo 17 que responde preguntas en lenguaje natural:

`Odoo → Debezium CDC → Kafka (KRaft) → worker Python → MinIO (Delta Lake: bronze/silver/gold) + Unity Catalog OSS → Trino + Ollama → FastAPI → Next.js`

- Diseño y versiones: [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md)
- Deuda técnica: [docs/TECHNICAL_DEBT.md](docs/TECHNICAL_DEBT.md)
- Fases y criterios de salida: [README.md](README.md#hoja-de-ruta)

**Fase actual: 0 (base arquitectónica).** `services/*` todavía no tiene código. No adelantes trabajo de fases posteriores si no se pide.

## Reglas de arquitectura

1. **Stack cerrado.** No se añade Spark, Flink, Zookeeper, Airflow, Hive Metastore ni otra base de datos sin aprobación explícita.
2. **Flujo en un solo sentido.** Ninguna capa escribe en la anterior. Odoo y su PostgreSQL son de solo lectura para todo lo demás: Debezium usa un rol propio con `REPLICATION` y `SELECT`.
3. **Un único escritor Delta.** Solo `medallion-worker` escribe en MinIO. Trino, FastAPI y el frontend solo leen. La única excepción es el metastore de Trino (TD-002).
4. **Bronze captura todo y no se modifica.** Todas las tablas del esquema `public`, sin `table.include.list`, en modo append-only. El evento de Debezium se guarda crudo.
5. **Silver y Gold solo contienen los dominios core:**

   | Dominio | Tablas de Odoo |
   |---|---|
   | Ventas | `sale_order`, `sale_order_line` |
   | Facturación | `account_move`, `account_move_line` |
   | Inventario | `stock_move`, `stock_quant`, `product_product`, `product_template` |
   | CRM | `crm_lead` |
   | Maestros | `res_partner`, `res_users` |

6. **Formato Delta Lake en las tres capas**, escrito con `deltalake` (delta-rs) y `polars`.
7. **Unity Catalog OSS es el catálogo de referencia.** Toda tabla Silver o Gold se registra como externa, con descripción de tabla y de columnas: el prompt de Text-to-SQL se construye con ellas.
8. **Text-to-SQL solo consulta Gold.** El SQL del modelo es una entrada no confiable y pasa todos estos controles:
   - Una sola sentencia, analizada como AST con `sqlglot` (dialecto `trino`). Prohibido validar con expresiones regulares.
   - Solo `SELECT` o `WITH ... SELECT`. Se rechaza DDL, DML, `CALL`, `SET`, `EXECUTE`, funciones de tabla, `system` e `information_schema`.
   - Todas las tablas pertenecen a `lakehouse.gold`, según una lista de permitidas.
   - `LIMIT` y tiempo máximo impuestos por el backend.
   - Trino aplica el mismo límite con reglas de acceso propias: usuario `text2sql`, solo `SELECT` sobre Gold.
9. **Los datos no salen del host.** El LLM es local. Ninguna API externa recibe datos, esquemas ni preguntas.
10. **Puertos solo en `127.0.0.1`** mientras no haya autenticación (TD-009).
11. **Versiones fijadas.** Nada de `latest` sin digest. Al cambiar una versión, comprueba que el tag existe y actualiza `docs/ARCHITECTURE.md`.
12. **Solo datos de demostración** hasta pagar TD-004.

## Presupuesto de memoria

WSL dispone de 22528 MiB. Los límites son fijos en `docker-compose.yml` y no se parametrizan por entorno.

| Servicio | Límite (MiB) | Ajuste interno | Perfil |
|---|---:|---|---|
| `odoo-db` | 1024 | `shared_buffers=256MB`, 60 conexiones | `source` |
| `odoo` | 1536 | `--workers=0` | `source` |
| `kafka` | 1280 | heap 768 MiB | `streaming` |
| `kafka-connect` | 1280 | heap 768 MiB | `streaming` |
| `minio` | 1024 | sin ajuste | `lakehouse` |
| `unity-catalog` | 768 | heap 512 MiB | `lakehouse` |
| `trino` | 5120 | heap 4096 MiB, 1500 MB por consulta | `query` |
| `ollama` | 5120 | capas que no caben en VRAM | `ai` |
| `medallion-worker` | 1536 | `POLARS_MAX_THREADS=4` | `apps` |
| `backend` | 512 | un proceso | `apps` |
| `frontend` | 512 | `--max-old-space-size=384` | `apps` |
| **Total** | **19712** | | |
| **Reserva** (kernel, Docker, editor) | **2816** | | |

`minio-init` (128 MiB) es de un solo uso y no cuenta en el total.

- Tope del total: **20480 MiB**. La reserva nunca baja de 2048 MiB.
- Todo servicio declara `deploy.resources.limits.memory`.
- Heap de JVM: como máximo el 80 % del límite del contenedor, siempre explícito.
- Para subir un límite, baja otro o consume holgura, y actualiza esta tabla en el mismo commit.
- Comprobación obligatoria al tocar el compose:

  ```bash
  docker compose --env-file .env.example --profile "*" config --format json | python3 -c '
  import json, sys
  services = json.load(sys.stdin)["services"]
  total = sum(int(s["deploy"]["resources"]["limits"]["memory"]) for n, s in services.items() if n != "minio-init") // 2**20
  print(f"{total} MiB (tope 20480)")
  sys.exit(total > 20480)'
  ```

## Presupuesto de VRAM

GPU: GTX 1060 con **6144 MiB** medidos con `nvidia-smi`. Es la única GPU y también dibuja el escritorio de Windows.

| Concepto | MiB |
|---|---:|
| Pesos de `qwen2.5-coder:7b-instruct-q4_K_M` | 4460 |
| Caché KV (contexto 4096, `q8_0`) | 120 |
| Buffers de cómputo (estimado) | 400–900 |
| **Necesario para el modelo completo en GPU** | **5000–5500** |
| Uso del escritorio al inspeccionar el equipo | 2440 |

El modelo aprobado no cabe entero junto al escritorio (TD-001): Ollama pasa capas a la CPU.

- Solo Ollama usa la GPU: ningún otro contenedor reserva dispositivos NVIDIA.
- `OLLAMA_MAX_LOADED_MODELS=1`, `OLLAMA_NUM_PARALLEL=1`, contexto de 4096 tokens como máximo.
- El modelo se elige con `OLLAMA_MODEL`. No se cambia el valor por defecto sin cerrar TD-001.

## Convenciones de código

**Idioma.** Documentación y comentarios en español. Identificadores, nombres de tablas y columnas, y mensajes de commit en inglés.

**Python 3.11+** (`medallion-worker`, `backend`)
- `pyproject.toml` con dependencias fijadas en un lockfile de `uv`.
- `ruff` (lint y formato), `mypy --strict` y `pytest`.
- Configuración con `pydantic-settings`: ningún `os.environ` disperso.
- Logs estructurados en JSON, sin `print`. No se registran credenciales ni filas de datos.
- Importes con `Decimal`, nunca `float`. Fechas con zona horaria, en UTC.

**SQL**
- Dialecto Trino, `snake_case`, palabras clave en mayúsculas.
- Silver conserva el nombre de la tabla de Odoo. Gold usa `dim_`, `fact_` y `agg_`.

**TypeScript** (`frontend`)
- `strict: true`, sin `any`. App Router con Server Components por defecto.
- Tailwind y Tremor para la interfaz. El navegador solo habla con FastAPI.

**Docker**
- Imagen fijada, límite de memoria, healthcheck, perfil y puerto en `127.0.0.1`.
- Dockerfile multietapa y usuario sin privilegios en los servicios propios.
- Los healthchecks no arrancan una JVM adicional.

**Git**
- Conventional Commits en inglés (`feat:`, `fix:`, `docs:`, `chore:`). Sin `push --force` a `main`.
- Secretos solo en `.env`. Una variable nueva se añade a `.env.example` en el mismo commit, sin valor real.

## Flujo obligatorio de deuda técnica

Es deuda técnica cualquier atajo, límite conocido, versión sin soporte, control de seguridad aplazado, prueba omitida o desviación de este documento.

1. **Antes de escribir el atajo**, busca si ya existe una entrada en `docs/TECHNICAL_DEBT.md`.
2. Si no existe, créala con la plantilla y el siguiente `TD-NNN` libre. Los identificadores no se reutilizan.
3. Marca el punto exacto del código: `# TODO(TD-NNN): motivo`. No se admite un `TODO` sin identificador.
4. Entrada y atajo van **en el mismo commit**, con `Debt: TD-NNN` al final del mensaje.
5. Severidad Crítica, o cualquier excepción a las reglas de arquitectura: pide aprobación antes de continuar.
6. **Al pagarla**, cambia el estado a `Pagada` con fecha y commit, y elimina los `TODO`. La entrada no se borra.
7. Al cerrar cada fase, revisa todas las entradas abiertas y actualiza la fecha de revisión.

## Antes de dar una tarea por terminada

- [ ] `docker compose --env-file .env.example --profile "*" config --quiet` termina sin errores.
- [ ] El presupuesto de memoria se cumple y la tabla está al día.
- [ ] Lint, tipos y pruebas del servicio modificado pasan.
- [ ] Sin secretos, datos reales ni `TODO` sin `TD-NNN`.
- [ ] README, arquitectura y deuda técnica reflejan el cambio.
- [ ] Se informa de lo que **no** se ha probado.
