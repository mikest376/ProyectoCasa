# medallion-worker

Consume los eventos CDC de Kafka y escribe Delta Lake en MinIO. Es el único servicio que escribe en el lakehouse.

| | |
|---|---|
| Tecnología | Python 3.11+, cliente de Kafka, `deltalake` (delta-rs) y `polars` |
| Entrada | Topics `odoo.public.*` |
| Salida | `s3://bronze/odoo/<tabla>`, `s3://silver/<dominio>/<tabla>`, `s3://gold/<dominio>/<modelo>` y registro en Trino y Unity Catalog |
| Memoria | 384 MiB la ingesta, 512 la transformación y 384 el mantenimiento |

| Orden | Servicio | Función |
|---|---|---|
| `ingest` | `medallion-worker` | Kafka → Bronze |
| `transform` | `medallion-transform` | Bronze → Silver → Gold y catálogos. `--once`, una sola vez |
| `maintain` | `bronze-maintenance` | Compactación y `VACUUM` diarios de Bronze. `--once`, una sola vez |
| `maintain-gold` | | Lo mismo para Silver y Gold: `transform` lo hace a diario |
| `partition` | | Particiona las tablas Bronze de mayor volumen. Con la ingesta detenida y el usuario de mantenimiento |
| `catalogs` | | Compara Trino, Unity Catalog y lo declarado |
| `contracts` | | Muestra los contratos de Silver en JSON |

| Módulo | Función |
|---|---|
| `events.py` | Evento de Debezium a fila de Bronze |
| `batching.py` | Lotes por número, tamaño y tiempo, agrupados por topic |
| `sinks.py` | Escritura en Delta Lake |
| `consumer.py` | Bucle de Kafka y confirmación de offsets |
| `contracts.py`, `decoding.py` | Contratos de Silver y conversión estricta de los valores de Debezium |
| `silver.py` | Estado actual por LSN: duplicados, borrados y valores TOAST |
| `gold_models.py`, `gold.py` | Modelos dimensionales y su reconstrucción |
| `catalogs.py` | Registro en Trino y Unity Catalog, y comparación |
| `maintenance.py` | Compactación, limpieza y partición |
| `transform.py` | Ciclo de transformación |

## Garantías

- **Al menos una vez.** El offset de cada topic se confirma después de su commit en Delta, de forma explícita. Si una tabla falla, se confirman las anteriores, el proceso se detiene, Docker lo reinicia y Kafka vuelve a entregar el resto. Puede haber filas repetidas, nunca perdidas: se distinguen por `(_topic, _partition, _offset)`.
- **Append-only.** Las tablas llevan `delta.appendOnly=true` y el usuario de MinIO del worker no puede borrar en Bronze ([infra/minio](../../infra/minio/README.md)).
- **Nada se descarta.** Un mensaje que no se puede interpretar se guarda tal cual en `bronze/odoo/_rejected`, con el motivo.
- **Memoria acotada.** Un lote se cierra a los `WORKER_BATCH_MAX_RECORDS` eventos, `WORKER_BATCH_MAX_BYTES` bytes o `WORKER_BATCH_MAX_SECONDS` segundos, lo que ocurra antes.

## Pruebas

```bash
docker build --target test services/medallion-worker     # lint, tipos y pruebas unitarias
bash scripts/test_integration.sh                         # Kafka, MinIO, Trino y Unity Catalog reales, aislados
```

Las reglas de orden, duplicados, borrados y valores TOAST están en [ARCHITECTURE.md](../../docs/ARCHITECTURE.md#medallion).
