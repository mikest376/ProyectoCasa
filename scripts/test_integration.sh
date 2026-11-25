#!/usr/bin/env bash
# Pruebas de integración de la Fase 2 con MinIO y Kafka reales, en un proyecto de Compose
# aislado: credenciales, redes y volúmenes propios, que se eliminan al terminar. No toca .env
# ni los datos del proyecto.
#
#   scripts/test_integration.sh
#
# Comprueba los permisos de MinIO (TD-014) y el recorrido Kafka -> worker -> Delta, con una
# caída a mitad de la escritura. Necesita unos 2,5 GB de RAM.
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/.."

project=lakehouse-it
env_file=$(mktemp)
random() { od -An -N20 -tx1 /dev/urandom | tr -d ' \n'; }

# .env.example con contraseñas de un solo uso y puertos distintos de los del proyecto.
while IFS= read -r line; do
    [[ $line == *CHANGE_ME* && $line != \#* ]] && line=${line//CHANGE_ME/$(random)}
    printf '%s\n' "$line"
done <.env.example >"$env_file"
cat >>"$env_file" <<EOF
MINIO_ROOT_USER=integration-root
MINIO_API_PORT_HOST=19000
MINIO_CONSOLE_PORT_HOST=19001
KAFKA_PORT_HOST=19092
EOF

dc() { docker compose --progress quiet -p "$project" --env-file "$env_file" --profile lakehouse --profile streaming "$@"; }

cleanup() {
    local status=$?
    ((status == 0)) || dc logs --tail 20 minio-init 2>/dev/null || true
    dc down -v --remove-orphans >/dev/null 2>&1 || true
    rm -f "$env_file"
    exit "$status"
}
trap cleanup EXIT

value() { sed -nE "s/^$1=//p" "$env_file" | tail -1; }

failures=0
report() {
    if [[ $1 == 0 ]]; then printf '  OK     %s\n' "$2"; else
        printf '  FALLO  %s\n' "$2"
        failures=$((failures + 1))
    fi
}

echo "Arranque de MinIO y Kafka"
dc up -d --wait minio kafka
dc up --no-log-prefix --exit-code-from minio-init minio-init
# Repetible: una segunda ejecución no falla.
dc up --no-log-prefix --exit-code-from minio-init --force-recreate minio-init >/dev/null

# as <root|worker|trino> <orden de mc...>: las credenciales van por el entorno.
as() {
    local who=$1 user secret
    shift
    case $who in
    root) user=$(value MINIO_ROOT_USER) secret=$(value MINIO_ROOT_PASSWORD) ;;
    worker) user=$(value WORKER_S3_ACCESS_KEY_ID) secret=$(value WORKER_S3_SECRET_ACCESS_KEY) ;;
    trino) user=$(value TRINO_S3_ACCESS_KEY_ID) secret=$(value TRINO_S3_SECRET_ACCESS_KEY) ;;
    esac
    MC_HOST_lake="http://$user:$secret@minio:9000" dc run --rm -T --no-deps \
        -e MC_HOST_lake --entrypoint mc minio-init "$@" >/dev/null 2>&1
}
allowed() { if as "$@"; then report 0 "$1 puede: ${*:2}"; else report 1 "$1 debería poder: ${*:2}"; fi; }
denied() { if as "$@"; then report 1 "$1 NO debería poder: ${*:2}"; else report 0 "$1 no puede: ${*:2}"; fi; }

echo "Permisos de MinIO"
for bucket in bronze silver gold; do
    allowed root cp /etc/os-release "lake/$bucket/root.txt"
    allowed worker cp /etc/os-release "lake/$bucket/worker.txt"
    allowed worker cat "lake/$bucket/root.txt"
    allowed worker ls "lake/$bucket"
done
denied worker rm lake/bronze/root.txt
denied worker rm lake/bronze/worker.txt
allowed worker rm lake/silver/worker.txt
allowed worker rm lake/gold/worker.txt
denied worker mb lake/otro
denied worker admin user list lake
# --force vacía el bucket antes de intentar eliminarlo: el bucket sigue, los objetos no.
denied worker rb --force lake/silver
allowed root cp /etc/os-release lake/silver/root.txt
for bucket in silver gold; do
    allowed trino ls "lake/$bucket"
    allowed trino cat "lake/$bucket/root.txt"
    denied trino cp /etc/os-release "lake/$bucket/trino.txt"
    denied trino rm "lake/$bucket/root.txt"
done
denied trino ls lake/bronze
denied trino cat lake/bronze/root.txt
denied trino cp /etc/os-release lake/bronze/trino.txt
denied trino admin user list lake

echo "Kafka -> worker -> Delta"
docker build -q --target test -t "$project-worker-test" services/medallion-worker >/dev/null
WORKER_S3_SECRET=$(value WORKER_S3_SECRET_ACCESS_KEY)
if S3_SECRET_ACCESS_KEY=$WORKER_S3_SECRET docker run --rm --network "${project}_lakehouse" \
    --memory 1536m --memory-swap 1536m -e POLARS_MAX_THREADS=4 \
    -e LAKEHOUSE_INTEGRATION=1 -e KAFKA_BOOTSTRAP_SERVERS=kafka:19092 \
    -e S3_ENDPOINT_URL=http://minio:9000 -e S3_SECRET_ACCESS_KEY \
    -e S3_ACCESS_KEY_ID="$(value WORKER_S3_ACCESS_KEY_ID)" \
    "$project-worker-test" uv run --no-sync pytest -q -m integration tests/test_integration.py; then
    report 0 "pruebas de integración del worker"
else
    report 1 "pruebas de integración del worker"
fi
docker image rm "$project-worker-test" >/dev/null 2>&1 || true

printf '\nResultado: %d fallos\n' "$failures"
((failures == 0))
