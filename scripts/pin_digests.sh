#!/usr/bin/env bash
# Imágenes fijadas por digest (TD-024).
#
#   bash scripts/pin_digests.sh            comprueba; termina con error si alguna no lo está
#   bash scripts/pin_digests.sh --write    añade el digest a las que falte
#   bash scripts/pin_digests.sh --refresh  vuelve a resolver todas desde su registro (tras subir
#                                          un tag a mano, o para recoger parches de la misma versión)
#
# Revisa docker-compose.yml, los Dockerfile de services/ e infra/ y las imágenes que usan los
# scripts. El tag se conserva para leerlo; el digest manda. El digest sale de la imagen ya
# descargada (lo que está en marcha) y, si no está, del registro, sin descargarla.
# Las imágenes propias (odoo-lakehouse/*) se construyen aquí y no llevan digest.
set -euo pipefail
cd "$(dirname "$0")/.."

mode=${1:---check}
[[ $mode == --check || $mode == --write || $mode == --refresh ]] || { sed -n '3,7p' "$0" >&2; exit 2; }

python3 - "$mode" <<'EOF'
import json
import re
import subprocess
import sys
from pathlib import Path

mode = sys.argv[1]
FILES = [
    Path("docker-compose.yml"),
    *sorted(Path("services").glob("*/Dockerfile")),
    *sorted(Path("infra").glob("*/Dockerfile")),
    *sorted(Path("scripts").glob("*.sh")),
    *sorted(Path("scripts").glob("*.py")),
    *sorted(Path("scripts/tests").glob("*.py")),
]
# Una referencia de imagen: registro/nombre:tag, con o sin @sha256:...
REF = r"(?P<ref>[a-z0-9][a-z0-9._/-]*:[A-Za-z0-9._-]+(?:@sha256:[0-9a-f]{64})?)"
PATTERNS = [
    re.compile(r"^\s*image:\s*" + REF + r"\s*(#.*)?$"),
    re.compile(r"^\s*x-[\w-]+:\s*&[\w-]+\s+" + REF + r"\s*(#.*)?$"),
    re.compile(r"^FROM\s+(--platform=\S+\s+)?" + REF + r"(\s+AS\s+\S+)?\s*$", re.IGNORECASE),
    re.compile(r"^\s*(export\s+)?[A-Z_]*IMAGE=" + REF + r"\s*$"),
    re.compile(r"^\s*[A-Z_]*IMAGE\s*=\s*\"" + REF + r"\"\s*$"),
]


def references(path: Path) -> list[tuple[int, str]]:
    found = []
    for number, line in enumerate(path.read_text(encoding="utf-8").splitlines()):
        for pattern in PATTERNS:
            match = pattern.match(line)
            if match and not match["ref"].startswith("odoo-lakehouse/"):
                found.append((number, match["ref"]))
                break
    return found


def local_digest(ref: str) -> str | None:
    result = subprocess.run(
        ["docker", "image", "inspect", "--format", "{{json .RepoDigests}}", ref],
        capture_output=True, text=True, check=False,
    )
    if result.returncode != 0:
        return None
    name = ref.rsplit(":", 1)[0]
    candidates = {name, name.removeprefix("docker.io/"), name.removeprefix("library/")}
    for item in json.loads(result.stdout) or []:
        repository, _, digest = item.partition("@")
        if repository in candidates or repository.removeprefix("docker.io/library/") in candidates:
            return digest
    return None


def registry_digest(ref: str) -> str | None:
    result = subprocess.run(
        ["docker", "buildx", "imagetools", "inspect", ref, "--format", "{{json .Manifest}}"],
        capture_output=True, text=True, check=False,
    )
    if result.returncode != 0:
        return None
    return json.loads(result.stdout).get("digest")


cache: dict[str, str | None] = {}
problems = changes = total = 0
for path in FILES:
    lines = path.read_text(encoding="utf-8").splitlines(keepends=True)
    edited = False
    for number, ref in references(path):
        total += 1
        tagged, _, pinned = ref.partition("@")
        if pinned and mode != "--refresh":
            continue
        if mode == "--check":
            print(f"  sin digest: {path}:{number + 1}  {ref}")
            problems += 1
            continue
        if tagged not in cache:
            cache[tagged] = (None if mode == "--refresh" else local_digest(tagged)) or registry_digest(tagged)
        digest = cache[tagged]
        if digest is None:
            print(f"  no se puede resolver: {path}:{number + 1}  {tagged}")
            problems += 1
            continue
        if digest != pinned:
            lines[number] = lines[number].replace(ref, f"{tagged}@{digest}", 1)
            edited = True
            changes += 1
            print(f"  fijada: {path}:{number + 1}  {tagged}@{digest[:19]}…")
    if edited:
        path.write_text("".join(lines), encoding="utf-8")

print(f"{total} referencias; {changes} cambiadas; {problems} problemas")
sys.exit(1 if problems else 0)
EOF
