#!/usr/bin/env bash
# Validación estática completa. Usa contenedores oficiales: no instala nada en el equipo y
# no arranca los servicios del proyecto.
#
#   scripts/validate.sh            todo
#   scripts/validate.sh --quick    sin construir las imágenes propias
set -uo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/.." || exit 2

SHELLCHECK_IMAGE=koalaman/shellcheck:v0.11.0
HADOLINT_IMAGE=hadolint/hadolint:v2.15.1-alpine
CADDY_IMAGE=caddy:2.11.9-alpine

failures=0
run() {
    local name=$1 output
    shift
    if output=$("$@" 2>&1); then
        printf '  \033[32mOK\033[0m     %s\n' "$name"
    else
        printf '  \033[31mFALLO\033[0m  %s\n' "$name"
        tail -n 25 <<<"$output" | sed 's/^/         /'
        failures=$((failures + 1))
    fi
}

mount="$PWD:/repo:ro"

budget() {
    docker compose --env-file .env.example --profile "*" config --format json | python3 -c '
import json, sys
services = json.load(sys.stdin)["services"]
total = sum(int(s["deploy"]["resources"]["limits"]["memory"]) for n, s in services.items() if not n.endswith("-init")) // 2**20
print(total)
sys.exit(total > 20480)'
}

python_scripts() {
    python3 - <<'EOF'
import ast
import json
import pathlib

for path in [*pathlib.Path("scripts").glob("*.py"), pathlib.Path("infra/odoo/harden.py")]:
    ast.parse(path.read_text(encoding="utf-8"), str(path))
json.loads(pathlib.Path("infra/debezium/odoo-postgres-cdc.json").read_text(encoding="utf-8"))
EOF
}

# Argumentos opcionales: variables adicionales, como -e CADDY_ODOO_HOST=...
caddy_config() {
    local hash
    hash=$(docker run --rm --network none "$CADDY_IMAGE" caddy hash-password --plaintext solo-para-validar)
    docker run --rm --network none -v "$mount" "$@" \
        -e CADDY_BASIC_AUTH_USER=ejemplo -e CADDY_BASIC_AUTH_HASH="$hash" "$CADDY_IMAGE" \
        caddy validate --config /repo/infra/caddy/Caddyfile --adapter caddyfile
}

run "compose: sintaxis" docker compose --env-file .env.example --profile "*" config --quiet
run "compose: presupuesto de memoria" budget
run "scripts: shellcheck" docker run --rm --network none -v "$mount" -w /repo "$SHELLCHECK_IMAGE" \
    scripts/check_env.sh scripts/init_odoo.sh scripts/validate.sh infra/odoo/entrypoint.sh infra/odoo/init.sh
run "scripts: python y JSON" python_scripts
for service in medallion-worker backend frontend; do
    run "hadolint: $service" docker run --rm --network none -v "$mount" -w /repo "$HADOLINT_IMAGE" \
        hadolint "services/$service/Dockerfile"
done
run "caddy: formato" docker run --rm --network none -v "$mount" "$CADDY_IMAGE" \
    caddy fmt --diff /repo/infra/caddy/Caddyfile
run "caddy: configuración" caddy_config
run "caddy: un solo nombre público" caddy_config -e CADDY_TLS=admin@example.org \
    -e CADDY_APP_HOST=tfg.example.org -e CADDY_ODOO_HOST=tfg.example.org:8443

if [[ ${1:-} != --quick ]]; then
    run "worker: lint, tipos y pruebas" docker build -q --target test services/medallion-worker
    run "backend: lint, tipos y pruebas" docker build -q --target test services/backend
    run "frontend: lint, tipos y compilación" docker build -q --target build services/frontend
    run "compose: imágenes propias" docker compose --env-file .env.example --profile apps build -q
fi

printf '\nResultado: %d fallos\n' "$failures"
((failures == 0))
