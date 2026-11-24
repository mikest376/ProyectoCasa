# medallion-worker

Consume los eventos CDC de Kafka y escribe Delta Lake en MinIO. Es el único servicio que escribe en el lakehouse.

| | |
|---|---|
| Tecnología | Python 3.11+, `deltalake` (delta-rs), `polars`, cliente de Kafka |
| Entrada | Topics `odoo.public.*` |
| Salida | `s3://bronze`, `s3://silver`, `s3://gold` y registro en Unity Catalog |
| Memoria | 1536 MiB |
| Fases | 2 (Bronze) y 3 (Silver y Gold) |

Las reglas de orden, duplicados, borrados y valores TOAST están en [ARCHITECTURE.md](../../docs/ARCHITECTURE.md#medallion). Todavía no hay código ni Dockerfile.
