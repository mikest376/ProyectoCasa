# Registro de deuda técnica

Deuda asumida de forma consciente para el MVP. Lo que no está aquí no es deuda aceptada: es un defecto.

El flujo para registrar y pagar deuda es obligatorio y está en [CLAUDE.md](../CLAUDE.md#flujo-obligatorio-de-deuda-técnica). La plantilla está al final de este documento.

**Última revisión:** 2026-11-25 (Fase 2, Bronze)

## Criterios

| Severidad | Significado |
|---|---|
| Crítica | Pérdida de datos o fuga de información posible hoy. Bloquea el avance. |
| Alta | Bloquea una fase, o bloquea el uso con datos reales. |
| Media | Degrada fiabilidad, rendimiento o mantenimiento. |
| Baja | Molestia o mejora pendiente. |

| Estado | Significado |
|---|---|
| Pendiente de decisión | Hay alternativas y falta elegir una. |
| Abierta | Plan definido, sin pagar. |
| Mitigada | El riesgo principal está resuelto y probado. Queda una parte, indicada en el plan de pago. |
| Aceptada | Se mantiene durante todo el MVP. |
| Pagada | Resuelta, con fecha y commit. |

## Resumen

| ID | Deuda | Severidad | Estado | Se paga en |
|---|---|---|---|---|
| [TD-001](#td-001) | El modelo 7B no cabe completo en la GPU (6 GB) | Media | Mitigada | Fase 4 |
| [TD-002](#td-002) | Trino no lee Unity Catalog OSS para tablas Delta | Media | Pagada | 2026-11-25 |
| [TD-003](#td-003) | MinIO Community archivado, con imagen de un tercero | Media | Aceptada | Después del MVP |
| [TD-004](#td-004) | La captura global copia datos sensibles a Kafka y Bronze | Alta | Mitigada | Fase 6 |
| [TD-005](#td-005) | La captura global puede bloquear escrituras en Odoo | Media | Mitigada | Fase 6 |
| [TD-006](#td-006) | Retención de WAL ligada al slot de replicación | Media | Mitigada | Fase 6 |
| [TD-007](#td-007) | Bronze sin particionamiento avanzado ni mantenimiento | Media | Pagada | 2026-11-25 |
| [TD-008](#td-008) | Trino en un solo nodo | Media | Aceptada | Después del MVP |
| [TD-009](#td-009) | Sin autenticación JWT ni autorización | Alta | Abierta | Fase 6 |
| [TD-010](#td-010) | Kafka con un solo broker y sin réplicas | Media | Aceptada | Después del MVP |
| [TD-011](#td-011) | Eventos JSON sin registro de esquemas | Media | Mitigada | Fase 6 |
| [TD-012](#td-012) | Odoo 17 fuera de soporte estándar | Alta | Abierta | Después del MVP |
| [TD-013](#td-013) | Unity Catalog en modo desarrollo | Media | Mitigada | Fase 6 |
| [TD-014](#td-014) | Credenciales raíz compartidas y tráfico interno sin TLS | Media | Mitigada | Fase 6 |
| [TD-015](#td-015) | Sin observabilidad ni alertas de infraestructura | Media | Abierta | Fase 6 |
| [TD-016](#td-016) | Sin integración continua | Media | Pagada | 2026-11-25 |
| [TD-017](#td-017) | Sin copias de seguridad | Media | Abierta | Fase 6 |
| [TD-018](#td-018) | Text-to-SQL sin conjunto de evaluación | Media | Abierta | Fase 4 |
| [TD-019](#td-019) | Tremor con poco mantenimiento | Media | Pendiente de decisión | Fase 5 |
| [TD-020](#td-020) | Servicios sin probar en ejecución | Media | Abierta | Fases 3 y 4 |
| [TD-021](#td-021) | NVIDIA NIM envía datos a un tercero | Alta | Abierta | Fase 4 |
| [TD-022](#td-022) | Exposición a Internet de la demo del TFG | Alta | Aceptada | Después del MVP |
| [TD-023](#td-023) | Langfuse 2 sin parches de seguridad | Media | Abierta | Fase 6 |
| [TD-024](#td-024) | Imágenes fijadas por tag, no por digest | Baja | Abierta | Fase 6 |
| [TD-025](#td-025) | El disco del proyecto y de Docker anuncia un fallo | Crítica | Pagada | 2026-11-25 |
| [TD-026](#td-026) | La memoria de WSL puede llenar la unidad del sistema | Media | Mitigada | Fase 6 |
| [TD-027](#td-027) | PostgreSQL no arranca solo al reiniciar Docker Desktop | Baja | Pagada | 2026-11-25 |

## Detalle

<a id="td-001"></a>
### TD-001 · El modelo 7B no cabe completo en la GPU (6 GB)

- **Contexto.** El diseño suponía 8 GB de VRAM. `nvidia-smi` informa de 6144 MiB, y la misma GPU dibuja el escritorio de Windows, que deja unos 3,6 GB libres. `qwen2.5-coder:7b-instruct-q4_K_M` pesa 4,68 GB y necesita entre 5 y 5,5 GB en ejecución.
- **Riesgo.** Un modelo más pequeño puede generar peor SQL. Además, la GTX 1060 (Pascal) está en la última rama de controladores que la admite (R580); Ollama [exige el 570 o superior](https://docs.ollama.com/gpu) para esta generación.
- **Mitigación actual.** El modelo local por defecto es `qwen2.5-coder:3b-instruct-q4_K_M` (1,93 GB), que cabe en la VRAM libre. Como alternativa de más calidad, `LLM_PROVIDER=nim` usa NVIDIA NIM ([TD-021](#td-021)). La GPU es visible desde los contenedores (comprobado el 2026-11-24 y el 2026-11-25: controlador 582.66, 4164 MiB libres).
- **Plan de pago.** En la Fase 4, medir con el conjunto de TD-018 la precisión del SQL y el tiempo de respuesta de 3B, 7B con capas en CPU y NIM, y fijar el modelo por defecto con esos datos.
- **Disparador.** Precisión de ejecución del 3B inferior al 80 %, o respuesta p95 superior a 30 s.

<a id="td-002"></a>
### TD-002 · Trino no lee Unity Catalog OSS para tablas Delta

- **Contexto.** Verificado el 2026-11-24:
  - Trino [retiró el soporte de Unity Catalog por HTTP](https://trino.io/ecosystem/data-lake) en la versión 473.
  - El conector Delta Lake de Trino 484 [documenta solo los metastores `thrift` y `glue`](https://trino.io/docs/current/object-storage/metastores.html). `file` sigue existiendo en el código, sin documentar.
  - La [integración oficial de Unity Catalog con Trino](https://github.com/unitycatalog/unitycatalog/blob/v0.7.0/docs/integrations/unity-catalog-trino.md) usa Iceberg REST y requiere tablas Delta con UniForm. delta-rs [no permite activarlo](https://github.com/delta-io/delta-rs/issues/3299).
- **Riesgo.** Dos registros de metadatos que pueden divergir, y dependencia de una opción de Trino sin documentar.
- **Decisión.** **Opción A, confirmada:** `hive.metastore=file` sobre MinIO, con registro de tablas desde el worker (`system.register_table`). No añade contenedores. Descartadas para el MVP: Hive Metastore (contenedor, base de datos y unos 768 MiB más) y Gold en Iceberg (cambia la decisión de usar Delta Lake).
- **Pago.** Pagada el 2026-11-25 (commit `999847d`). `medallion-transform` registra las 11 tablas Silver y los 14 modelos Gold en los dos catálogos a partir de las mismas definiciones, y los repara cada 10 minutos. `python -m medallion_worker catalogs` compara Trino, Unity Catalog y lo declarado (tablas, columnas, tipos, ubicación y descripción): 25 tablas, 0 diferencias, también tras reiniciar. La comparación forma parte de `scripts/test_integration.sh`.
- **Queda como límite.** El metastore `file` sigue sin documentar: antes de actualizar Trino, repetir la integración.
- **Disparador.** Una versión de Trino elimina el metastore `file`. En ese caso, Hive Metastore.

<a id="td-003"></a>
### TD-003 · MinIO Community archivado, con imagen de un tercero

- **Contexto.** El repositorio [`minio/minio`](https://github.com/minio/minio) está archivado (último cambio: 2026-04-24). El 2026-11-24, `minio/minio` y `minio/mc` devuelven 404 en Docker Hub, y Quay rechaza la descarga anónima (401).
- **Riesgo.** El nivel gratuito de Chainguard solo ofrece `latest`: puede retirar la imagen o eliminar el digest fijado.
- **Decisión.** **Aceptada para el MVP:** la [imagen pública de Chainguard](https://www.chainguard.dev/fr-FR/unchained/secure-and-free-minio-chainguard-containers), fijada por digest.
- **Mitigación actual.** Probada en ejecución el 2026-11-24: arranque, creación de buckets y, con delta-rs 1.7, append, MERGE y dos escritores concurrentes sin pérdida de filas (`aws_conditional_put=etag`). Los servicios usan solo la API S3.
- **Copia local.** `scripts/backup_minio_image.sh` guarda la imagen fijada (60 MiB comprimida, 236 MiB en disco) fuera del repositorio, con su SHA-256. Probado el 2026-11-25: rechaza una copia dañada y, con la imagen local eliminada, la restaura y MinIO arranca con `--pull never`. Está en el mismo SSD que Docker: protege de la retirada del digest, no de un fallo de disco ([TD-017](#td-017)).
- **Plan de pago.** Después del MVP, evaluar SeaweedFS, Garage y RustFS. Trino solo prueba su compatibilidad con AWS S3 y MinIO.
- **Disparador.** El digest deja de descargarse, o aparece una vulnerabilidad crítica sin parche.

<a id="td-004"></a>
### TD-004 · La captura global copia datos sensibles a Kafka y Bronze

- **Contexto.** Capturar todas las tablas incluye hashes de contraseñas, claves de API, tokens, parámetros con secretos, credenciales de correo y datos personales.
- **Riesgo.** Secretos y datos personales en claro en los topics y en una capa que, por diseño, no se modifica: borrarlos después es costoso.
- **Mitigación actual.** **Implementada y probada antes del primer snapshot:**
  - `infra/debezium/column-exclude.list` excluye contraseñas, claves, tokens, credenciales SMTP e IMAP, parámetros del sistema, cuerpos de correo (llevan enlaces con token) y contenido de adjuntos. La captura sigue siendo de todas las tablas. Se excluye en vez de enmascarar porque el hash de un secreto corto se puede atacar.
  - `scripts/cdc.py check` compara la lista con el esquema real y **bloquea el alta del conector** si una columna sospechosa quedaría sin excluir. En la primera ejecución detectó dos secretos que no estaban en la lista.
  - Comprobado con Odoo 17 y datos de demostración: 59 de 4892 columnas excluidas (5 en tablas core) y ningún secreto en los 44 692 mensajes publicados.
- **Plan de pago.** Quedan los **datos personales** (`res_partner`, usuarios, contactos de CRM), necesarios para el análisis. En la Fase 6, definir retención y borrado en Kafka y Bronze.
- **Disparador.** **Bloquea la carga de datos personales reales.** Repetir la comprobación tras instalar módulos de Odoo.

<a id="td-005"></a>
### TD-005 · La captura global puede bloquear escrituras en Odoo

- **Contexto.** PostgreSQL rechaza `UPDATE` y `DELETE` en tablas publicadas que no tienen clave primaria ni `REPLICA IDENTITY`. Además, cada tabla con datos genera un topic.
- **Riesgo.** Errores de escritura en el ERP, y presión de memoria en Kafka y Connect.
- **Mitigación actual.** `scripts/cdc.py prepare` asigna `REPLICA IDENTITY FULL` a las tablas que lo necesitan. Probado: con los módulos del MVP, las 481 tablas tienen clave primaria; en una tabla de prueba sin ella se reprodujo el error y el script lo resolvió. Con unos 200 topics, Kafka usa 673 MiB y Connect, hasta 951 MiB de sus 1280 (74 %), medido el 2026-11-24 tras el snapshot inicial.
- **Pruebas.** `scripts/tests/test_cdc.py` ejecuta `prepare` y `check` contra un PostgreSQL desechable con la configuración, los roles y el SQL reales: reproduce el bloqueo, comprueba que se resuelve y que una tabla posterior lo vuelve a necesitar. Forma parte de `validate.sh`. `scripts/init_odoo.sh` repite `prepare` tras instalar módulos, antes de que Odoo vuelva a escribir.
- **Plan de pago.** Queda un caso: instalar un módulo con tablas sin clave primaria desde la interfaz de Odoo, sin pasar por el script. En la Fase 6, alertar de tablas publicadas sin identidad.
- **Disparador.** Error `cannot update table ... because it does not have a replica identity`, o Connect por encima del 80 % de su límite.

<a id="td-006"></a>
### TD-006 · Retención de WAL ligada al slot de replicación

- **Contexto.** Con el conector detenido, PostgreSQL conserva WAL para el slot. Se limita con `max_slot_wal_keep_size=4GB`.
- **Riesgo.** Al superar el tope, el slot se invalida y hace falta un snapshot completo. Sin tope, el disco se llenaría.
- **Mitigación actual.** Tope de 4 GB y `heartbeat.interval.ms=10000`. `scripts/cdc.py status` muestra el retraso del slot. Probado: los cambios hechos con Connect parado se recuperan al arrancarlo, y el [procedimiento de recuperación](../infra/postgres/README.md#recuperar-un-slot-invalidado) repite el snapshot.
- **Plan de pago.** En la Fase 6, alertar sobre el retraso del slot.
- **Disparador.** Retraso del slot superior a 2 GB.

<a id="td-007"></a>
### TD-007 · Bronze sin particionamiento avanzado ni mantenimiento

- **Contexto.** Cada tabla de origen se escribe en una tabla Delta sin particiones, en microlotes. No hay compactación ni `VACUUM` programados.
- **Riesgo.** Muchos ficheros pequeños, lecturas lentas hacia Silver y crecimiento continuo del almacenamiento.
- **Pago.** Pagada el 2026-11-25 (commit `999847d`), con autorización expresa del usuario:
  - `bronze-maintenance` compacta a diario (03:00 UTC) las tablas con 8 ficheros o más, ejecuta `VACUUM` con 7 días de retención y limpia el registro. Usa un usuario de MinIO propio, que solo alcanza `bronze/odoo/`; el de la ingesta sigue sin poder borrar ([TD-014](#td-014)).
  - Ocho tablas, las de mayor volumen o crecimiento, están particionadas por `_ingested_date`. Se convirtieron en un commit atómico por tabla, comprobando fila a fila: 26 547 filas, ninguna distinta. `delta.appendOnly` se levanta solo durante ese commit.
  - Silver y Gold los mantiene `medallion-transform`.
  - Probado: 12 ficheros pasan a 1 sin cambiar filas, con la ingesta escribiendo a la vez, y el usuario de la ingesta no puede ejecutar `VACUUM`.
- **Disparador.** Más de 1000 ficheros o 5 GB en una tabla Bronze.

<a id="td-008"></a>
### TD-008 · Trino en un solo nodo

- **Contexto.** Coordinador y worker comparten una JVM con 4 GB de heap y 1500 MB por consulta.
- **Riesgo.** Sin alta disponibilidad. Una consulta pesada falla o degrada a las demás.
- **Mitigación actual.** Límites de memoria y tiempo en `infra/trino/config.properties`, `LIMIT` obligatorio en el backend y tablas Gold preagregadas.
- **Plan de pago.** Separar coordinador y workers cuando haya hardware.
- **Disparador.** Más del 5 % de consultas fallidas por memoria, o p95 superior a 10 s.

<a id="td-009"></a>
### TD-009 · Sin autenticación JWT ni autorización

- **Contexto.** En el MVP, FastAPI, Trino, Unity Catalog, Kafka Connect y Ollama no exigen identidad.
- **Riesgo.** Quien acceda al host puede leer los datos, y no hay trazabilidad por usuario.
- **Mitigación actual.** Todos los puertos se publican solo en `127.0.0.1` y las redes están segmentadas: Odoo, el frontend y Caddy no alcanzan los servicios sin autenticación del plano de datos (comprobado). CORS está restringido al frontend.
- **Excepción aprobada.** La demo del TFG, publicada de forma permanente por decisión del usuario, solo a través de Caddy, con contraseña, con datos de demostración y con los controles de [TD-022](#td-022).
- **Plan de pago.** En la Fase 6, JWT con OIDC en FastAPI, roles por dominio y propagación del usuario a Trino.
- **Disparador.** **Bloquea cualquier otra exposición fuera del host**, y el uso de datos reales en la demo.

<a id="td-010"></a>
### TD-010 · Kafka con un solo broker y sin réplicas

- **Contexto.** KRaft con broker y controlador en un mismo proceso y factor de réplica 1.
- **Riesgo.** Si se pierde el volumen, se pierden los eventos no ingeridos y los offsets de Debezium. Además, los offsets vuelven a empezar en 0: las coordenadas `(_topic, _partition, _offset)` de Bronze se repetirían con eventos distintos.
- **Mitigación actual.** Volumen persistente y retención de 7 días. El origen permite repetir el snapshot.
- **Mitigación actual (Silver).** Desde la Fase 3, la deduplicación usa `(_topic, _partition, _offset, source_lsn)` y el orden lo da el LSN: si se recrea el clúster de Kafka, los offsets repetidos no ocultan eventos nuevos (con prueba). Si se recrea **PostgreSQL**, el LSN vuelve a empezar y los cambios parecerían antiguos: habría que vaciar Silver y reconstruirlo desde Bronze.
- **Plan de pago.** Tres brokers cuando haya hardware.
- **Disparador.** Necesidad de alta disponibilidad.

<a id="td-011"></a>
### TD-011 · Eventos JSON sin registro de esquemas

- **Contexto.** Se usa `JsonConverter` con `schemas.enable=false` para ahorrar memoria y simplificar el worker.
- **Riesgo.** Decimales, fechas y marcas de tiempo llegan como texto o enteros, y un cambio de esquema en Odoo puede pasar inadvertido.
- **Mitigación actual.** El evento crudo se conserva en Bronze. Silver impone contratos (`contracts.py`): 11 tablas y 188 columnas con su tipo. Una columna que falta, un tipo distinto o un nulo no permitido detienen **esa tabla**, sin convertir nada en nulo: las demás siguen, Gold conserva lo anterior y el healthcheck de `medallion-transform` deja de pasar. `scripts/check_contracts.py` compara los contratos con el esquema real de Odoo: 0 diferencias el 2026-11-25. Hay pruebas de cada tipo de incumplimiento.
- **Plan de pago.** Evaluar Apicurio con Avro en la Fase 6: la imagen de Debezium ya incluye los convertidores. Hasta entonces, repetir `check_contracts.py` al instalar o actualizar módulos.
- **Disparador.** Dos incidencias de tipos, u otro consumidor de los topics.

<a id="td-012"></a>
### TD-012 · Odoo 17 fuera de soporte estándar

- **Contexto.** El [soporte estándar de Odoo 17](https://www.odoo.com/documentation/20.0/administration/standard_extended_support.html) terminó en septiembre de 2026. La [imagen oficial](https://github.com/docker-library/official-images/blob/master/library/odoo) ya solo se publica para 18.0, 19.0 y 20.0; la última de 17.0 es `17.0-20260908`.
- **Riesgo.** Sin parches de seguridad. La severidad sube a Alta porque Odoo se expondrá durante la defensa.
- **Mitigación actual.** Datos de demostración, imagen fijada, rol de base de datos sin superusuario, accesos por defecto cerrados y Caddy delante con contraseña y rutas bloqueadas.
- **Plan de pago.** Migrar a una versión con soporte tras el MVP. Silver absorbe los cambios de esquema.
- **Disparador.** Vulnerabilidad crítica o uso con datos reales. La exposición más allá de la defensa está asumida por el usuario ([TD-022](#td-022)).

<a id="td-013"></a>
### TD-013 · Unity Catalog en modo desarrollo

- **Contexto.** Base H2 embebida y autorización desactivada. La plantilla de configuración 0.7.0 no permite indicar un endpoint S3 propio, y siguen abiertas las incidencias con MinIO ([#844](https://github.com/unitycatalog/unitycatalog/issues/844), [#1324](https://github.com/unitycatalog/unitycatalog/issues/1324)).
- **Riesgo.** Sin permisos por tabla ni credenciales temporales: cada cliente necesita sus propias claves S3.
- **Mitigación actual.** Unity Catalog se usa solo para metadatos, con la base en un volumen persistente. Probado el 2026-11-25: registro de 25 tablas externas en MinIO con descripciones de tabla y columna, sin credenciales de S3, y persistencia tras reiniciar (commit `999847d`). Si se pierde la base H2, `medallion-transform` la reconstruye en el siguiente ciclo.
- **Plan de pago.** En la Fase 6, pasar a PostgreSQL y activar la autorización.
- **Disparador.** Otro servicio escribe en el catálogo, o hacen falta permisos por tabla.

<a id="td-014"></a>
### TD-014 · Credenciales raíz compartidas y tráfico interno sin TLS

- **Contexto.** Hasta la Fase 2, worker y Trino usaban las credenciales raíz de MinIO. Los servicios se comunican sin cifrar y los secretos están en `.env`.
- **Riesgo.** Quien lea el tráfico de la red interna o `.env` obtiene las credenciales.
- **Mitigación actual.** Redes segmentadas y puertos locales. En PostgreSQL, Odoo y Debezium usan roles de mínimo privilegio. En MinIO, `minio-init` crea un usuario por servicio ([infra/minio](../infra/minio/README.md)): el worker no puede borrar en Bronze, y Trino solo lee Silver y Gold. Las credenciales raíz solo las reciben `minio` y `minio-init`, y `validate.sh` lo comprueba. `scripts/test_integration.sh` verifica 53 permisos concedidos y denegados con usuarios reales.
- **Excepciones de la Fase 3** (2026-11-25, commit `999847d`), las dos previstas y acotadas: Trino escribe solo en `gold/_trino_metastore/`, prefijo vetado al worker, y `bronze-maintenance` borra solo en `bronze/odoo/`.
- **Plan de pago.** TLS y Docker Secrets en la Fase 6.
- **Disparador.** Datos reales o acceso desde otro equipo.

<a id="td-015"></a>
### TD-015 · Sin observabilidad ni alertas de infraestructura

- **Contexto.** Solo hay healthchecks y logs rotados de Docker. Langfuse cubre las llamadas al LLM desde la Fase 4, no la infraestructura.
- **Riesgo.** El retraso del CDC, los fallos del worker y las muertes por memoria pasan inadvertidos.
- **Mitigación actual.** Healthchecks en los servicios, `scripts/cdc.py status` y `scripts/check_env.sh`.
- **Plan de pago.** Métricas de retraso de consumidor, slot, memoria y VRAM en la Fase 6, dentro del presupuesto de RAM.
- **Disparador.** Primera incidencia detectada por un usuario.

<a id="td-016"></a>
### TD-016 · Sin integración continua

- **Contexto.** El disparador («primer código en `services/`») se alcanzó en la Fase 1: había pruebas, pero nada garantizaba que se ejecutaran antes de llegar a `main`.
- **Riesgo.** Un cambio que no pasa las pruebas, o un secreto, llega a `main`.
- **Mitigación actual.** `.github/workflows/ci.yml` se ejecuta en cada push y pull request a `main`, con permisos de solo lectura y acciones fijadas por commit. Ejecuta `scripts/validate.sh` (incluye gitleaks sobre los ficheros y todo el historial, y las pruebas de TD-005 y TD-022) y `scripts/test_integration.sh`. Los controles negativos, comprobados el 2026-11-25: 8 mutaciones de Caddy, CDC y roles, y un secreto inventado, hacen fallar la validación.
- **Primera ejecución** (2026-11-25, commit `67bb45a`). En verde: validación y secretos, 91 s; Kafka, MinIO y worker, 65 s.
- **Pago.** Pagada el 2026-11-25 (commits `67bb45a` y `4741296`). El usuario activó la protección de `main` y se ha comprobado con la API de GitHub: pull request obligatorio, los dos trabajos exigidos con la rama al día y sin `push --force`. La protección no se aplica a los administradores: una cuenta propietaria todavía puede saltársela.
<a id="td-017"></a>
### TD-017 · Sin copias de seguridad

- **Contexto.** Todo el estado vive en volúmenes locales de Docker.
- **Riesgo.** Pérdida total por fallo de disco o por `docker compose down -v`.
- **Mitigación actual.** Los datos de demostración se regeneran y el código está en GitHub. Bronze se reconstruye desde Kafka durante 7 días, o con un snapshot nuevo. La imagen de MinIO tiene copia ([TD-003](#td-003)). `.env` solo existe en este equipo: conviene guardarlo en un gestor de contraseñas.
- **Plan de pago.** Copias de PostgreSQL, MinIO, Unity Catalog y Langfuse, con prueba de restauración, en la Fase 6.
- **Disparador.** Datos que no se puedan regenerar.

<a id="td-018"></a>
### TD-018 · Text-to-SQL sin conjunto de evaluación

- **Contexto.** No hay preguntas de referencia con su SQL y su resultado esperados.
- **Riesgo.** Respuestas incorrectas pero verosímiles, y ningún criterio objetivo para elegir modelo o prompt.
- **Mitigación actual.** El SQL ejecutado se mostrará siempre junto al resultado.
- **Plan de pago.** Al menos 50 preguntas de los cinco dominios, medidas por precisión de ejecución, en la Fase 4.
- **Disparador.** Cualquier cambio de modelo, de proveedor o de prompt.

<a id="td-019"></a>
### TD-019 · Tremor con poco mantenimiento

- **Contexto.** `@tremor/react` 3.18.7 exige React 18 (`^18.0.0`) y su repositorio no cambia desde enero de 2025. La variante de copiar y pegar requiere Tailwind 4 y su repositorio no cambia desde octubre de 2025. Next.js 16 admite React 18 y 19.
- **Riesgo.** Quedar anclados a React 18 y Tailwind 3, o mantener componentes copiados.
- **Mitigación actual.** El esqueleto del frontend usa React 19, sin Tremor ni Tailwind.
- **Plan de pago.** Decidir al empezar la Fase 5. Recomendación: componentes de copiar y pegar con React 19 y Tailwind 4, porque el código queda en el repositorio. Elegir `@tremor/react` obligaría a bajar a React 18.
- **Disparador.** Inicio de la Fase 5.

<a id="td-020"></a>
### TD-020 · Servicios sin probar en ejecución

- **Contexto.** Probados el 2026-11-24 en un proyecto aislado, con datos de demostración y sin exponer nada: PostgreSQL, Odoo, Kafka, Debezium, MinIO, Trino, Langfuse, worker, backend, frontend y Caddy. Un cambio en Odoo llegó a Kafka en 0,3 a 1,3 segundos, y ningún contenedor superó su límite de memoria.
- **Riesgo.** Errores que solo aparecen en ejecución en lo que falta: Unity Catalog, Ollama, NVIDIA NIM (no hay clave para probarlo) y el conjunto completo con 22 GB de RAM.
- **Mitigación actual.** Perfiles por capa: cada fase arranca solo lo que valida. El 2026-11-25, `check_env.sh --gpu` termina con 0 fallos (22019 MiB, usuario sin privilegios y GPU en contenedor) y Caddy sirve un certificado público válido ([TD-022](#td-022)).
- **Fase 2 en ejecución** (2026-11-25, SSD). Tiempos de arranque hasta estar sanos: `source` 12 s, `streaming` 22 s, MinIO 6 s, `apps` 8 s, `observability` 13 s. Los 42 885 mensajes de los 198 topics están en Bronze, sin pérdidas ni repeticiones, tras un `kill -9` a mitad de la carga, 100 s con MinIO parado y un reinicio completo de Docker. Un cambio en Odoo se lee en Delta a los 35 s (lote de 30 s). El worker alcanzó 129 MiB de 1536. Ningún secreto de `.env` ni columna excluida en Bronze.
- **Plan de pago.** Unity Catalog y Trino con las tablas reales en la Fase 3, `ai` y NIM en la Fase 4. Antes de probar el conjunto completo, revisar [TD-026](#td-026).
- **Disparador.** Inicio de cada fase.

<a id="td-021"></a>
### TD-021 · NVIDIA NIM envía datos a un tercero

- **Contexto.** `LLM_PROVIDER=nim` envía el esquema de Gold y la pregunta a la API de NVIDIA. Sus [términos de prueba](https://assets.ngc.nvidia.com/products/api-catalog/legal/NVIDIA%20API%20Trial%20Terms%20of%20Service.pdf) (consultados el 2026-11-24) solo permiten pruebas internas y evaluación (1.4), prohíben incluir datos confidenciales o personales (2.6) y autorizan a NVIDIA a usar lo enviado y lo generado para mejorar sus productos, incluidos sus modelos (3.3).
- **Riesgo.** Una pregunta puede contener datos personales («ventas a Juan Pérez»). Dependencia de un servicio gratuito cuyo catálogo cambia: el 2026-11-24 ya no incluía Qwen Coder. En los foros de NVIDIA se habla de 40 peticiones por minuto, sin confirmación oficial.
- **Mitigación actual.** Desactivado por defecto. La API no arranca con `nim` sin clave, exige HTTPS y no muestra la clave en logs, errores ni respuestas (con pruebas). `GET /api/llm` y la interfaz indican que el prompt sale del host. Nunca se envían filas de resultados.
- **Plan de pago.** En la Fase 4: aviso antes de la primera pregunta, detección de datos personales en la pregunta y prueba con una clave real. El modelo `nvidia/nemotron-3.5-lightning-30b-a3b` figura en el catálogo público, pero no se ha ejecutado.
- **Disparador.** **Bloquea el uso de NIM con datos reales.** También un cambio de términos o la retirada del modelo.

<a id="td-022"></a>
### TD-022 · Exposición a Internet de la demo del TFG

- **Contexto.** Excepción aprobada a [TD-009](#td-009): durante la presentación, Odoo y el frontend se publican a través de Caddy. Como solo hay un nombre público, el frontend usa el 443 y Odoo el 8443, con el mismo certificado; el 80 solo redirige a HTTPS. El router redirige los tres puertos al equipo. La regla 11 de CLAUDE.md incluye el 8443 desde el 2026-11-25.
- **Riesgo.** Equipo doméstico accesible desde Internet; una sola contraseña compartida, sin límite de intentos (la imagen oficial de Caddy no lo incluye); Odoo sin parches ([TD-012](#td-012)); Docker Desktop oculta la IP del cliente, así que Caddy no puede filtrar por IP; y los certificados públicos anuncian el dominio. Con un solo nombre, las cookies de Odoo también llegan al 443, porque no distinguen puertos.
- **Mitigación actual.**
  - `CADDY_BIND_IP=127.0.0.1` por defecto: exponer es un acto explícito de una persona. Caddy no se reinicia solo.
  - Contraseña en todas las rutas. Probado: sin credenciales válidas no pasa nadie, y 18 variantes de rutas peligrosas de Odoo (codificadas, con barras dobles, mayúsculas) devuelven 404 incluso con contraseña.
  - Caddy solo ve a Odoo, el frontend y FastAPI. Contenedor de solo lectura y sin privilegios adicionales.
  - `scripts/init_odoo.sh` cierra `admin/admin`, `demo/demo` y `portal/portal`. `scripts/check_exposure.py` lo comprueba y valida la cadena y el nombre del certificado servido en cada sitio: con una CA propia, no da el visto bueno.
  - Caddy no reenvía `Cookie` al frontend ni a FastAPI, que no usan cookies: la sesión de Odoo no sale de Odoo.
- **Ensayo** (2026-11-24). Let's Encrypt emitió el certificado (TLS-ALPN, 443). En 6 minutos llegaron 168 peticiones, muchas de rastreadores que buscaban `.env` o `.git/config`: 115 respuestas 401, 53 redirecciones y ningún acceso. El script fallaba con `TLSV1_ALERT_INTERNAL_ERROR` porque enviaba `nombre:8443` como SNI y por el 443: publicar el puerto no bastaba. La contraseña apareció en una captura y se rotó: la antigua devuelve 401 en los dos puertos.
- **Comprobado en `127.0.0.1` con el certificado real** (2026-11-25). `check_exposure.py`, 0 fallos; con `CADDY_TLS=internal`, un fallo por sitio, como debe. Odoo redirige con `:8443`, el WebSocket responde 101 y 17 variantes de rutas bloqueadas devuelven 404 con contraseña.
- **Cookies de Odoo** (2026-11-25). Odoo no las marca con `Secure` y viajaban en claro al abrir `http://nombre`. Caddy lo añade a todas, sin duplicarlo. Lo prueban `scripts/tests/test_caddy.sh`, con el Caddyfile real y servicios simulados en una red interna, y `check_exposure.py`, con Odoo. La primera también cubre contraseña, rutas bloqueadas y cabeceras que no se reenvían, y se ejecuta en `validate.sh`.
- **Exposición de más** (2026-11-25). Tras el ensayo, `.env` conservó la IP de la red local y Caddy quedó publicado de 09:43 a 13:42. Llegaron 67 peticiones: 44 respuestas 401, 23 redirecciones y ningún acceso. Caddy está retirado y `.env`, en modo local.
- **Decisión del usuario** (2026-11-25). Mantener la demo publicada de forma permanente, solo con datos de demostración, y asumir sus riesgos: deja de ser una excepción temporal a [TD-009](#td-009) y [TD-012](#td-012). `.env` conserva la IP de la red local y `CADDY_RESTART=unless-stopped`; `.env.example` sigue en `127.0.0.1` y `no`. El Caddyfile va dentro de la imagen, para que arranque con Docker ([TD-027](#td-027)).
- **Comprobado antes de publicar** (2026-11-25). Con el nombre y el certificado reales en `127.0.0.1`, `check_exposure.py` termina con **0 fallos**: certificado de Let's Encrypt válido hasta el 22 de febrero de 2027 en 443 y 8443, 401 sin credenciales, cookies con `Secure` y rutas de Odoo bloqueadas. El nombre público resuelve a la dirección del router.
- **Pendiente de una persona.** Publicar Caddy en la IP de la red local y probarlo desde fuera: CLAUDE.md reserva ese paso a una persona y no lo ha hecho el agente.
- **Límites conocidos.** Los scripts prueban desde el propio equipo, no desde Internet. Con la exposición permanente, la contraseña única sin límite de intentos y Odoo sin parches pesan más. Comprobar una contraseña cuesta CPU (bcrypt): muchas peticiones pueden frenar el equipo.
- **Plan de pago.** Para dejar de asumirla: pagar TD-009 y TD-012, o poner delante un túnel con identidad. Mientras siga publicada: revisar los accesos (`docker compose logs caddy`), cambiar la contraseña con regularidad y no cargar datos reales.
- **Disparador.** Cualquier acceso inesperado en los logs, o una vulnerabilidad crítica de Odoo 17: corte inmediato con `docker compose --profile caddy stop caddy`.

<a id="td-023"></a>
### TD-023 · Langfuse 2 sin parches de seguridad

- **Contexto.** Se instala Langfuse 2 (`2.95.11`) porque solo necesita PostgreSQL. Las versiones 3 y 4 requieren ClickHouse, Redis y S3, que no caben en el presupuesto. Langfuse [declara la versión 2 en fin de vida](https://langfuse.com/docs/compatibility), sin parches de seguridad.
- **Riesgo.** Vulnerabilidades sin corregir en un servicio que guarda prompts y SQL. Los SDK actuales no son compatibles: hay que usar el de Python v2.
- **Mitigación actual.** Solo en `127.0.0.1`, fuera de Caddy, sin telemetría y con el registro cerrado. Probado: claves falsas rechazadas, registro desactivado y una traza guardada con el SDK 2.60. Usa 280 MiB de 768.
- **Plan de pago.** En la Fase 6, revisar si una versión con soporte cabe en el hardware, o sustituirlo por un registro de trazas propio.
- **Disparador.** Vulnerabilidad crítica, datos reales o necesidad de acceso desde fuera.

<a id="td-024"></a>
### TD-024 · Imágenes fijadas por tag, no por digest

- **Contexto.** Salvo MinIO, las imágenes se fijan por tag completo. Un tag se puede volver a publicar con otro contenido.
- **Riesgo.** Dos equipos con el mismo compose podrían ejecutar imágenes distintas.
- **Mitigación actual.** Tags de versión exacta, comprobados en sus registros.
- **Plan de pago.** Fijar digests y automatizar su actualización en la Fase 6.
- **Disparador.** Comportamiento distinto entre equipos, o incidente de cadena de suministro.

<a id="td-025"></a>
### TD-025 · El disco del proyecto y de Docker anuncia un fallo

- **Contexto.** El repositorio, `.env` y los datos de Docker (18,2 GB) están en un disco duro mecánico con 48 050 horas de uso. El 2026-11-25, Windows lo marca como *Predictive Failure*. El registro de eventos contiene el aviso 52 («haga una copia de seguridad de sus datos de inmediato», 2026-11-11) y reinicios del controlador (129) el 2026-11-24. Los errores de lectura, 1716, se han corregido todos hasta ahora.
- **Riesgo.** Pérdida de los volúmenes de Docker y de `.env` en cualquier momento. Además, el disco se satura: tras reiniciar, las tareas esperaron a la E/S el 78 % del tiempo (media de 5 minutos) y `docker info` tardó más de 4 minutos. Instalar los módulos de Odoo superó los 10 minutos.
- **Pago.** Pagada el 2026-11-25 (commit `882c1dd`). Nada del proyecto queda en ese disco:
  - **Repositorio y `.env`**, en el SSD (`E:`), idénticos a los de `D:` y a GitHub antes de empezar. `.env` sigue ignorado.
  - **Docker**, en el SSD. La copia de 18,2 GB tardó 54 minutos: entre las 14:09 y las 14:31, una zona del disco bajó a 0,5 MB/s, sin errores irrecuperables.
  - **Ubuntu**, cuyo disco virtual también estaba en `D:` y por el que pasan los ficheros montados en los contenedores.
  - Comprobado: contenedores recreados desde `E:`, volúmenes intactos (conector en marcha, slot al día, 198 topics), `check_env.sh --gpu` con 0 fallos, y `docker info` en 0,7 s frente a más de 4 minutos.
- **Pendiente de una persona.** Sustituir el disco. Antes de desecharlo, borrarlo de forma segura: conserva una copia antigua del repositorio con `.env`.

<a id="td-026"></a>
### TD-026 · La memoria de WSL puede llenar la unidad del sistema

- **Contexto.** Medido el 2026-11-25. WSL puede ocupar 22 GB de los 32 del equipo, y la caché de disco de Linux cuenta como memoria en uso para Windows: 12 GB tras compilar las imágenes, con los servicios usando 2,6. Con un navegador abierto, la memoria confirmada llegó a 44 GB y Windows amplió su fichero de paginación, que es automático, hasta dejar `C:` con 4,1 GB.
- **Riesgo.** Si se llena `C:`, falla Windows.
- **Mitigación actual.**
  - **Tope de WSL: de 22 a 15 GB.** Es lo que de verdad protege `C:`. `autoMemoryReclaim=gradual` también está puesto ([SETUP_WSL](SETUP_WSL.md#3-wslconfig)), pero no basta: con los servicios en marcha la máquina nunca está ociosa y la caché no se devuelve (13 GB tras descargar imágenes).
  - Límites ajustados a lo medido: de 19 840 a 12 864 MiB con Trino, Unity Catalog y los dos servicios nuevos en marcha. En reposo, los 14 servicios usan unos 4,3 GB.
  - `check_env.sh` avisa por debajo de 20 GB libres. `wsl --shutdown` devuelve la memoria en el acto.
- **Plan de pago.** Medir `C:` y la memoria confirmada con el conjunto completo en marcha y un día de uso.
- **Disparador.** Menos de 10 GB libres en `C:` antes de una descarga o de una compilación.

<a id="td-027"></a>
### TD-027 · PostgreSQL no arranca solo al reiniciar Docker Desktop

- **Contexto.** Los ficheros montados en `odoo-db`, `odoo` y `caddy` pasan por Ubuntu. Al reiniciarse, Docker Desktop arranca los contenedores antes de que Ubuntu los publique: `odoo-db` termina con el código 127 (`not a directory`) y la política de reinicio no lo reintenta. Odoo se recupera solo cuando vuelve la base. Reproducido dos veces el 2026-11-25.
- **Riesgo.** Odoo y el CDC quedan parados hasta que alguien lo nota. No se pierden datos: el conector y Bronze continúan donde estaban (comprobado).
- **Pago.** Pagada el 2026-11-25 (commit `4741296`). `odoo-db`, `odoo` y `caddy` llevan su configuración dentro de imágenes propias. Comprobado: tras reiniciar Docker Desktop, los diez servicios vuelven a estar sanos en 72 s sin intervenir. El coste es que un cambio en `infra/` exige repetir el `up` con `--build`.

## Plantilla

```markdown
<a id="td-nnn"></a>
### TD-NNN · Título breve

- **Contexto.** Qué se ha decidido y por qué. Con fecha si depende de un hecho externo.
- **Riesgo.** Qué puede pasar y a quién afecta.
- **Mitigación actual.** Qué lo contiene hoy. Si no hay nada, se dice.
- **Plan de pago.** Qué se hará y en qué fase.
- **Disparador.** Señal medible que obliga a adelantar el pago.
```

Añade también la fila correspondiente en la tabla de resumen.

## Historial

| Fecha | Cambio |
|---|---|
| 2026-11-24 | Registro inicial: TD-001 a TD-020 (Fase 0). |
| 2026-11-24 | Fase 1. Mitigadas: TD-001, TD-002 (opción A, probada), TD-004, TD-005 y TD-006. Aceptada: TD-003. TD-012 sube a Alta. TD-016 se reduce a integración continua. Nuevas: TD-021 a TD-024. |
| 2026-11-24 | Puesta en marcha persistente. TD-005: consumo de Connect actualizado. |
| 2026-11-25 | TD-022: esquema 443/8443 con un solo nombre, certificado validado por `check_exposure.py` y contraseña rotada. TD-020: 22 GB, GPU y certificado público comprobados. Nueva, Crítica: TD-025. |
| 2026-11-25 | Fase 2, Bronze. Pagada: TD-025. Mitigadas: TD-014 (usuarios de MinIO) y TD-016 (integración continua en verde). TD-003 con copia restaurable. TD-005 con prueba automática. TD-022: `Secure` en las cookies y exposición de más, sin accesos. TD-010 y TD-020 actualizadas. Nuevas: TD-026 y TD-027. |
