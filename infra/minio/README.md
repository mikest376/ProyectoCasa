# MinIO

| Fichero | Contenido |
|---|---|
| `init.sh` | Buckets, políticas y usuarios. Lo ejecuta `minio-init`; se puede repetir |
| `policies/medallion-worker.json` | Único escritor. En Bronze puede añadir, no borrar |
| `policies/trino.json` | Solo lectura de Silver y Gold, más su metastore |
| `policies/bronze-maintenance.json` | Compactar y limpiar `bronze/odoo/` |

`@BRONZE@`, `@SILVER@` y `@GOLD@` se sustituyen por los nombres de los buckets de `.env`.

| Usuario | Bronze | Silver | Gold | Administración |
|---|---|---|---|---|
| raíz (`MINIO_ROOT_USER`) | Todo | Todo | Todo | Sí. Solo `minio` y `minio-init` |
| `WORKER_S3_ACCESS_KEY_ID` | Leer y escribir, sin borrar | Leer, escribir y borrar | Leer, escribir y borrar, salvo `_trino_metastore/` | No |
| `TRINO_S3_ACCESS_KEY_ID` | Nada | Leer | Leer; escribir solo en `_trino_metastore/` | No |
| `MAINTENANCE_S3_ACCESS_KEY_ID` | Leer, escribir y borrar en `odoo/` | Nada | Nada | No |

Cambiar una contraseña o una política: edita `.env` o el fichero y repite la inicialización.

```bash
docker compose --profile lakehouse up --force-recreate minio-init
docker compose --profile apps up -d medallion-worker     # recoge la contraseña nueva
```

`scripts/test_integration.sh` comprueba estos permisos con usuarios reales.

## Copia de la imagen

MinIO ya no publica imágenes y la de Chainguard puede desaparecer ([TD-003](../../docs/TECHNICAL_DEBT.md#td-003)). `scripts/backup_minio_image.sh` guarda una copia local y la restaura:

```bash
bash scripts/backup_minio_image.sh save       # fuera del repositorio, en ../backups
bash scripts/backup_minio_image.sh verify     # integridad de la copia
bash scripts/backup_minio_image.sh restore    # si el digest deja de descargarse
```
