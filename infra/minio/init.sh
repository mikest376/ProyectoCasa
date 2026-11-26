#!/usr/bin/bash
# Crea los buckets Medallion y un usuario por servicio con su política mínima (TD-014).
# Se puede repetir: actualiza políticas y contraseñas. Los secretos llegan por el entorno
# y por la entrada estándar, nunca como argumentos. La imagen solo trae bash, mc y coreutils.
set -euo pipefail

POLICY_DIR=/opt/lakehouse/policies

# MC_HOST_lake (en docker-compose.yml) lleva las credenciales raíz.
for bucket in "$S3_BUCKET_BRONZE" "$S3_BUCKET_SILVER" "$S3_BUCKET_GOLD"; do
    mc mb --ignore-existing "lake/$bucket"
done

# apply_policy <nombre>: sustituye los nombres de los buckets y crea o actualiza la política.
apply_policy() {
    local name=$1 document rendered
    document=$(<"$POLICY_DIR/$name.json")
    document=${document//@BRONZE@/$S3_BUCKET_BRONZE}
    document=${document//@SILVER@/$S3_BUCKET_SILVER}
    document=${document//@GOLD@/$S3_BUCKET_GOLD}
    rendered=/tmp/$name.json
    printf '%s\n' "$document" >"$rendered"
    mc admin policy create lake "$name" "$rendered"
}

# apply_user <usuario> <contraseña> <política>
apply_user() {
    local user=$1 secret=$2 policy=$3
    if [[ $user == "$MINIO_ROOT_USER" ]]; then
        echo "El usuario de $policy no puede ser el raíz de MinIO" >&2
        exit 1
    fi
    printf '%s\n%s\n' "$user" "$secret" | mc admin user add lake
    # attach falla si la política ya estaba asignada: lo que cuenta es la comprobación posterior.
    mc admin policy attach lake "$policy" --user "$user" >/dev/null 2>&1 || true
    if [[ $(mc admin user info lake "$user" --json) != *"\"policyName\":\"$policy\""* ]]; then
        echo "El usuario $user no tiene exactamente la política $policy" >&2
        exit 1
    fi
    echo "Usuario $user: solo la política $policy"
}

apply_policy medallion-worker
apply_policy trino
apply_policy bronze-maintenance
apply_user "$WORKER_S3_ACCESS_KEY_ID" "$WORKER_S3_SECRET_ACCESS_KEY" medallion-worker
apply_user "$TRINO_S3_ACCESS_KEY_ID" "$TRINO_S3_SECRET_ACCESS_KEY" trino
apply_user "$MAINTENANCE_S3_ACCESS_KEY_ID" "$MAINTENANCE_S3_SECRET_ACCESS_KEY" bronze-maintenance
