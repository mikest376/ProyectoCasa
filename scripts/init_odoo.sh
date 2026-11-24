#!/usr/bin/env bash
# Inicializa Odoo: módulos core, datos de demostración y cierre de accesos por defecto.
# Es repetible. El servicio de Odoo se detiene mientras tanto para no duplicar memoria.
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/.."

docker compose --profile source stop odoo
docker compose --profile source up -d --wait odoo-db
docker compose --profile source --profile init run --rm -T odoo-init
docker compose --profile source up -d --wait odoo

echo "Odoo inicializado. Siguiente paso: python3 scripts/cdc.py register"
