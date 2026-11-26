#!/usr/bin/env bash
# Restauración de una copia de scripts/backup.sh (TD-017).
#
#   bash scripts/restore.sh --verify <directorio>   prueba la copia sin tocar el sistema
#   bash scripts/restore.sh <directorio> --yes      restaura en el sistema en marcha (destructivo)
#
# --verify comprueba las sumas, restaura los dos PostgreSQL en contenedores desechables y sin
# red, lee todas las tablas Delta de la copia y revisa los tar, y compara con los recuentos
# del manifiesto. Solo lee la copia.
#
# Sin --verify sustituye el estado actual: PostgreSQL de Odoo y de Langfuse, buckets de MinIO
# (con las credenciales de minio-init, el único con las raíz), Unity Catalog y adjuntos.
# Detiene antes los servicios que escriben. Tras restaurar Odoo, el CDC envía las filas
# restauradas como cambios nuevos: Silver se queda con la versión más reciente de cada una.
set -euo pipefail

ROOT=$(cd "$(dirname "$0")/.." && pwd)
cd "$ROOT"
TOOLS_IMAGE=postgres:15.20-bookworm@sha256:0cb925629e7eb35c77276f3165440d1670c35c2ddbe92b346c579299b75a520f
WORKER_IMAGE=odoo-lakehouse/medallion-worker:dev

usage() { sed -n '3,5p' "$0" >&2; exit 2; }
mode=restore
case ${1:-} in
--verify) mode=verify; source=${2:-} ;;
*) source=${1:-}; [[ ${2:-} == --yes ]] || usage ;;
esac
[[ -n $source && -f $source/manifest.json && -f $source/SHA256SUMS ]] || usage
source=$(cd "$source" && pwd)

failures=0
report() {
    if [[ $1 == 0 ]]; then printf '  OK     %s\n' "$2"; else printf '  FALLO  %s\n' "$2"; failures=$((failures + 1)); fi
}
expected() {
    python3 -c 'import json, sys; v = json.load(open(sys.argv[1]))[sys.argv[2]]; print(v if isinstance(v, str) else json.dumps(v, sort_keys=True))' \
        "$source/manifest.json" "$1"
}
containers=()
cleanup() {
    local container
    for container in "${containers[@]}"; do docker rm -f -v "$container" >/dev/null 2>&1 || true; done
}
trap cleanup EXIT
same() { if [[ $1 == "$2" ]]; then echo 0; else echo 1; fi; }
volume() {
    docker compose --profile '*' config --format json | python3 -c "import json, sys; print(json.load(sys.stdin)['volumes']['$1']['name'])"
}

# restore_dump <fichero> <consulta de recuentos>: PostgreSQL desechable, sin red ni disco.
restore_dump() {
    local dump=$1 query=$2 name="restore-verify-$$-$RANDOM" output
    containers+=("$name")
    # La contraseña llega por el entorno de este proceso, no como argumento.
    POSTGRES_PASSWORD=$(od -An -N16 -tx1 /dev/urandom | tr -d ' \n') docker run -d --name "$name" --network none \
        --memory 512m --tmpfs /var/lib/postgresql/data -e POSTGRES_PASSWORD "$TOOLS_IMAGE" >/dev/null
    for _ in $(seq 1 60); do
        docker exec "$name" pg_isready -h 127.0.0.1 -U postgres >/dev/null 2>&1 && break
        sleep 1
    done
    output=$(docker exec "$name" createdb -U postgres restored &&
        docker exec -i "$name" pg_restore -U postgres -d restored --no-owner --no-privileges --exit-on-error <"$dump" &&
        docker exec -i "$name" psql -X -At -U postgres -d restored <<<"$query") || output="no se pudo restaurar"
    docker rm -f -v "$name" >/dev/null 2>&1 || true
    printf '%s\n' "$output"
}

