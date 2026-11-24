#!/usr/bin/env bash
# Contenedor odoo-init: instala los módulos core con datos de demostración y cierra los
# accesos por defecto. Es repetible: los módulos ya instalados no se reinstalan.
set -euo pipefail

: "${ODOO_DB_NAME:?falta ODOO_DB_NAME}"
: "${ODOO_MODULES:?falta ODOO_MODULES}"
: "${ODOO_ADMIN_PASSWORD:?falta ODOO_ADMIN_PASSWORD}"

odoo -d "$ODOO_DB_NAME" -i "$ODOO_MODULES" --stop-after-init --no-http --max-cron-threads=0
odoo shell -d "$ODOO_DB_NAME" --no-http </opt/lakehouse/harden.py
