#!/usr/bin/env python3
"""Monitor del equipo y del CDC (TD-005, TD-006 y TD-015).

    python3 scripts/health_monitor.py            tabla legible
    python3 scripts/health_monitor.py --json     para otra herramienta
    python3 scripts/health_monitor.py --watch 300  repite cada 300 s; avisa solo si cambia

Comprueba lo que los servicios no ven sin romper la segmentación de redes: el conector y el
retraso del slot de WAL (fallo por encima de 2 GB, TD-006), las tablas publicadas sin
REPLICA IDENTITY (TD-005), el retraso de la ingesta en Kafka, la salud de los contenedores y
su memoria frente al límite, la VRAM y el espacio libre en C: y E:. Incluye el estado de
/api/system/health, que el backend calcula con sus propias dependencias.

Termina con error si algo falla. Solo lee. Biblioteca estándar.
"""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
import time
import urllib.request
from pathlib import Path
from typing import NamedTuple

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
sys.dont_write_bytecode = True

import cdc  # noqa: E402

GIB = 1024**3
SLOT_FAIL, SLOT_WARN = 2 * GIB, 1 * GIB
LAG_WARN, LAG_FAIL = 1_000, 10_000
MEMORY_WARN, MEMORY_FAIL = 0.85, 0.95
VRAM_WARN = 0.90
DISK_FAIL, DISK_WARN = 10 * GIB, 15 * GIB
DISKS = {"C": "/mnt/c", "E": "/mnt/e"}
BACKEND = "http://127.0.0.1:8000/api/system/health"


class Result(NamedTuple):
    name: str
    status: str  # ok, aviso, fallo
    detail: str


def level(value: float, warn: float, fail: float) -> str:
    return "fallo" if value > fail else "aviso" if value > warn else "ok"


def connector() -> list[Result]:
    code, body = cdc.connect_api("GET", f"/connectors/{cdc.connector_name()}/status")
    if code != 200:
        return [Result("conector", "fallo", f"no registrado ({code})")]
    data = json.loads(body)
    states = [data["connector"]["state"], *(task["state"] for task in data["tasks"])]
    good = states == ["RUNNING", "RUNNING"]
    return [Result("conector", "ok" if good else "fallo", ", ".join(states))]


def slot() -> list[Result]:
    rows = cdc.psql(
        "SELECT slot_name, active, wal_status,"
        " coalesce(pg_wal_lsn_diff(pg_current_wal_lsn(), confirmed_flush_lsn), 0)::bigint"
        " FROM pg_replication_slots"
    )
    if not rows:
        return [Result("slot de WAL", "fallo", "no existe")]
    results = []
    for row in rows:
        name, active, wal, lag = row.split("|")
        status = "fallo" if wal == "lost" or active != "t" else level(int(lag), SLOT_WARN, SLOT_FAIL)
        results.append(
            Result(f"slot {name}", status, f"retraso {int(lag) / 1024**2:.1f} MiB, wal={wal}, activo={active}")
        )
    return results


def replica_identity() -> list[Result]:
    tables = cdc.psql(
        """
        SELECT c.relname FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
        JOIN pg_publication_tables p ON p.schemaname = n.nspname AND p.tablename = c.relname
        WHERE c.relkind IN ('r', 'p')
          AND (c.relreplident = 'n' OR (c.relreplident = 'd' AND NOT EXISTS (
               SELECT 1 FROM pg_index i WHERE i.indrelid = c.oid AND i.indisprimary)))
        ORDER BY 1
        """
    )
    detail = "todas las publicadas la tienen" if not tables else f"sin identidad: {', '.join(tables[:5])}"
    return [Result("identidad de réplica", "fallo" if tables else "ok", detail)]


def ingestion_lag() -> list[Result]:
    group = cdc.compose("exec", "-T", "medallion-worker", "printenv", "WORKER_CONSUMER_GROUP").strip()
    output = cdc.compose(
        "exec", "-T", "-e", "KAFKA_HEAP_OPTS=-Xmx128m", "kafka", "/opt/kafka/bin/kafka-consumer-groups.sh",
        "--bootstrap-server", "kafka:19092", "--describe", "--group", group or "medallion-worker",
    )
    lag = 0
    for line in output.splitlines():
        fields = line.split()
        if len(fields) >= 6 and fields[5].isdigit():
            lag += int(fields[5])
    return [Result("retraso de la ingesta", level(lag, LAG_WARN, LAG_FAIL), f"{lag} mensajes")]


