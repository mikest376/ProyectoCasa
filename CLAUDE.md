# CLAUDE.md

Reglas obligatorias para agentes y desarrolladores de este repositorio. Si una tarea exige incumplir una regla, detente y pide aprobación antes de escribir código.

## Proyecto

Lakehouse local sobre Odoo 17 que responde preguntas en lenguaje natural:

`Odoo → Debezium CDC → Kafka (KRaft) → worker Python → MinIO (Delta Lake: bronze/silver/gold) + Unity Catalog OSS → Trino + LLM → FastAPI → Next.js → Caddy`

- Diseño y versiones: [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md)
- Deuda técnica: [docs/TECHNICAL_DEBT.md](docs/TECHNICAL_DEBT.md)
- Preparación del equipo: [docs/SETUP_WSL.md](docs/SETUP_WSL.md)
- Fases y criterios de salida: [README.md](README.md#hoja-de-ruta)

**Fase actual: 5 (dashboard conversacional).** El recorrido completo funciona: de Odoo a la respuesta en el navegador. No adelantes trabajo de fases posteriores si no se pide.

## Reglas de arquitectura

1. **Stack cerrado.** Además del flujo anterior, están aprobados Caddy (entrada), Langfuse 2 (observabilidad del LLM) y NVIDIA NIM (LLM opcional). No se añade Spark, Flink, Zookeeper, Airflow, Hive Metastore ni otra base de datos sin aprobación explícita.
2. **Flujo en un solo sentido.** Ninguna capa escribe en la anterior. Debezium usa un rol propio con `REPLICATION` y `SELECT`. Odoo usa un rol propietario de su base, nunca el superusuario.
3. **Un único escritor Delta.** Solo el código de `medallion-worker` escribe en MinIO, en tres procesos: ingesta (Bronze), transformación (Silver y Gold) y mantenimiento (compactación de Bronze, con usuario propio). Trino, FastAPI y el frontend solo leen. La única excepción es el metastore de Trino (TD-002, opción A): ficheros en MinIO, con registro de tablas desde el worker. Cada servicio usa su propio usuario de MinIO con la política mínima (`infra/minio/policies`); las credenciales raíz solo las reciben `minio` y `minio-init`.
4. **Bronze captura todas las tablas y no se modifica.** Todas las del esquema `public`, sin `table.include.list`, en modo append-only. El evento de Debezium se guarda crudo. La compactación y el `VACUUM` (TD-007) reescriben ficheros, nunca filas.
5. **Los secretos no se capturan.** `infra/debezium/column-exclude.list` excluye contraseñas, claves, tokens y credenciales de correo. Ningún conector se registra sin que `scripts/cdc.py check` termine bien, y se repite tras instalar módulos de Odoo.
6. **Silver y Gold solo contienen los dominios core:**

   | Dominio | Tablas de Odoo |
   |---|---|
   | Ventas | `sale_order`, `sale_order_line` |
   | Facturación | `account_move`, `account_move_line` |
   | Inventario | `stock_move`, `stock_quant`, `product_product`, `product_template` |
   | CRM | `crm_lead` |
   | Maestros | `res_partner`, `res_users` |

7. **Formato Delta Lake en las tres capas**, escrito con `deltalake` (delta-rs) y `polars`.
8. **Unity Catalog OSS es el catálogo de referencia.** Toda tabla Silver o Gold se registra como externa, con descripción de tabla y de columnas: el prompt de Text-to-SQL se construye con ellas.
9. **Text-to-SQL solo consulta Gold.** El SQL del modelo es una entrada no confiable y pasa todos estos controles:
   - Una sola sentencia, analizada como AST con `sqlglot` (dialecto `trino`). Prohibido validar con expresiones regulares.
   - Solo `SELECT` o `WITH ... SELECT`. Se rechaza DDL, DML, `CALL`, `SET`, `EXECUTE`, funciones de tabla, `system` e `information_schema`.
   - Todas las tablas pertenecen a `lakehouse.gold`, según una lista de permitidas.
   - `LIMIT` y tiempo máximo impuestos por el backend.
   - Trino aplica el mismo límite con reglas de acceso propias: usuario `text2sql`, solo `SELECT` sobre Gold.
10. **Los datos no salen del host por defecto.** `LLM_PROVIDER=ollama`. Con `nim`, solo pueden salir los metadatos de Gold y la pregunta, nunca filas de resultados. Los términos de prueba de NVIDIA prohíben datos personales y confidenciales y permiten usar lo enviado para mejorar sus modelos (TD-021). La interfaz indica siempre qué proveedor responde.
11. **Puertos solo en `127.0.0.1`.** La única excepción es Caddy (80, 443 y 8443), durante la defensa del TFG y con `CADDY_BIND_IP` cambiado a mano. Ver [exposición](#exposición-externa).
12. **Redes segmentadas.** Caddy solo comparte red con Odoo, el frontend y FastAPI. Odoo no ve el plano de datos. Ningún servicio se añade a `edge-odoo` o `edge-app` sin aprobación.
13. **Versiones fijadas.** Nada de `latest` sin digest. Al cambiar una versión, comprueba que el tag existe y actualiza `docs/ARCHITECTURE.md`.
14. **Solo datos de demostración** mientras sigan abiertas TD-009, TD-012 o la parte de datos personales de TD-004.

## Exposición externa

Solo se publican Odoo y el frontend (con `/api` hacia FastAPI). Todo lo demás es interno.

- `CADDY_BIND_IP` vale `127.0.0.1` en `.env.example` y no se cambia en el repositorio.
- `basic_auth` es obligatorio en todos los sitios del Caddyfile. No se añaden rutas sin él.
- Siguen bloqueados el gestor de bases de datos, `/xmlrpc`, `/jsonrpc`, el registro y la recuperación de contraseña de Odoo.
- Antes de exponer, `scripts/check_exposure.py` debe terminar sin fallos.
- Un agente **nunca** cambia `CADDY_BIND_IP`, reglas del router o del firewall, ni arranca Caddy fuera de `127.0.0.1`: lo hace una persona.
- El procedimiento y los riesgos están en el [README](README.md#exposición-para-la-defensa-del-tfg), TD-009 y TD-022.

## Presupuesto de memoria

WSL dispone de 16384 MiB. Los límites son fijos en `docker-compose.yml` y no se parametrizan por entorno.

| Servicio | Límite (MiB) | Ajuste interno | Perfil |
|---|---:|---|---|
| `odoo-db` | 512 | `shared_buffers=256MB`, 60 conexiones | `source` |
| `odoo` | 768 | `workers=0` | `source` |
| `kafka` | 1024 | heap 768 MiB | `streaming` |
| `kafka-connect` | 1536 | heap 768 MiB | `streaming` |
| `minio` | 640 | sin ajuste | `lakehouse` |
| `unity-catalog` | 640 | heap 512 MiB | `lakehouse` |
| `trino` | 3072 | heap 2048 MiB, 800 MB por consulta | `query` |
| `ollama` | 2560 | capas que no caben en VRAM | `ai` |
| `medallion-worker` | 384 | `POLARS_MAX_THREADS=4` | `apps` |
| `medallion-transform` | 512 | `POLARS_MAX_THREADS=4` | `apps` |
| `bronze-maintenance` | 384 | `POLARS_MAX_THREADS=2` | `apps` |
| `backend` | 384 | un proceso | `apps` |
| `frontend` | 256 | `--max-old-space-size=160` | `apps` |
| `langfuse` | 640 | `--max-old-space-size=512` | `observability` |
| `langfuse-db` | 192 | sin ajuste | `observability` |
| `caddy` | 128 | sin ajuste | `caddy` |
| **Total** | **13632** | | |
| **Reserva** (kernel, Docker, editor) | **2752** | | |

Los servicios de un solo uso (`*-init`) no cuentan en el total. `odoo-init` se ejecuta con `odoo` detenido.

- Tope del total: **14336 MiB**. La reserva nunca baja de 2048 MiB.
- Todo servicio declara `deploy.resources.limits.memory`.
- Heap de JVM: como máximo el 80 % del límite del contenedor, siempre explícito.
- Para subir un límite, baja otro o consume holgura, y actualiza esta tabla en el mismo commit.
- `scripts/validate.sh` comprueba el tope.

## Presupuesto de VRAM

GPU: GTX 1060 con **6144 MiB** medidos con `nvidia-smi`. Es la única GPU y también dibuja el escritorio de Windows, que ocupaba unos 2440 MiB: quedan unos 3600 MiB.

| Modelo | Pesos (MiB) | Total estimado (MiB) | ¿Cabe? |
|---|---:|---:|---|
| `qwen2.5-coder:3b-instruct-q4_K_M` (por defecto) | 1840 | 2300–2700 | Sí |
| `qwen2.5-coder:7b-instruct-q4_K_M` | 4460 | 5000–5500 | No entero: usa CPU (TD-001) |

- Solo Ollama usa la GPU: ningún otro contenedor reserva dispositivos NVIDIA.
- `OLLAMA_MAX_LOADED_MODELS=1`, `OLLAMA_NUM_PARALLEL=1`, contexto de 4096 tokens como máximo.

## Espacio en disco

- Antes de descargar imágenes o modelos, comprueba el espacio de la unidad de **Windows** donde Docker guarda sus datos (`scripts/check_env.sh`). El `df` de WSL no sirve: su disco virtual anuncia 1 TB.
- Ninguna descarga deja la unidad del sistema por debajo de 10 GB libres.

## Convenciones de código

**Idioma.** Documentación y comentarios en español. Identificadores, nombres de tablas y columnas, y mensajes de commit en inglés.

**Python 3.11+** (`medallion-worker`, `backend`)
- `pyproject.toml` con dependencias fijadas en un lockfile de `uv`.
- `ruff` (lint y formato), `mypy --strict` y `pytest`.
- Configuración con `pydantic-settings`: ningún `os.environ` disperso. Las claves son `SecretStr`.
- Logs estructurados en JSON, sin `print`. No se registran credenciales, prompts ni filas de datos.
- Importes con `Decimal`, nunca `float`. Fechas con zona horaria, en UTC.

**Scripts** (`scripts/`)
- Bash con `set -euo pipefail` o Python solo con biblioteca estándar.
- Repetibles. Ningún secreto en argumentos ni en la salida. Los de diagnóstico solo leen.

**SQL**
- Dialecto Trino, `snake_case`, palabras clave en mayúsculas.
- Silver conserva el nombre de la tabla de Odoo. Gold usa `dim_`, `fact_` y `agg_`.

**TypeScript** (`frontend`)
- `strict: true`, sin `any`. App Router con Server Components por defecto.
- Tailwind CSS 4 y componentes propios al estilo de Tremor Raw (TD-019), con Recharts para los gráficos. El navegador solo habla con FastAPI.

**Docker**
- Imagen fijada, límite de memoria, healthcheck, perfil, red explícita y puerto en `127.0.0.1`.
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

- [ ] `scripts/validate.sh` termina sin fallos (compose, presupuesto, lint, tipos, pruebas, Caddy, CDC, secretos).
- [ ] Si cambia el worker, MinIO o Kafka, `scripts/test_integration.sh` termina sin fallos.
- [ ] Las tablas de presupuesto están al día.
- [ ] Sin secretos, datos reales ni `TODO` sin `TD-NNN`.
- [ ] README, arquitectura y deuda técnica reflejan el cambio.
- [ ] Se informa de lo que **no** se ha probado.