verify() {
    echo "Copia de $(expected created) (commit $(expected commit))"
    echo "Sumas"
    (cd "$source" && sha256sum --quiet -c SHA256SUMS) >/dev/null 2>&1
    report $? "$(wc -l <"$source/SHA256SUMS") ficheros sin alterar"

    echo "PostgreSQL, restaurado en contenedores desechables"
    local actual
    actual=$(restore_dump "$source/odoo.dump" "SELECT json_build_object('res_partner', (SELECT count(*) FROM res_partner), 'sale_order', (SELECT count(*) FROM sale_order), 'account_move', (SELECT count(*) FROM account_move), 'crm_lead', (SELECT count(*) FROM crm_lead));" 2>&1 | tail -1)
    actual=$(python3 -c 'import json, sys; print(json.dumps(json.loads(sys.argv[1]), sort_keys=True))' "$actual" 2>/dev/null || echo "$actual")
    report "$(same "$actual" "$(expected odoo)")" "Odoo: $actual"
    actual=$(restore_dump "$source/langfuse.dump" "SELECT json_build_object('traces', (SELECT count(*) FROM traces), 'observations', (SELECT count(*) FROM observations));" 2>&1 | tail -1)
    actual=$(python3 -c 'import json, sys; print(json.dumps(json.loads(sys.argv[1]), sort_keys=True))' "$actual" 2>/dev/null || echo "$actual")
    report "$(same "$actual" "$(expected langfuse)")" "Langfuse: $actual"

    echo "MinIO: todas las tablas Delta de la copia, leídas"
    actual=$(docker run --rm --network none -v "$source/minio:/m:ro" --entrypoint python "$WORKER_IMAGE" -c '
import json, pathlib
import polars as pl
counts = {}
for bucket in sorted(p.name for p in pathlib.Path("/m").iterdir() if p.is_dir()):
    tables = sorted(d.parent for d in pathlib.Path("/m", bucket).rglob("_delta_log") if d.is_dir())
    counts[bucket] = {"tables": len(tables), "rows": sum(pl.read_delta(str(t)).height for t in tables)}
print(json.dumps(counts, sort_keys=True))' 2>&1 | tail -1)
    report "$(same "$actual" "$(expected minio)")" "MinIO: $actual"

    echo "Archivos"
    for archive in unity-catalog odoo-filestore; do
        files=$(tar -tzf "$source/$archive.tar.gz" 2>/dev/null | wc -l)
        report "$(same "$((files > 0))" 1)" "$archive.tar.gz: $files entradas legibles"
    done
    echo
    echo "Resultado: $failures fallos"
    ((failures == 0))
}

restore() {
    echo "Restaurando $source sobre el sistema en marcha"
    local writers=(odoo kafka-connect langfuse medallion-worker medallion-transform bronze-maintenance unity-catalog)
    (cd "$source" && sha256sum --quiet -c SHA256SUMS) || { echo "La copia está alterada: no se restaura" >&2; exit 1; }
    docker compose stop "${writers[@]}"
    docker compose exec -T odoo-db sh -c 'exec pg_restore -U "$POSTGRES_USER" -d "$ODOO_DB_NAME" --clean --if-exists --exit-on-error' <"$source/odoo.dump"
    docker compose exec -T langfuse-db sh -c 'exec pg_restore -U "$POSTGRES_USER" -d "$POSTGRES_DB" --clean --if-exists --exit-on-error' <"$source/langfuse.dump"
    docker compose run --rm -T --no-deps -v "$source/minio:/backup:ro" --entrypoint /usr/bin/bash minio-init -c '
        set -e
        for bucket in "$S3_BUCKET_BRONZE" "$S3_BUCKET_SILVER" "$S3_BUCKET_GOLD"; do
            mc mirror --quiet --overwrite --remove "/backup/$bucket" "lake/$bucket" >/dev/null
        done'
    for pair in "unity-catalog-db:unity-catalog" "odoo-data:odoo-filestore"; do
        docker run --rm --network none -v "$(volume "${pair%%:*}"):/data" -v "$source:/backup:ro" "$TOOLS_IMAGE" \
            sh -c "find /data -mindepth 1 -delete && tar xzf /backup/${pair##*:}.tar.gz -C /data"
    done
    docker compose up -d --wait "${writers[@]}"
    echo "Restaurado. Comprueba el CDC con: python3 scripts/cdc.py status"
}

if [[ $mode == verify ]]; then verify; else restore; fi
