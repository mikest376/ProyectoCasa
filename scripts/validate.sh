#!/usr/bin/env bash
# Validación completa. Usa contenedores oficiales: no instala nada en el equipo, no usa .env y
# no arranca los servicios del proyecto. Es lo que ejecuta la integración continua.
#
#   scripts/validate.sh            todo
#   scripts/validate.sh --quick    sin construir las imágenes propias
set -uo pipefail
ROOT=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd) || exit 2
cd "$ROOT" || exit 2

SHELLCHECK_IMAGE=koalaman/shellcheck:v0.11.0@sha256:61862eba1fcf09a484ebcc6feea46f1782532571a34ed51fedf90dd25f925a8d
HADOLINT_IMAGE=hadolint/hadolint:v2.15.1-alpine@sha256:a1d49ae1a4e83c1dbad26b8c1ad7588c8bd1e04f4866b34ad3cac50335198552
ACTIONLINT_IMAGE=rhysd/actionlint:1.7.12@sha256:b1934ee5f1c509618f2508e6eb47ee0d3520686341fec936f3b79331f9315667
GITLEAKS_IMAGE=zricethezav/gitleaks:v8.30.1@sha256:c00b6bd0aeb3071cbcb79009cb16a60dd9e0a7c60e2be9ab65d25e6bc8abbb7f
export CADDY_IMAGE=caddy:2.11.9-alpine@sha256:003a97808e781aa9f7e75cc2f43d34d461099031faf5e20faefb9f61126836f0

failures=0
run() {
    local name=$1 output
    shift
    # En WSL, Docker Desktop vuelve a montar la unidad de Windows la primera vez que la usa tras
    # reiniciarse, y el directorio actual deja de existir: se entra de nuevo por la ruta absoluta.
    cd "$ROOT" || exit 2
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
sys.exit(total > 14336)'
}

# Las credenciales raíz de MinIO no salen de minio y minio-init (TD-014).
minio_root() {
    docker compose --env-file .env.example --profile "*" config --format json | python3 -c '
import json, sys
services = json.load(sys.stdin)["services"]
holders = {n for n, s in services.items() if {"MINIO_ROOT_USER", "MINIO_ROOT_PASSWORD", "MC_HOST_lake"} & set(s.get("environment") or {})}
print(holders)
sys.exit(holders != {"minio", "minio-init"})'
}

python_scripts() {
    python3 - <<'EOF'
import ast
import json
import pathlib

scripts = pathlib.Path("scripts")
for path in [*scripts.glob("*.py"), *scripts.glob("tests/*.py"), pathlib.Path("infra/odoo/harden.py")]:
    ast.parse(path.read_text(encoding="utf-8"), str(path))
for path in ["infra/debezium/odoo-postgres-cdc.json", *pathlib.Path("infra/minio/policies").glob("*.json")]:
    json.loads(pathlib.Path(path).read_text(encoding="utf-8"))
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

gitleaks() {
    docker run --rm --network none "$@"
}

# Todo el historial. Los hallazgos se muestran sin el secreto (--redact).
secrets_history() {
    gitleaks -v "$mount" \
        -e GIT_CONFIG_COUNT=1 -e GIT_CONFIG_KEY_0=safe.directory -e GIT_CONFIG_VALUE_0=/repo \
        "$GITLEAKS_IMAGE" git /repo --redact --no-banner --verbose
}

# Lo que se podría subir ahora: versionado o nuevo, sin lo ignorado (.env no se copia).
secrets_files() {
    local copy status=0
    copy=$(mktemp -d)
    git ls-files -z --cached --others --exclude-standard | while IFS= read -r -d '' file; do
        [[ ! -f $file ]] || cp --parents -- "$file" "$copy"
    done
    chmod -R a+rX "$copy"
    gitleaks -v "$copy:/files:ro" "$GITLEAKS_IMAGE" dir /files --redact --no-banner --verbose || status=$?
    rm -rf "$copy"
    return "$status"
}

run "compose: sintaxis" docker compose --env-file .env.example --profile "*" config --quiet
run "compose: presupuesto de memoria" budget
run "compose: credenciales raíz de MinIO" minio_root
run "imágenes: fijadas por digest (TD-024)" bash scripts/pin_digests.sh
run "scripts: shellcheck" docker run --rm --network none -v "$mount" -w /repo "$SHELLCHECK_IMAGE" \
    scripts/*.sh scripts/tests/*.sh infra/odoo/entrypoint.sh infra/odoo/init.sh infra/minio/init.sh
run "scripts: python y JSON" python_scripts
run "integración continua: actionlint" docker run --rm --network none -v "$mount" -w /repo \
    "$ACTIONLINT_IMAGE" -no-color .github/workflows/ci.yml
run "hadolint" docker run --rm --network none -v "$mount" -w /repo "$HADOLINT_IMAGE" \
    hadolint services/*/Dockerfile infra/*/Dockerfile
run "secretos: ficheros" secrets_files
run "secretos: historial" secrets_history
run "caddy: formato" docker run --rm --network none -v "$mount" "$CADDY_IMAGE" \
    caddy fmt --diff /repo/infra/caddy/Caddyfile
run "caddy: configuración" caddy_config
run "caddy: un solo nombre público" caddy_config -e CADDY_TLS=admin@example.org \
    -e CADDY_APP_HOST=tfg.example.org -e CADDY_ODOO_HOST=tfg.example.org:8443
run "caddy: contraseña, rutas bloqueadas y cookies (TD-022)" bash scripts/tests/test_caddy.sh
run "cdc: identidad de réplica y secretos (TD-004, TD-005)" python3 scripts/tests/test_cdc.py

if [[ ${1:-} != --quick ]]; then
    run "worker: lint, tipos y pruebas" docker build -q --target test services/medallion-worker
    run "backend: lint, tipos y pruebas" docker build -q --target test services/backend
    run "frontend: lint, tipos y compilación" docker build -q --target build services/frontend
    run "compose: imágenes propias" docker compose --env-file .env.example --profile apps build -q
fi

printf '\nResultado: %d fallos\n' "$failures"
((failures == 0))
