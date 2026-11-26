# Los scripts van dentro de la imagen: al reiniciarse Docker Desktop, los ficheros montados
# desde WSL todavía no existen y el contenedor no arrancaba (TD-027).
FROM odoo:17.0-20260908
COPY entrypoint.sh init.sh harden.py /opt/lakehouse/
