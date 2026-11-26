# scripts

Se ejecutan desde la raíz del repositorio, dentro de WSL.

| Script | Función | Modifica |
|---|---|---|
| `check_env.sh` | Diagnóstico de WSL, Docker, disco y configuración. `--gpu` prueba la GPU | Nada |
| `init_odoo.sh` | PostgreSQL, Odoo con módulos y datos de demostración, cierre de accesos por defecto y `cdc.py prepare` | Base de Odoo |
| `cdc.py` | `prepare`, `check`, `register` y `status` del CDC | Publicación y conector |
| `check_exposure.py` | Exposición por la dirección publicada y por el nombre público. Con `CADDY_CHECK_PASSWORD`, también entra: interfaz, `/api`, una pregunta real, Odoo y rutas bloqueadas | Nada (la pregunta deja su traza) |
| `check_pii.py` | Busca los datos personales de Odoo en las tres capas, su registro y sus ficheros sustituidos (TD-004) | Nada |
| `health_monitor.py` | Conector, slot de WAL, identidad de réplica, retraso de la ingesta, contenedores, memoria, VRAM, discos y `/api/system/health`. `--watch N` repite (TD-005, TD-006, TD-015) | Nada |
| `backup.sh` | Copia de PostgreSQL, MinIO, Unity Catalog y adjuntos fuera del repositorio (TD-017) | `../backups` |
| `restore.sh` | `--verify` restaura una copia en contenedores desechables y compara recuentos; con `--yes`, sobre el sistema en marcha | Nada con `--verify` |
| `pin_digests.sh` | Comprueba que toda imagen lleva digest; `--write` y `--refresh` los fijan (TD-024) | Ficheros con imágenes |
| `validate.sh` | Lint, tipos, pruebas, secretos y configuración, en contenedores. `--quick` no construye imágenes. Es lo que ejecuta la integración continua | Imágenes locales |
| `check_contracts.py` | Compara los contratos de Silver con el esquema real de Odoo (TD-011) | Nada |
| `test_integration.sh` | Permisos de MinIO, de Kafka a Bronze y de Bronze a los catálogos, en un proyecto de Compose aislado que se elimina al terminar | Nada del proyecto |
| `backup_minio_image.sh` | Copia local de la imagen de MinIO: `save`, `verify` y `restore` (TD-003) | `../backups` |
| `tests/test_cdc.py` | `cdc.py` contra un PostgreSQL desechable (TD-004 y TD-005) | Nada |
| `tests/test_caddy.sh` | El Caddyfile real con servicios simulados (TD-022) | Nada |

Normas: Bash con `set -euo pipefail` (los de diagnóstico, sin `-e`, para mostrar todos los fallos) o Python solo con biblioteca estándar. Son repetibles y ningún secreto pasa por argumentos ni por la salida.

Si los ficheros no tienen permiso de ejecución (por ejemplo, en una unidad de Windows), usa `bash scripts/<script>.sh`.
