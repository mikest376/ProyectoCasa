# medallion-worker

Consume los eventos CDC de Kafka y escribe Delta Lake en MinIO. Es el único servicio que escribe en el lakehouse.

| | |
|---|---|
| Tecnología | Python 3.11+, cliente de Kafka; `deltalake` (delta-rs) y `polars` desde la Fase 2 |
| Entrada | Topics `odoo.public.*` |
| Salida | `s3://bronze`, `s3://silver`, `s3://gold` y registro en Unity Catalog |
| Memoria | 1536 MiB |

**Estado (Fase 1).** Esqueleto funcional: se suscribe a los topics, valida e interpreta cada evento, los agrupa en lotes y registra un resumen. No guarda datos ni confirma offsets, así que la Fase 2 empezará desde el principio de los topics sin perder eventos.

| Módulo | Función |
|---|---|
| `events.py` | Evento de Debezium a fila de Bronze |
| `batching.py` | Lotes por tamaño y tiempo |
| `sinks.py` | Destinos. En la Fase 2 se añade Delta Lake |
| `consumer.py` | Bucle de Kafka y confirmación de offsets |

Lint, tipos y pruebas:

```bash
docker build --target test services/medallion-worker
```

Las reglas de orden, duplicados, borrados y valores TOAST están en [ARCHITECTURE.md](../../docs/ARCHITECTURE.md#medallion).