def containers() -> list[Result]:
    services = [
        json.loads(line) for line in cdc.compose("--profile", "*", "ps", "--format", "json").splitlines()
    ]
    unhealthy = [s["Service"] for s in services if s.get("State") == "running" and s.get("Health") not in ("healthy", "")]
    results = [
        Result(
            "contenedores",
            "fallo" if unhealthy else "ok",
            f"{len(services)} en marcha" + (f"; sin salud: {', '.join(unhealthy)}" if unhealthy else ""),
        )
    ]
    stats = subprocess.run(
        ["docker", "stats", "--no-stream", "--format", "{{.Name}}|{{.MemPerc}}"],
        capture_output=True, text=True, check=False,
    ).stdout
    worst = [
        (float(percent.rstrip("%")) / 100, name.removeprefix("odoo-lakehouse-").removesuffix("-1"))
        for name, percent in (line.split("|") for line in stats.splitlines() if "|" in line)
        if percent.rstrip("%").replace(".", "", 1).isdigit()
    ]
    if worst:
        share, name = max(worst)
        results.append(
            Result("memoria", level(share, MEMORY_WARN, MEMORY_FAIL), f"máximo {share:.0%} de su límite ({name})")
        )
    return results


def vram() -> list[Result]:
    binary = shutil.which("nvidia-smi") or "/usr/lib/wsl/lib/nvidia-smi"
    try:
        output = subprocess.run(
            [binary, "--query-gpu=memory.used,memory.total", "--format=csv,noheader,nounits"],
            capture_output=True, text=True, check=True, timeout=20,
        ).stdout
    except (OSError, subprocess.SubprocessError):
        return [Result("VRAM", "aviso", "nvidia-smi no responde")]
    used, total = (int(value) for value in output.split(",")[:2])
    return [Result("VRAM", level(used / total, VRAM_WARN, 1.0), f"{used} de {total} MiB")]


def disks() -> list[Result]:
    results = []
    for label, path in DISKS.items():
        try:
            free = shutil.disk_usage(path).free
        except OSError:
            results.append(Result(f"disco {label}", "aviso", "no se puede leer"))
            continue
        status = "fallo" if free < DISK_FAIL else "aviso" if free < DISK_WARN else "ok"
        results.append(Result(f"disco {label}", status, f"{free / GIB:.1f} GB libres"))
    return results


def backend() -> list[Result]:
    try:
        with urllib.request.urlopen(BACKEND, timeout=30) as response:
            data = json.load(response)
    except urllib.error.HTTPError as error:
        data = json.load(error)
    except (OSError, ValueError):
        return [Result("backend", "fallo", "/api/system/health no responde")]
    mapping = {"ok": "ok", "warning": "aviso", "fail": "fallo"}
    detail = "; ".join(f"{item['name']}: {item['detail']}" for item in data.get("checks", []))
    return [Result("backend", mapping.get(data.get("status"), "fallo"), detail)]


CHECKS = (connector, slot, replica_identity, ingestion_lag, containers, vram, disks, backend)


def collect() -> list[Result]:
    results: list[Result] = []
    for probe in CHECKS:
        try:
            results.extend(probe())
        except (cdc.CdcError, OSError, ValueError, KeyError) as error:
            results.append(Result(probe.__name__, "fallo", f"no se pudo comprobar ({type(error).__name__})"))
    return results


def show(results: list[Result], as_json: bool) -> None:
    if as_json:
        print(json.dumps([result._asdict() for result in results], ensure_ascii=False))
        return
    labels = {"ok": "OK   ", "aviso": "AVISO", "fallo": "FALLO"}
    for result in results:
        print(f"  {labels[result.status]}  {result.name}: {result.detail}")
    failures = sum(result.status == "fallo" for result in results)
    warnings = sum(result.status == "aviso" for result in results)
    print(f"\nResultado: {failures} fallos, {warnings} avisos")


def main() -> int:
    parser = argparse.ArgumentParser(description="Monitor del equipo y del CDC")
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--watch", type=int, metavar="SEGUNDOS")
    arguments = parser.parse_args()
    previous: list[tuple[str, str]] | None = None
    while True:
        results = collect()
        states = [(result.name, result.status) for result in results]
        if arguments.watch is None or states != previous:
            if arguments.watch is not None:
                print(time.strftime("%Y-%m-%d %H:%M:%S"))
            show(results, arguments.json)
        previous = states
        if arguments.watch is None:
            return 1 if any(result.status == "fallo" for result in results) else 0
        time.sleep(arguments.watch)


if __name__ == "__main__":
    sys.exit(main())
