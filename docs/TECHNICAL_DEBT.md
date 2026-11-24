# Registro de deuda técnica

Deuda asumida de forma consciente para el MVP. Lo que no está aquí no es deuda aceptada: es un defecto.

El flujo para registrar y pagar deuda es obligatorio y está en [CLAUDE.md](../CLAUDE.md#flujo-obligatorio-de-deuda-técnica). La plantilla está al final de este documento.

**Última revisión:** 2026-11-24 (Fase 0)

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
| Aceptada | Se mantiene durante todo el MVP. |
| Pagada | Resuelta, con fecha y commit. |

## Resumen

| ID | Deuda | Severidad | Estado | Se paga en |
|---|---|---|---|---|
| [TD-001](#td-001) | El modelo aprobado no cabe completo en la GPU (6 GB) | Alta | Pendiente de decisión | Antes de Fase 4 |
| [TD-002](#td-002) | Trino no lee Unity Catalog OSS para tablas Delta | Alta | Pendiente de decisión | Fase 3 |
| [TD-003](#td-003) | MinIO Community archivado, con imagen de un tercero | Alta | Abierta | Fases 2 y 6 |
| [TD-004](#td-004) | La captura global copia datos sensibles a Kafka y Bronze | Alta | Abierta | Fase 1 |
| [TD-005](#td-005) | La captura global puede bloquear escrituras en Odoo | Alta | Abierta | Fase 1 |
| [TD-006](#td-006) | Retención de WAL ligada al slot de replicación | Media | Abierta | Fases 1 y 6 |
| [TD-007](#td-007) | Bronze sin particionamiento avanzado ni mantenimiento | Media | Abierta | Fase 3 |
| [TD-008](#td-008) | Trino en un solo nodo | Media | Aceptada | Después del MVP |
| [TD-009](#td-009) | Sin autenticación JWT ni autorización | Alta | Abierta | Fase 6 |
| [TD-010](#td-010) | Kafka con un solo broker y sin réplicas | Media | Aceptada | Después del MVP |
| [TD-011](#td-011) | Eventos JSON sin registro de esquemas | Media | Abierta | Fase 6 |
| [TD-012](#td-012) | Odoo 17 fuera de soporte estándar | Media | Abierta | Después del MVP |
| [TD-013](#td-013) | Unity Catalog en modo desarrollo | Media | Abierta | Fases 3 y 6 |
| [TD-014](#td-014) | Credenciales raíz compartidas y tráfico interno sin TLS | Media | Abierta | Fases 2 y 6 |
| [TD-015](#td-015) | Sin observabilidad ni alertas | Media | Abierta | Fase 6 |
| [TD-016](#td-016) | Sin CI ni pruebas automatizadas | Media | Abierta | Fase 2 |
| [TD-017](#td-017) | Sin copias de seguridad | Media | Abierta | Fase 6 |
| [TD-018](#td-018) | Text-to-SQL sin conjunto de evaluación | Media | Abierta | Fase 4 |
| [TD-019](#td-019) | Tremor con poco mantenimiento | Media | Pendiente de decisión | Fase 5 |
| [TD-020](#td-020) | Compose estructural sin probar en ejecución | Media | Abierta | Fases 1 a 5 |

## Detalle

<a id="td-001"></a>
### TD-001 · El modelo aprobado no cabe completo en la GPU (6 GB)

- **Contexto.** El diseño suponía 8 GB de VRAM. `nvidia-smi` informa de 6144 MiB, y la misma GPU dibuja el escritorio de Windows: 2,4 GB ocupados y 3,6 GB libres en la inspección del 2026-11-24. `qwen2.5-coder:7b-instruct-q4_K_M` pesa 4,68 GB y necesita entre 5 y 5,5 GB en ejecución.
- **Riesgo.** Ollama reparte las capas entre GPU y CPU: respuestas más lentas y más RAM consumida. Además, la GTX 1060 (Pascal) ya está en la última rama de controladores que la admite (R580). Ollama [exige el controlador 570 o superior](https://docs.ollama.com/gpu) para esta generación.
- **Mitigación actual.** Un modelo y una petición a la vez, contexto de 4096 tokens, caché KV en `q8_0` y 5120 MiB de RAM para las capas en CPU. El modelo se cambia con `OLLAMA_MODEL`.
- **Plan de pago.** Comparar en la Fase 4 el modelo 7B con `qwen2.5-coder:3b-instruct-q4_K_M` (1,93 GB, cabe en la VRAM libre) usando el conjunto de evaluación de TD-018. Medir precisión del SQL y tiempo de respuesta, y fijar el modelo por defecto. Liberar VRAM cerrando aplicaciones con aceleración gráfica ayuda en ambos casos.
- **Disparador.** Respuesta p95 superior a 30 s, o errores de memoria de GPU.

<a id="td-002"></a>
### TD-002 · Trino no lee Unity Catalog OSS para tablas Delta

- **Contexto.** Verificado el 2026-11-24:
  - Trino [retiró el soporte de Unity Catalog por HTTP](https://trino.io/ecosystem/data-lake) en la versión 473.
  - El conector Delta Lake de Trino 484 [documenta solo los metastores `thrift` y `glue`](https://trino.io/docs/current/object-storage/metastores.html). `file` sigue existiendo en el código, sin documentar.
  - La [integración oficial de Unity Catalog con Trino](https://github.com/unitycatalog/unitycatalog/blob/v0.7.0/docs/integrations/unity-catalog-trino.md) usa Iceberg REST y requiere tablas Delta con UniForm. delta-rs [no permite activarlo](https://github.com/delta-io/delta-rs/issues/3299).
- **Riesgo.** Dos registros de metadatos que pueden divergir, y dependencia de una opción de Trino sin documentar.
- **Mitigación actual.** Unity Catalog es el catálogo de referencia y Trino está fijado en la versión 484. La configuración propuesta está inactiva en `infra/trino/catalog/lakehouse.properties.example`.
- **Plan de pago.** Elegir en la Fase 3:
  - **A (recomendada para el MVP):** `hive.metastore=file` sobre MinIO, con registro por `system.register_table` desde el worker. No añade contenedores.
  - **B:** Hive Metastore (`thrift`). Es la vía documentada, pero añade un contenedor, una base de datos y unos 768 MiB. Agota la holgura de RAM.
  - **C:** Gold en Iceberg a través de Unity Catalog. Elimina la duplicidad, pero cambia la decisión de usar Delta Lake.
- **Disparador.** La opción A falla en la Fase 3, o una versión de Trino elimina el metastore `file`.

<a id="td-003"></a>
### TD-003 · MinIO Community archivado, con imagen de un tercero

- **Contexto.** El repositorio [`minio/minio`](https://github.com/minio/minio) está archivado (último cambio: 2026-04-24). El 2026-11-24, `minio/minio` y `minio/mc` devuelven 404 en Docker Hub, y Quay rechaza la descarga anónima (401). El compose usa la [imagen pública de Chainguard](https://www.chainguard.dev/fr-FR/unchained/secure-and-free-minio-chainguard-containers), compilada desde código fuente.
- **Riesgo.** El nivel gratuito de Chainguard solo ofrece `latest`: puede retirar la imagen o eliminar el digest fijado. El proyecto original no publica más parches.
- **Mitigación actual.** Imagen fijada por digest. Los servicios usan solo la API S3, así que cambiar de almacenamiento es un cambio de configuración, salvo la creación de buckets en `minio-init`.
- **Plan de pago.** En la Fase 2, guardar una copia local de la imagen con `docker save`. En la Fase 6, evaluar SeaweedFS, Garage y RustFS. Hay que tener en cuenta que Trino solo prueba su compatibilidad con AWS S3 y MinIO.
- **Disparador.** El digest deja de descargarse, o aparece una vulnerabilidad crítica sin parche.

<a id="td-004"></a>
### TD-004 · La captura global copia datos sensibles a Kafka y Bronze

- **Contexto.** Capturar todas las tablas incluye hashes de contraseñas (`res_users`), claves de API (`res_users_apikeys`), parámetros con secretos (`ir_config_parameter`), credenciales de correo (`ir_mail_server`, `fetchmail_server`) y datos personales (`res_partner`, `mail_message`).
- **Riesgo.** Secretos y datos personales en claro en los topics y en una capa que, por diseño, no se modifica: borrarlos después es costoso.
- **Mitigación actual.** El conector aún no está registrado. Solo se permiten datos de demostración y Bronze no es accesible desde Text-to-SQL.
- **Plan de pago.** Antes del primer snapshot, en la Fase 1, excluir o enmascarar esas columnas con `column.exclude.list` y `column.mask.*`. La captura sigue siendo de todas las tablas. En la Fase 6, definir retención y borrado de datos personales en Bronze.
- **Disparador.** **Bloquea la carga de cualquier dato real.**

<a id="td-005"></a>
### TD-005 · La captura global puede bloquear escrituras en Odoo

- **Contexto.** PostgreSQL rechaza `UPDATE` y `DELETE` en tablas publicadas que no tienen clave primaria ni `REPLICA IDENTITY`. Además, cada tabla genera un topic, y una instalación de Odoo tiene cientos de tablas.
- **Riesgo.** Errores de escritura en el ERP al activar la publicación, y presión de memoria en Kafka y Connect (heap de 768 MiB cada uno).
- **Mitigación actual.** Ninguna: la publicación todavía no existe.
- **Plan de pago.** En la Fase 1, listar las tablas sin clave primaria antes de crear la publicación y asignarles `REPLICA IDENTITY FULL`. Repetirlo tras instalar módulos. Medir número de topics y heap; si no cabe, agrupar topics con enrutado en Debezium.
- **Disparador.** Error `cannot update table ... because it does not have a replica identity`, o heap de Connect por encima del 80 %.

<a id="td-006"></a>
### TD-006 · Retención de WAL ligada al slot de replicación

- **Contexto.** Con el conector detenido, PostgreSQL conserva WAL para el slot. Se limita con `max_slot_wal_keep_size=4GB`.
- **Riesgo.** Al superar el tope, el slot se invalida y hace falta un snapshot completo. Sin tope, el disco se llenaría.
- **Mitigación actual.** Tope de 4 GB en `infra/postgres/postgresql.conf`.
- **Plan de pago.** En la Fase 1, activar `heartbeat.interval.ms` y documentar la recuperación. En la Fase 6, alertar sobre el retraso del slot.
- **Disparador.** Retraso del slot superior a 2 GB.

<a id="td-007"></a>
### TD-007 · Bronze sin particionamiento avanzado ni mantenimiento

- **Contexto.** Cada tabla de origen se escribe en una tabla Delta sin particiones, en microlotes. No hay compactación ni `VACUUM` programados.
- **Riesgo.** Muchos ficheros pequeños, lecturas lentas hacia Silver y crecimiento continuo del almacenamiento.
- **Mitigación actual.** Lotes por tamaño y tiempo (`WORKER_BATCH_MAX_RECORDS`, `WORKER_BATCH_MAX_SECONDS`).
- **Plan de pago.** En la Fase 3, compactar y limpiar a diario con delta-rs, y particionar por fecha de ingesta las tablas de mayor volumen.
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
- **Mitigación actual.** Todos los puertos se publican solo en `127.0.0.1` y CORS está restringido al frontend.
- **Plan de pago.** En la Fase 6, JWT con OIDC en FastAPI, roles por dominio y propagación del usuario a Trino.
- **Disparador.** **Bloquea exponer cualquier puerto fuera del host.**

<a id="td-010"></a>
### TD-010 · Kafka con un solo broker y sin réplicas

- **Contexto.** KRaft con broker y controlador en un mismo proceso y factor de réplica 1.
- **Riesgo.** Si se pierde el volumen, se pierden los eventos no ingeridos y los offsets de Debezium.
- **Mitigación actual.** Volumen persistente y retención de 7 días. El origen permite repetir el snapshot.
- **Plan de pago.** Tres brokers cuando haya hardware.
- **Disparador.** Necesidad de alta disponibilidad.

<a id="td-011"></a>
### TD-011 · Eventos JSON sin registro de esquemas

- **Contexto.** Se usa `JsonConverter` con `schemas.enable=false` para ahorrar memoria y simplificar el worker.
- **Riesgo.** Decimales, fechas y marcas de tiempo llegan como texto o enteros, y un cambio de esquema en Odoo puede pasar inadvertido.
- **Mitigación actual.** El evento crudo se conserva en Bronze y los tipos se imponen en Silver.
- **Plan de pago.** Contratos de esquema con pruebas en la Fase 3. Evaluar Apicurio con Avro en la Fase 6: la imagen de Debezium ya incluye los convertidores.
- **Disparador.** Actualización de módulos de Odoo, o dos incidencias de tipos.

<a id="td-012"></a>
### TD-012 · Odoo 17 fuera de soporte estándar

- **Contexto.** El [soporte estándar de Odoo 17](https://www.odoo.com/documentation/20.0/administration/standard_extended_support.html) terminó en septiembre de 2026. La [imagen oficial](https://github.com/docker-library/official-images/blob/master/library/odoo) ya solo se publica para 18.0, 19.0 y 20.0; la última de 17.0 es `17.0-20260908`.
- **Riesgo.** Sin parches de seguridad.
- **Mitigación actual.** Uso local con datos de demostración e imagen fijada.
- **Plan de pago.** Migrar a una versión con soporte tras el MVP. Silver absorbe los cambios de esquema.
- **Disparador.** Vulnerabilidad crítica o uso con datos reales.

<a id="td-013"></a>
### TD-013 · Unity Catalog en modo desarrollo

- **Contexto.** Base H2 embebida y autorización desactivada. La plantilla de configuración 0.7.0 no permite indicar un endpoint S3 propio, y siguen abiertas las incidencias con MinIO ([#844](https://github.com/unitycatalog/unitycatalog/issues/844), [#1324](https://github.com/unitycatalog/unitycatalog/issues/1324)).
- **Riesgo.** Sin permisos por tabla ni credenciales temporales: cada cliente necesita sus propias claves S3. El registro de tablas externas sobre MinIO está sin probar.
- **Mitigación actual.** Unity Catalog se usa solo para metadatos, con la base en un volumen persistente.
- **Plan de pago.** Probar el registro de tablas externas al inicio de la Fase 3. En la Fase 6, pasar a PostgreSQL y activar la autorización.
- **Disparador.** La prueba de la Fase 3 falla, o se corrompe la base H2.

<a id="td-014"></a>
### TD-014 · Credenciales raíz compartidas y tráfico interno sin TLS

- **Contexto.** Worker y Trino usan las credenciales raíz de MinIO. Los servicios se comunican sin cifrar y los secretos están en `.env`.
- **Riesgo.** Un servicio comprometido puede modificar cualquier capa.
- **Mitigación actual.** Red interna de Docker y puertos locales. Variables S3 separadas de las de MinIO para poder cambiarlas sin tocar código.
- **Plan de pago.** Usuarios de MinIO por servicio en la Fase 2 (worker con escritura, Trino con lectura). TLS y Docker Secrets en la Fase 6.
- **Disparador.** Datos reales o acceso desde otro equipo.

<a id="td-015"></a>
### TD-015 · Sin observabilidad ni alertas

- **Contexto.** Solo hay healthchecks y logs rotados de Docker.
- **Riesgo.** El retraso del CDC, los fallos del worker y las muertes por memoria pasan inadvertidos.
- **Mitigación actual.** Healthchecks en los servicios de infraestructura.
- **Plan de pago.** Métricas de retraso de consumidor, slot, memoria y VRAM en la Fase 6, dentro del presupuesto de RAM.
- **Disparador.** Primera incidencia detectada por un usuario.

<a id="td-016"></a>
### TD-016 · Sin CI ni pruebas automatizadas

- **Contexto.** La Fase 0 no contiene código de aplicación.
- **Riesgo.** Regresiones y secretos que llegan a `main` sin control.
- **Mitigación actual.** Lista de comprobación en `CLAUDE.md`.
- **Plan de pago.** En la Fase 2, GitHub Actions con validación del compose, presupuesto de memoria, `ruff`, `mypy`, `pytest` y detección de secretos.
- **Disparador.** Primer código en `services/`.

<a id="td-017"></a>
### TD-017 · Sin copias de seguridad

- **Contexto.** Todo el estado vive en volúmenes locales de Docker.
- **Riesgo.** Pérdida total por fallo de disco o por `docker compose down -v`.
- **Mitigación actual.** Los datos de demostración se regeneran.
- **Plan de pago.** Copias de PostgreSQL, MinIO y Unity Catalog, con prueba de restauración, en la Fase 6.
- **Disparador.** Datos que no se puedan regenerar.

<a id="td-018"></a>
### TD-018 · Text-to-SQL sin conjunto de evaluación

- **Contexto.** No hay preguntas de referencia con su SQL y su resultado esperados.
- **Riesgo.** Respuestas incorrectas pero verosímiles, y ningún criterio objetivo para elegir modelo o prompt.
- **Mitigación actual.** El SQL ejecutado se mostrará siempre junto al resultado.
- **Plan de pago.** Al menos 50 preguntas de los cinco dominios, medidas por precisión de ejecución, en la Fase 4.
- **Disparador.** Cualquier cambio de modelo o de prompt.

<a id="td-019"></a>
### TD-019 · Tremor con poco mantenimiento

- **Contexto.** `@tremor/react` 3.18.7 exige React 18 (`^18.0.0`) y su repositorio no cambia desde enero de 2025. La variante de copiar y pegar requiere Tailwind 4 y su repositorio no cambia desde octubre de 2025. Next.js 16 admite React 18 y 19.
- **Riesgo.** Quedar anclados a React 18 y Tailwind 3, o mantener componentes copiados.
- **Mitigación actual.** El frontend todavía no existe.
- **Plan de pago.** Decidir al empezar la Fase 5. Recomendación: componentes de copiar y pegar con React 19 y Tailwind 4, porque el código queda en el repositorio.
- **Disparador.** Inicio de la Fase 5.

<a id="td-020"></a>
### TD-020 · Compose estructural sin probar en ejecución

- **Contexto.** `docker-compose.yml` se ha validado con `docker compose config` y los tags se han comprobado en sus registros, pero ningún contenedor se ha arrancado. Los servicios propios no tienen Dockerfile.
- **Riesgo.** Errores de arranque, permisos de volúmenes o healthchecks que solo aparecen en ejecución.
- **Mitigación actual.** Perfiles por capa: cada fase arranca solo lo que valida.
- **Plan de pago.** Validar cada perfil en su fase: `source` y `streaming` en la 1, `lakehouse` en la 2, `query` en la 3, `ai` en la 4 y `apps` de la 2 a la 5.
- **Disparador.** Inicio de cada fase.

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
