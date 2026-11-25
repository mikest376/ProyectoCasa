# scripts

Se ejecutan desde la raíz del repositorio, dentro de WSL.

| Script | Función | Modifica |
|---|---|---|
| `check_env.sh` | Diagnóstico de WSL, Docker, disco y configuración. `--gpu` prueba la GPU | Nada |
| `init_odoo.sh` | PostgreSQL, Odoo con módulos y datos de demostración, cierre de accesos por defecto y `cdc.py prepare` | Base de Odoo |
| `cdc.py` | `prepare`, `check`, `register` y `status` del CDC | Publicación y conector |
| `check_exposure.py` | Comprobación previa a exponer el entorno | Nada |
| `validate.sh` | Lint, tipos, pruebas, secretos y configuración, en contenedores. `--quick` no construye imágenes. Es lo que ejecuta la integración continua | Imágenes locales |
| `test_integration.sh` | Permisos de MinIO y recorrido de Kafka a Bronze, en un proyecto de Compose aislado que se elimina al terminar | Nada del proyecto |
| `backup_minio_image.sh` | Copia local de la imagen de MinIO: `save`, `verify` y `restore` (TD-003) | `../backups` |
| `tests/test_cdc.py` | `cdc.py` contra un PostgreSQL desechable (TD-004 y TD-005) | Nada |
| `tests/test_caddy.sh` | El Caddyfile real con servicios simulados (TD-022) | Nada |

Normas: Bash con `set -euo pipefail` (los de diagnóstico, sin `-e`, para mostrar todos los fallos) o Python solo con biblioteca estándar. Son repetibles y ningún secreto pasa por argumentos ni por la salida.

Si los ficheros no tienen permiso de ejecución (por ejemplo, en una unidad de Windows), usa `bash scripts/<script>.sh`.
