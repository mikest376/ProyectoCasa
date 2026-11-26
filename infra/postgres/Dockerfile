# La configuración va dentro de la imagen: al reiniciarse Docker Desktop, los ficheros montados
# desde WSL todavía no existen y el contenedor no arrancaba (TD-027).
FROM postgres:15.20-bookworm@sha256:0cb925629e7eb35c77276f3165440d1670c35c2ddbe92b346c579299b75a520f
COPY postgresql.conf /etc/postgresql/postgresql.conf
COPY init/10-roles-and-database.sql /docker-entrypoint-initdb.d/10-roles-and-database.sql
