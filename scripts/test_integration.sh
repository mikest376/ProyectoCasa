#!/usr/bin/env bash
# Pruebas de integración con MinIO, Kafka, Trino y Unity Catalog reales, en un proyecto de
# Compose aislado: credenciales, redes y volúmenes propios, que se eliminan al terminar. No toca
# .env ni los datos del proyecto.
#
#   scripts/test_integration.sh
#
# Comprueba los permisos de MinIO (TD-014), el recorrido Kafka -> Bronze con una caída a mitad de
# la escritura, y Bronze -> Silver -> Gold con los dos catálogos (TD-002). Necesita unos 4 GB.
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
TRINO_PORT_HOST=18080
UNITY_CATALOG_PORT_HOST=18081
EOF

dc() { docker compose --progress quiet -p "$project" --env-file "$env_file" --profile lakehouse --profile streaming --profile query "$@"; }

cleanup() {
    local status=$?
    ((status == 0)) || dc logs --tail 20 minio-init 2>/dev/null || true
    docker image rm "$project-worker-test" >/dev/null 2>&1 || true
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

# as <root|worker|trino|janitor> <orden de mc...>: las credenciales van por el entorno.
as() {
    local who=$1 user secret
    shift
    case $who in
    root) user=$(value MINIO_ROOT_USER) secret=$(value MINIO_ROOT_PASSWORD) ;;
    worker) user=$(value WORKER_S3_ACCESS_KEY_ID) secret=$(value WORKER_S3_SECRET_ACCESS_KEY) ;;
    trino) user=$(value TRINO_S3_ACCESS_KEY_ID) secret=$(value TRINO_S3_SECRET_ACCESS_KEY) ;;
    janitor) user=$(value MAINTENANCE_S3_ACCESS_KEY_ID) secret=$(value MAINTENANCE_S3_SECRET_ACCESS_KEY) ;;
    backup) user=$(value BACKUP_S3_ACCESS_KEY_ID) secret=$(value BACKUP_S3_SECRET_ACCESS_KEY) ;;
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
# Excepción acotada: Trino escribe su metastore, y solo eso (TD-002). El worker, no.
allowed trino cp /etc/os-release lake/gold/_trino_metastore/prueba.txt
denied worker cp /etc/os-release lake/gold/_trino_metastore/worker.txt
denied worker rm lake/gold/_trino_metastore/prueba.txt
allowed trino rm lake/gold/_trino_metastore/prueba.txt
# El mantenimiento borra en bronze/odoo, y en ningún otro sitio (TD-007).
allowed root cp /etc/os-release lake/bronze/odoo/tabla/viejo.parquet
allowed janitor cat lake/bronze/odoo/tabla/viejo.parquet
allowed janitor cp /etc/os-release lake/bronze/odoo/tabla/nuevo.parquet
denied worker rm lake/bronze/odoo/tabla/viejo.parquet
allowed janitor rm lake/bronze/odoo/tabla/viejo.parquet
allowed janitor rm lake/bronze/odoo/tabla/nuevo.parquet
denied janitor rm lake/bronze/root.txt
for bucket in silver gold; do
    denied janitor ls "lake/$bucket"
    denied janitor cat "lake/$bucket/root.txt"
    denied janitor cp /etc/os-release "lake/$bucket/janitor.txt"
    denied janitor rm "lake/$bucket/root.txt"
done
denied janitor admin user list lake
# Las copias de seguridad leen las tres capas y no escriben ni borran nada (TD-017).
for bucket in bronze silver gold; do
    allowed backup ls "lake/$bucket"
    allowed backup cat "lake/$bucket/root.txt"
    denied backup cp /etc/os-release "lake/$bucket/backup.txt"
    denied backup rm "lake/$bucket/root.txt"
done
denied backup admin user list lake

docker build -q --target test -t "$project-worker-test" services/medallion-worker >/dev/null

# run_tests <fichero>: con la imagen y el límite de memoria del worker.
run_tests() {
    S3_SECRET_ACCESS_KEY=$(value WORKER_S3_SECRET_ACCESS_KEY) \
        MAINTENANCE_S3_SECRET_ACCESS_KEY=$(value MAINTENANCE_S3_SECRET_ACCESS_KEY) \
        docker run --rm --network "${project}_lakehouse" \
        --memory 768m --memory-swap 768m -e POLARS_MAX_THREADS=4 \
        -e LAKEHOUSE_INTEGRATION=1 -e KAFKA_BOOTSTRAP_SERVERS=kafka:19092 \
        -e S3_ENDPOINT_URL=http://minio:9000 -e S3_SECRET_ACCESS_KEY \
        -e S3_ACCESS_KEY_ID="$(value WORKER_S3_ACCESS_KEY_ID)" \
        -e MAINTENANCE_S3_SECRET_ACCESS_KEY \
        -e MAINTENANCE_S3_ACCESS_KEY_ID="$(value MAINTENANCE_S3_ACCESS_KEY_ID)" \
        "$project-worker-test" uv run --no-sync pytest -q -m integration "tests/$1"
}

echo "Kafka -> Bronze"
if run_tests test_integration.py; then report 0 "ingesta"; else report 1 "ingesta"; fi

echo "Bronze -> Silver -> Gold -> Trino y Unity Catalog"
dc up -d --build --wait trino unity-catalog
if run_tests test_integration_lakehouse.py; then report 0 "transformación y catálogos"; else
    report 1 "transformación y catálogos"
fi

printf '\nResultado: %d fallos\n' "$failures"
((failures == 0))
