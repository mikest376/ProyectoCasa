# La configuración va dentro de la imagen: al reiniciarse Docker Desktop, los ficheros montados
# desde WSL todavía no existen y el contenedor no arrancaba (TD-027).
FROM trinodb/trino:484@sha256:2704eb53935af5e582d63d6033e9bf99d20c325e9b0e30b1016ef59a4773979f
# Sin los catálogos de ejemplo: solo el lakehouse.
RUN rm -f /etc/trino/catalog/*
COPY config.properties jvm.config access-control.properties access-control.json /etc/trino/
COPY catalog/lakehouse.properties /etc/trino/catalog/lakehouse.properties
