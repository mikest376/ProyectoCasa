#!/usr/bin/env bash
# Prueba funcional del Caddyfile real con servicios simulados (TD-022): cookies con Secure,
# contraseña en todas las rutas, rutas de Odoo bloqueadas y cabeceras que no se reenvían.
#
# Red interna y desechable, sin puertos publicados: no usa .env ni los servicios del proyecto.
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/../.."

CADDY_IMAGE=${CADDY_IMAGE:-caddy:2.11.9-alpine}
PYTHON_IMAGE=${PYTHON_IMAGE:-python:3.13.18-slim-bookworm}
run_id=caddy-test-$$
password=$(od -An -N16 -tx1 /dev/urandom | tr -d ' \n')

cleanup() {
    docker rm -f "$run_id-caddy" "$run_id-upstream" >/dev/null 2>&1 || true
    docker network rm "$run_id" >/dev/null 2>&1 || true
}
trap cleanup EXIT

hash=$(printf '%s\n' "$password" | docker run --rm -i --network none "$CADDY_IMAGE" caddy hash-password)

docker network create --internal "$run_id" >/dev/null
docker run -d --name "$run_id-upstream" --network "$run_id" --memory 128m \
    --network-alias odoo --network-alias backend --network-alias frontend \
    -v "$PWD/scripts/tests:/tests:ro" "$PYTHON_IMAGE" python /tests/caddy_probe.py serve >/dev/null
docker run -d --name "$run_id-caddy" --network "$run_id" --memory 128m \
    --network-alias app.localhost --network-alias odoo.localhost \
    --read-only --tmpfs /data --tmpfs /config \
    --cap-drop ALL --cap-add NET_BIND_SERVICE --security-opt no-new-privileges:true \
    -e CADDY_TLS=internal -e CADDY_BASIC_AUTH_USER=ejemplo -e CADDY_BASIC_AUTH_HASH="$hash" \
    -v "$PWD/infra/caddy/Caddyfile:/etc/caddy/Caddyfile:ro" "$CADDY_IMAGE" >/dev/null

if ! PROBE_PASSWORD=$password docker run --rm --network "$run_id" --memory 128m \
    -e PROBE_PASSWORD -v "$PWD/scripts/tests:/tests:ro" "$PYTHON_IMAGE" \
    python /tests/caddy_probe.py check; then
    docker logs --tail 20 "$run_id-caddy" >&2 || true
    exit 1
fi
