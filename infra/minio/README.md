# MinIO

| Fichero | Contenido |
|---|---|
| `init.sh` | Buckets, políticas y usuarios. Lo ejecuta `minio-init`; se puede repetir |
| `policies/medallion-worker.json` | Único escritor. En Bronze puede añadir, no borrar |
| `policies/trino.json` | Solo lectura de Silver y Gold |

`@BRONZE@`, `@SILVER@` y `@GOLD@` se sustituyen por los nombres de los buckets de `.env`.

| Usuario | Bronze | Silver | Gold | Administración |
|---|---|---|---|---|
| raíz (`MINIO_ROOT_USER`) | Todo | Todo | Todo | Sí. Solo `minio` y `minio-init` |
| `WORKER_S3_ACCESS_KEY_ID` | Leer y escribir, sin borrar | Leer, escribir y borrar | Leer, escribir y borrar | No |
| `TRINO_S3_ACCESS_KEY_ID` | Nada | Leer | Leer | No |

Cambiar una contraseña o una política: edita `.env` o el fichero y repite la inicialización.

```bash
docker compose --profile lakehouse up --force-recreate minio-init
docker compose --profile apps up -d medallion-worker     # recoge la contraseña nueva
```

**Fase 3.** El metastore de Trino en ficheros ([TD-002](../../docs/TECHNICAL_DEBT.md#td-002)) exige que Trino escriba en `gold/_trino_metastore/`, y la compactación de Bronze ([TD-007](../../docs/TECHNICAL_DEBT.md#td-007)), que alguien pueda borrar allí. Las dos excepciones se añadirán a estas políticas cuando hagan falta, y solo con ese alcance.

`scripts/test_integration.sh` comprueba estos permisos con usuarios reales.

## Copia de la imagen

MinIO ya no publica imágenes y la de Chainguard puede desaparecer ([TD-003](../../docs/TECHNICAL_DEBT.md#td-003)). `scripts/backup_minio_image.sh` guarda una copia local y la restaura:

```bash
bash scripts/backup_minio_image.sh save       # fuera del repositorio, en ../backups
bash scripts/backup_minio_image.sh verify     # integridad de la copia
bash scripts/backup_minio_image.sh restore    # si el digest deja de descargarse
```
