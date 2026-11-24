#!/usr/bin/env bash
# Genera odoo.conf a partir del entorno y delega en el entrypoint oficial de la imagen.
# La contraseña maestra no se guarda en el repositorio ni llega al proceso de Odoo
# como variable de entorno.
set -euo pipefail

: "${HOST:?falta HOST}" "${PORT:?falta PORT}" "${USER:?falta USER}" "${PASSWORD:?falta PASSWORD}"
: "${ODOO_DB_NAME:?falta ODOO_DB_NAME}"
: "${ODOO_MASTER_PASSWORD:?falta ODOO_MASTER_PASSWORD}"

umask 077
export ODOO_RC=/tmp/odoo.conf

cat >"$ODOO_RC" <<EOF
[options]
addons_path = /mnt/extra-addons
data_dir = /var/lib/odoo
admin_passwd = ${ODOO_MASTER_PASSWORD}
db_host = ${HOST}
db_port = ${PORT}
db_user = ${USER}
db_password = ${PASSWORD}
db_name = ${ODOO_DB_NAME}
dbfilter = ^${ODOO_DB_NAME}\$
list_db = False
proxy_mode = True
workers = 0
max_cron_threads = 1
db_maxconn = 20
EOF

unset ODOO_MASTER_PASSWORD
exec /entrypoint.sh "$@"
