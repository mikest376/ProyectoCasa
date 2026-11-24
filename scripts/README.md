# scripts

Se ejecutan desde la raíz del repositorio, dentro de WSL.

| Script | Función | Modifica |
|---|---|---|
| `check_env.sh` | Diagnóstico de WSL, Docker, disco y configuración. `--gpu` prueba la GPU | Nada |
| `init_odoo.sh` | PostgreSQL, Odoo con módulos y datos de demostración, y cierre de accesos por defecto | Base de Odoo |
| `cdc.py` | `prepare`, `check`, `register` y `status` del CDC | Publicación y conector |
| `check_exposure.py` | Comprobación previa a exponer el entorno | Nada |
| `validate.sh` | Lint, tipos, pruebas y configuración, en contenedores. `--quick` no construye imágenes | Imágenes locales |

Normas: Bash con `set -euo pipefail` (los de diagnóstico, sin `-e`, para mostrar todos los fallos) o Python solo con biblioteca estándar. Son repetibles y ningún secreto pasa por argumentos ni por la salida.

Si los ficheros no tienen permiso de ejecución (por ejemplo, en una unidad de Windows), usa `bash scripts/<script>.sh`.
