#!/usr/bin/env bash
# Diagnóstico del entorno. SOLO LEE: no cambia nada del sistema ni del proyecto.
#
#   scripts/check_env.sh          WSL, Docker, espacio en disco y configuración
#   scripts/check_env.sh --gpu    además prueba la GPU en un contenedor
#
# Termina con error si algo impide trabajar. Las soluciones están en docs/SETUP_WSL.md.
set -uo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/.." || exit 2

MIN_RAM_MIB=15500
MIN_DISK_GIB=20
MAX_BUDGET_MIB=14336
GPU_IMAGE=postgres:15.20-bookworm

failures=0
warnings=0
ok() { printf '  \033[32mOK\033[0m     %s\n' "$*"; }
warn() { printf '  \033[33mAVISO\033[0m  %s\n' "$*"; warnings=$((warnings + 1)); }
fail() { printf '  \033[31mFALLO\033[0m  %s\n' "$*"; failures=$((failures + 1)); }
title() { printf '\n%s\n' "$*"; }

title "WSL"
if grep -qi microsoft /proc/version; then ok "WSL 2 ($(uname -r))"; else warn "No parece WSL: el resto del proyecto asume WSL 2"; fi

if [[ $(id -u) -eq 0 ]]; then
    warn "Estás trabajando como root. Configura [user] default en /etc/wsl.conf"
else
    ok "Usuario $(id -un)"
fi

if [[ -r /etc/wsl.conf ]]; then
    # Solo se admiten secciones, claves, comentarios y líneas vacías.
    invalid=$(grep -vnE '^[[:space:]]*(#.*)?$|^[[:space:]]*\[[A-Za-z]+\][[:space:]]*$|^[[:space:]]*[A-Za-z]+[[:space:]]*=' /etc/wsl.conf || true)
    # Una orden de shell pegada por error puede contener "=" y parecer una clave.
    pasted=$(grep -nE '^[[:space:]]*(echo|sudo|cat|tee|exit|export)\b' /etc/wsl.conf || true)
    if [[ -n $invalid$pasted ]]; then
        fail "/etc/wsl.conf tiene líneas que no son configuración:"
        printf '%s\n' "$invalid" "$pasted" | sed '/^$/d' | sort -u | sed 's/^/             /'
    else
        ok "/etc/wsl.conf tiene una sintaxis válida"
    fi
    if grep -qE '^[[:space:]]*default[[:space:]]*=' /etc/wsl.conf; then
        ok "Usuario por defecto: $(sed -nE 's/^[[:space:]]*default[[:space:]]*=[[:space:]]*//p' /etc/wsl.conf | head -1)"
    else
        warn "/etc/wsl.conf no define el usuario por defecto"
    fi
else
    warn "No existe /etc/wsl.conf"
fi

if [[ $(cat /proc/1/comm) == systemd ]]; then ok "systemd activo"; else warn "systemd no está activo"; fi

ram_mib=$(awk '/^MemTotal/ {print int($2 / 1024)}' /proc/meminfo)
if ((ram_mib >= MIN_RAM_MIB)); then
    ok "RAM de WSL: $ram_mib MiB"
else
    fail "RAM de WSL: $ram_mib MiB; el presupuesto necesita 16 GB (.wslconfig)"
fi

# En una unidad de Windows (/mnt/x) los permisos los gobierna NTFS, no Linux.
filesystem=$(stat -f -c %T .)
on_windows=0
[[ $filesystem == v9fs || $filesystem == 9p || $filesystem == drvfs ]] && on_windows=1
owner=$(stat -c %U .)
if [[ $owner == "$(id -un)" ]]; then ok "El proyecto pertenece a $owner"; else warn "El proyecto pertenece a $owner y tú eres $(id -un)"; fi

title "Espacio en disco"
# El df de WSL no sirve: su disco virtual anuncia 1 TB aunque el disco real esté lleno.
for mount in /mnt/[a-z]; do
    [[ -d $mount ]] || continue
    free_gib=$(df -BG --output=avail "$mount" 2>/dev/null | tail -1 | tr -dc '0-9')
    [[ -n $free_gib ]] || continue
    if ((free_gib >= MIN_DISK_GIB)); then ok "$mount: $free_gib GiB libres"; else warn "$mount: $free_gib GiB libres. No descargues imágenes si Docker guarda ahí sus datos"; fi
