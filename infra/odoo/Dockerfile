# Los scripts van dentro de la imagen: al reiniciarse Docker Desktop, los ficheros montados
# desde WSL todavía no existen y el contenedor no arrancaba (TD-027).
FROM odoo:17.0-20260908@sha256:8779f8157ddcc37b84014c94646073f46fd4ad776b7a5d95fc3b0dc73a24a8fa
COPY entrypoint.sh init.sh harden.py /opt/lakehouse/
