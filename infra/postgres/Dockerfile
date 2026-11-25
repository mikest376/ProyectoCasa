# La configuración va dentro de la imagen: al reiniciarse Docker Desktop, los ficheros montados
# desde WSL todavía no existen y el contenedor no arrancaba (TD-027).
FROM postgres:15.20-bookworm
COPY postgresql.conf /etc/postgresql/postgresql.conf
COPY init/10-roles-and-database.sql /docker-entrypoint-initdb.d/10-roles-and-database.sql