done

title "Docker"
docker_ready=0
if ! command -v docker >/dev/null; then
    fail "docker no está en el PATH. Activa la integración de WSL en Docker Desktop"
elif info=$(docker info --format '{{.ServerVersion}}|{{.MemTotal}}|{{.CgroupVersion}}' 2>&1); then
    docker_ready=1
    IFS='|' read -r version memory cgroup <<<"$info"
    ok "Daemon $version, cgroup v$cgroup"
    memory_mib=$((memory / 1048576))
    if ((memory_mib >= MIN_RAM_MIB)); then ok "Memoria disponible para Docker: $memory_mib MiB"; else fail "Memoria disponible para Docker: $memory_mib MiB"; fi
elif grep -qi 'permission denied' <<<"$info"; then
    fail "$(id -un) no puede usar Docker. Añádelo al grupo docker"
else
    fail "El daemon no responde. Arranca Docker Desktop"
fi
if ((docker_ready)); then
    if compose_version=$(docker compose version --short 2>/dev/null); then ok "Compose $compose_version"; else fail "Falta docker compose"; fi
    helper=$(python3 -c 'import json, os; print(json.load(open(os.path.expanduser("~/.docker/config.json"))).get("credsStore", ""))' 2>/dev/null || true)
    if [[ -n $helper ]] && ! command -v "docker-credential-$helper" >/dev/null; then
        warn "docker-credential-$helper no está en el PATH: las descargas fallarán. Reinicia la terminal y VS Code"
    fi
fi

title "Proyecto"
env_file=.env.example
if [[ -f .env ]]; then
    env_file=.env
    placeholders=$(grep -cE '=.?CHANGE_ME' .env || true)
    if ((placeholders == 0)); then ok ".env sin valores CHANGE_ME"; else fail ".env tiene $placeholders valores CHANGE_ME"; fi
    mode=$(stat -c %a .env)
    if ((on_windows)); then
        ok ".env en una unidad de Windows: sus permisos los gobierna NTFS"
    elif [[ $mode == 600 ]]; then
        ok ".env con permisos 600"
    else
        warn ".env tiene permisos $mode (recomendado: chmod 600 .env)"
    fi
    bind_ip=$(sed -nE "s/^CADDY_BIND_IP=//p" .env | tr -d "\"'")
    if [[ ${bind_ip:-127.0.0.1} == 127.0.0.1 ]]; then ok "Caddy solo escucha en 127.0.0.1"; else warn "Caddy escuchará en $bind_ip: revisa la exposición en el README"; fi
    if grep -qE "^LLM_PROVIDER=['\"]?nim" .env; then warn "LLM_PROVIDER=nim: el esquema y las preguntas salen del host (TD-021)"; fi
else
    warn "No existe .env (cp .env.example .env). Se valida con .env.example"
fi
if command -v docker >/dev/null && docker compose version >/dev/null 2>&1; then
    if config=$(docker compose --env-file "$env_file" --profile "*" config --format json 2>/dev/null); then
        ok "docker-compose.yml válido"
        total=$(python3 -c '
import json, sys
services = json.load(sys.stdin)["services"]
print(sum(int(s["deploy"]["resources"]["limits"]["memory"]) for n, s in services.items() if not n.endswith("-init")) // 2**20)' <<<"$config")
        if ((total <= MAX_BUDGET_MIB)); then ok "Presupuesto: $total MiB (tope $MAX_BUDGET_MIB)"; else fail "Presupuesto superado: $total MiB (tope $MAX_BUDGET_MIB)"; fi
    else
        fail "docker-compose.yml no es válido con $env_file"
    fi
fi

if [[ ${1:-} == --gpu ]]; then
    title "GPU"
    if ((docker_ready)) && gpu=$(docker run --rm --gpus all --network none --entrypoint nvidia-smi "$GPU_IMAGE" --query-gpu=name,memory.total,memory.free --format=csv,noheader 2>&1); then
        ok "En contenedor: $gpu"
    else
        fail "La GPU no está disponible en los contenedores"
    fi
fi

printf '\nResultado: %d fallos, %d avisos\n' "$failures" "$warnings"
((failures == 0))
