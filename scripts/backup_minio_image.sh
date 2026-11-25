#!/usr/bin/env bash
# Copia local de la imagen de MinIO fijada por digest en docker-compose.yml (TD-003).
#
#   scripts/backup_minio_image.sh save      guarda la imagen comprimida y su suma SHA-256
#   scripts/backup_minio_image.sh verify    comprueba la copia sin cargarla
#   scripts/backup_minio_image.sh restore   la carga en Docker
#
# Destino: ../backups, fuera del repositorio. Se cambia con LAKEHOUSE_BACKUP_DIR.
# No guarda nada si dejaría la unidad de destino con menos de 10 GiB libres.
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/.."

MIN_FREE_MIB=10240
BACKUP_DIR=${LAKEHOUSE_BACKUP_DIR:-$PWD/../backups}

image=$(docker compose --env-file .env.example --profile lakehouse config --format json |
    python3 -c 'import json, sys; print(json.load(sys.stdin)["services"]["minio"]["image"])')
digest=${image##*@sha256:}
if [[ $digest == "$image" ]]; then
    echo "La imagen de MinIO no está fijada por digest: $image" >&2
    exit 1
fi
name=minio-${digest:0:12}.tar.gz

free_mib() { df -BM --output=avail "$1" | tail -1 | tr -dc '0-9'; }
mib() { echo $(($1 / 1048576)); }

# Con comprobaciones explícitas: dentro de una condición, set -e no actúa.
verify() {
    local content
    (cd "$BACKUP_DIR" && sha256sum --check --quiet "$name.sha256") || return 1
    content=$(tar -tzf "$BACKUP_DIR/$name") || return 1
    # La copia contiene exactamente el manifiesto que pide docker-compose.yml.
    if ! grep -qx "blobs/sha256/$digest" <<<"$content"; then
        echo "La copia no contiene el manifiesto sha256:$digest" >&2
        return 1
    fi
    echo "Copia correcta: $BACKUP_DIR/$name ($(mib "$(stat -c %s "$BACKUP_DIR/$name")") MiB)"
}

case ${1:-} in
save)
    mkdir -p "$BACKUP_DIR"
    if [[ -f $BACKUP_DIR/$name && -f $BACKUP_DIR/$name.sha256 ]] && verify; then
        echo "Ya existe y es válida: no se repite."
        exit 0
    fi
    docker image inspect "$image" >/dev/null 2>&1 || docker pull --quiet "$image"
    size=$(docker image inspect --format '{{.Size}}' "$image")
    free=$(free_mib "$BACKUP_DIR")
    if ((free - $(mib "$size") < MIN_FREE_MIB)); then
        echo "Espacio insuficiente en $BACKUP_DIR: $free MiB libres" >&2
        exit 1
    fi
    # Nombre temporal: un corte no deja una copia a medias con el nombre bueno.
    trap 'rm -f "$BACKUP_DIR/$name.part"' EXIT
    docker save "$image" | gzip -9 >"$BACKUP_DIR/$name.part"
    mv "$BACKUP_DIR/$name.part" "$BACKUP_DIR/$name"
    (cd "$BACKUP_DIR" && sha256sum "$name" >"$name.sha256")
    verify
    echo "Sin comprimir: $(mib "$size") MiB. Libres en el destino: $(free_mib "$BACKUP_DIR") MiB"
    ;;
verify)
    verify
    ;;
restore)
    verify
    gzip -dc "$BACKUP_DIR/$name" | docker load
    # docker load la recupera sin nombre: sin etiqueta, Compose no la encuentra por digest.
    docker tag "sha256:$digest" "${image%@*}"
    docker image inspect --format 'Disponible para Compose: {{.Id}}' "$image"
    ;;
*)
    sed -n '2,9s/^# \{0,1\}//p' "${BASH_SOURCE[0]}"
    exit 2
    ;;
esac
