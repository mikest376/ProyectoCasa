# medallion-worker

Consume los eventos CDC de Kafka y escribe Delta Lake en MinIO. Es el único servicio que escribe en el lakehouse.

| | |
|---|---|
| Tecnología | Python 3.11+, cliente de Kafka, `deltalake` (delta-rs) y `polars` |
| Entrada | Topics `odoo.public.*` |
| Salida | `s3://bronze/odoo/<tabla>`. Silver, Gold y el registro en Unity Catalog, en la Fase 3 |
| Memoria | 1536 MiB |

**Estado (Fase 2).** Escribe Bronze: una tabla Delta por topic, en modo append-only, con el evento crudo.

| Módulo | Función |
|---|---|
| `events.py` | Evento de Debezium a fila de Bronze |
| `batching.py` | Lotes por número, tamaño y tiempo, agrupados por topic |
| `sinks.py` | Escritura en Delta Lake |
| `consumer.py` | Bucle de Kafka y confirmación de offsets |

## Garantías

- **Al menos una vez.** El offset de cada topic se confirma después de su commit en Delta, de forma explícita. Si una tabla falla, se confirman las anteriores, el proceso se detiene, Docker lo reinicia y Kafka vuelve a entregar el resto. Puede haber filas repetidas, nunca perdidas: se distinguen por `(_topic, _partition, _offset)`.
- **Append-only.** Las tablas llevan `delta.appendOnly=true` y el usuario de MinIO del worker no puede borrar en Bronze ([infra/minio](../../infra/minio/README.md)).
- **Nada se descarta.** Un mensaje que no se puede interpretar se guarda tal cual en `bronze/odoo/_rejected`, con el motivo.
- **Memoria acotada.** Un lote se cierra a los `WORKER_BATCH_MAX_RECORDS` eventos, `WORKER_BATCH_MAX_BYTES` bytes o `WORKER_BATCH_MAX_SECONDS` segundos, lo que ocurra antes.

## Pruebas

```bash
docker build --target test services/medallion-worker     # lint, tipos y pruebas unitarias
bash scripts/test_integration.sh                         # Kafka y MinIO reales, en un proyecto aislado
```

Las reglas de orden, duplicados, borrados y valores TOAST están en [ARCHITECTURE.md](../../docs/ARCHITECTURE.md#medallion).
