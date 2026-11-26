# El Caddyfile va dentro de la imagen: al reiniciarse Docker Desktop, los ficheros montados
# desde WSL todavía no existen y el contenedor no arrancaba (TD-027).
FROM caddy:2.11.9-alpine
COPY Caddyfile /etc/caddy/Caddyfile
