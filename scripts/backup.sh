#!/usr/bin/env bash
# Copia de seguridad fuera del repositorio (TD-017).
#
#   bash scripts/backup.sh [directorio]
#
# Por defecto, en ../backups/<fecha UTC>, junto al repositorio y fuera de él. Guarda:
#   odoo.dump, langfuse.dump   PostgreSQL de Odoo y de Langfuse (pg_dump, formato propio)
#   minio/                     buckets bronze, silver y gold (usuario backup, solo lectura)
#   unity-catalog.tar.gz       base de Unity Catalog, con el servicio parado unos segundos
#   odoo-filestore.tar.gz      adjuntos de Odoo
#   manifest.json, SHA256SUMS  recuentos y sumas para scripts/restore.sh --verify
#
# La copia lleva datos y hashes de contraseñas: se crea con permisos 700/600 (en una unidad
# de Windows los gobierna NTFS). .env no se copia: guárdalo en un gestor de contraseñas.
# Con los perfiles source, lakehouse y observability en marcha. Cada ejecución crea un
# directorio nuevo. Ningún secreto pasa por argumentos ni por la salida.
set -euo pipefail

ROOT=$(cd "$(dirname "$0")/.." && pwd)
cd "$ROOT"
target=${1:-$(dirname "$ROOT")/backups/$(date -u +%Y%m%dT%H%M%SZ)}
if [[ -e $target ]]; then
    echo "Ya existe $target: elige otro directorio" >&2
    exit 1
fi
umask 077
mkdir -p "$target/minio"
target=$(cd "$target" && pwd)
TOOLS_IMAGE=postgres:15.20-bookworm@sha256:0cb925629e7eb35c77276f3165440d1670c35c2ddbe92b346c579299b75a520f
WORKER_IMAGE=odoo-lakehouse/medallion-worker:dev

step() { printf '%s  %s\n' "$(date +%H:%M:%S)" "$1"; }
env_value() { sed -nE "s/^$1=//p" .env | tail -1 | tr -d "\"'"; }
volume() {
    docker compose --profile '*' config --format json | python3 -c "import json, sys; print(json.load(sys.stdin)['volumes']['$1']['name'])"
}
restart_catalog() { docker compose up -d --wait unity-catalog >/dev/null 2>&1 || echo "AVISO: arranca unity-catalog a mano" >&2; }

step "PostgreSQL de Odoo"
docker compose exec -T odoo-db sh -c 'exec pg_dump -U "$POSTGRES_USER" -d "$ODOO_DB_NAME" -Fc' >"$target/odoo.dump"
odoo_counts=$(docker compose exec -T odoo-db sh -c 'exec psql -X -At -U "$POSTGRES_USER" -d "$ODOO_DB_NAME"' <<'SQL'
SELECT json_build_object('res_partner', (SELECT count(*) FROM res_partner), 'sale_order', (SELECT count(*) FROM sale_order),
                         'account_move', (SELECT count(*) FROM account_move), 'crm_lead', (SELECT count(*) FROM crm_lead));
SQL
)

step "PostgreSQL de Langfuse"
docker compose exec -T langfuse-db sh -c 'exec pg_dump -U "$POSTGRES_USER" -d "$POSTGRES_DB" -Fc' >"$target/langfuse.dump"
langfuse_counts=$(docker compose exec -T langfuse-db sh -c 'exec psql -X -At -U "$POSTGRES_USER" -d "$POSTGRES_DB"' <<'SQL'
SELECT json_build_object('traces', (SELECT count(*) FROM traces), 'observations', (SELECT count(*) FROM observations));
SQL
)

step "MinIO: bronze, silver y gold"
MC_HOST_lake="http://$(env_value BACKUP_S3_ACCESS_KEY_ID):$(env_value BACKUP_S3_SECRET_ACCESS_KEY)@minio:9000" \
    docker compose run --rm -T --no-deps -e MC_HOST_lake -e MC_CONFIG_DIR=/tmp/mc --user "$(id -u):$(id -g)" \
    -v "$target/minio:/backup" --entrypoint /usr/bin/bash minio-init -c '
        set -e
        for bucket in "$S3_BUCKET_BRONZE" "$S3_BUCKET_SILVER" "$S3_BUCKET_GOLD"; do
            mc mirror --quiet "lake/$bucket" "/backup/$bucket" >/dev/null
        done' 2>&1 | grep -v -e Creating -e Created || true
delta_counts=$(docker run --rm --network none -v "$target/minio:/m:ro" --entrypoint python "$WORKER_IMAGE" -c '
import json, pathlib
import polars as pl
counts = {}
for bucket in sorted(p.name for p in pathlib.Path("/m").iterdir() if p.is_dir()):
    tables = sorted(d.parent for d in pathlib.Path("/m", bucket).rglob("_delta_log") if d.is_dir())
    counts[bucket] = {"tables": len(tables), "rows": sum(pl.read_delta(str(t)).height for t in tables)}
print(json.dumps(counts))')

step "Unity Catalog (parado mientras se copia)"
trap restart_catalog EXIT
docker compose stop unity-catalog >/dev/null 2>&1
docker run --rm --network none -v "$(volume unity-catalog-db):/data:ro" -v "$target:/backup" "$TOOLS_IMAGE" \
    tar czf /backup/unity-catalog.tar.gz -C /data .
restart_catalog
trap - EXIT

step "Adjuntos de Odoo"
docker run --rm --network none -v "$(volume odoo-data):/data:ro" -v "$target:/backup" "$TOOLS_IMAGE" \
    tar czf /backup/odoo-filestore.tar.gz -C /data .

step "Manifiesto y sumas"
# /mnt/e se vuelve a montar al reiniciar contenedores: la ruta absoluta no depende del directorio.
cd "$ROOT"
python3 - "$target/manifest.json" "$odoo_counts" "$langfuse_counts" "$delta_counts" "$(git -C "$ROOT" rev-parse --short HEAD)" <<'EOF'
import json, sys
from datetime import UTC, datetime
path, odoo, langfuse, delta, commit = sys.argv[1:]
manifest = {
    "created": datetime.now(tz=UTC).isoformat(timespec="seconds"),
    "commit": commit,
    "odoo": json.loads(odoo),
    "langfuse": json.loads(langfuse),
    "minio": json.loads(delta),
}
json.dump(manifest, open(path, "w"), indent=2)
EOF
sums=$(mktemp)
(cd "$target" && find . -type f -print0 | sort -z | xargs -0 sha256sum) >"$sums"
mv "$sums" "$target/SHA256SUMS"
chmod -R go-rwx "$target" 2>/dev/null || true

echo
echo "Copia en $target ($(du -sh "$target" | cut -f1))"
python3 -c 'import json, sys; m = json.load(open(sys.argv[1])); print("Odoo:", m["odoo"]); print("Langfuse:", m["langfuse"]); print("MinIO:", m["minio"])' "$target/manifest.json"
echo "Compruébala con: bash scripts/restore.sh --verify $target"
