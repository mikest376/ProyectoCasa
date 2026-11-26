"""Punto de entrada: python -m medallion_worker [orden].

ingest       consume Kafka y escribe Bronze (por defecto)
transform    Bronze -> Silver -> Gold y registro en los catálogos; --once para una sola vez
maintain     mantenimiento diario de Bronze; --once para una sola vez
maintain-gold  mantenimiento de Silver y Gold, una sola vez
partition    particiona las tablas Bronze de mayor volumen (con la ingesta detenida)
catalogs     compara Trino, Unity Catalog y lo declarado; termina con error si difieren
contracts    muestra los contratos de Silver en JSON

Limpieza única de datos personales (TD-004, excepción aprobada a la regla 4):
redact-bronze     quita de Bronze las columnas excluidas; lee column-exclude.list por la
                  entrada estándar. Con la ingesta y el mantenimiento detenidos
redact-lakehouse  seudónimos en Silver, Gold reconstruido y registro limpio. Con la
                  transformación detenida
scan-personal     busca valores ({"values": [...]} por la entrada estándar) en las tres capas,
                  su registro y sus ficheros sobrantes; termina con error si aparece alguno
"""

import argparse
import json
import logging
import signal
import sys
import threading
from collections.abc import Callable, Iterable
from datetime import UTC, datetime, timedelta
from types import FrameType

import httpx
from confluent_kafka import Consumer

from medallion_worker import catalogs, json_logging, maintenance, redaction, transform
from medallion_worker.config import Settings
from medallion_worker.consumer import run as run_consumer
from medallion_worker.contracts import CONTRACTS, POSTGRES_TYPES
from medallion_worker.events import table_name
from medallion_worker.sinks import REJECTED_TABLE, DeltaSink
from medallion_worker.storage import Lake, storage_options

logger = logging.getLogger(__name__)


def ingest(settings: Settings, _once: bool, stop: threading.Event) -> int:
    sink = DeltaSink(
        settings.bronze_uri,
        storage_options(settings),
        partitioned=settings.partitioned_tables,
    )
    run_consumer(settings, sink, stop)
    return 0


def run_transform(settings: Settings, once: bool, stop: threading.Event) -> int:
    lake = Lake.from_settings(settings)
    if not once:
        transform.run(settings, lake, stop, settings.worker_alive_file.touch)
        return 0
    summary = transform.Transformer(lake, settings.worker_language, settings.pii_salt).run_once()
    with httpx.Client(timeout=catalogs.TIMEOUT) as http:
        changes = transform.sync_catalogs(settings, lake, http)
    logger.info(
        "transformación terminada",
        extra={
            "fields": {
                "applied": summary.applied,
                "models": len(summary.models),
                "broken": summary.broken,
                "catalog_changes": changes,
            }
        },
    )
    return 1 if summary.broken else 0


def bronze_uris(settings: Settings, lake: Lake) -> Iterable[str]:
    """Las tablas Bronze se deducen de los topics: MinIO no las enumera por tabla."""
    consumer = Consumer(
        {
            "bootstrap.servers": settings.kafka_bootstrap_servers,
            "group.id": f"{settings.worker_consumer_group}-maintenance",
        }
    )
    try:
        topics = consumer.list_topics(timeout=30).topics
    finally:
        consumer.close()
    names = sorted(table_name(topic) for topic in topics if ".public." in topic)
    for name in (*names, REJECTED_TABLE):
        uri = lake.bronze(name)
        if lake.exists(uri):
            yield uri


def maintain(settings: Settings, once: bool, stop: threading.Event) -> int:
    lake = Lake.from_settings(settings)
    failed = 0
    while not stop.is_set():
        if not once and not _wait_until(settings, stop):
            break
        _done, failed = maintenance.run(
            lake, bronze_uris(settings, lake), settings.worker_alive_file.touch
        )
        if once:
            break
    return 1 if failed else 0


def _wait_until(settings: Settings, stop: threading.Event) -> bool:
    """Espera a la hora diaria sin dejar de señalar que el proceso sigue vivo."""
    now = datetime.now(tz=UTC)
    target = now.replace(hour=settings.worker_maintenance_hour, minute=0, second=0, microsecond=0)
    if target <= now:
        target += timedelta(days=1)
    while datetime.now(tz=UTC) < target:
        settings.worker_alive_file.touch()
        if stop.wait(20):
            return False
    return True


def maintain_gold(settings: Settings, _once: bool, _stop: threading.Event) -> int:
    lake = Lake.from_settings(settings)
    entries = [entry.location for entry in catalogs.expected(lake) if lake.exists(entry.location)]
    _done, failed = maintenance.run(lake, entries)
    return 1 if failed else 0


def partition(settings: Settings, _once: bool, _stop: threading.Event) -> int:
    lake = Lake.from_settings(settings)
    changed = [
        name for name in sorted(settings.partitioned_tables) if maintenance.partition(lake, name)
    ]
    logger.info("partición terminada", extra={"fields": {"tables": changed}})
    return 0


def compare_catalogs(settings: Settings, _once: bool, _stop: threading.Event) -> int:
    lake = Lake.from_settings(settings)
    with httpx.Client(timeout=catalogs.TIMEOUT) as http:
        problems = catalogs.compare(
            catalogs.TrinoClient(
                http, settings.trino_url, settings.trino_worker_user, settings.trino_catalog
            ),
            catalogs.UnityClient(http, settings.unity_catalog_url, settings.unity_catalog_name),
            catalogs.expected(lake),
        )
    logger.info(
        "catálogos comparados",
        extra={"fields": {"tables": len(catalogs.expected(lake)), "problems": problems}},
    )
    return 1 if problems else 0


def redact_bronze(settings: Settings, _once: bool, _stop: threading.Event) -> int:
    exclusions = redaction.Exclusions(sys.stdin.read().splitlines())
    if not exclusions:
        logger.error("no llegan expresiones de exclusión por la entrada estándar")
        return 1
    lake = Lake.from_settings(settings)
    failed = 0
    for table in redaction.bronze_tables(lake, redaction.tables(lake, ("bronze",))):
        if table == REJECTED_TABLE:
            # Mensajes ilegibles guardados tal cual: no hay columnas que quitar, solo bytes.
            rows = lake.read(lake.bronze(table)).height
            if rows:
                logger.warning("eventos rechazados por revisar", extra={"fields": {"rows": rows}})
            continue
        try:
            result = redaction.redact_bronze(lake, table, exclusions)
        except Exception:
            failed += 1
            logger.exception("limpieza fallida", extra={"fields": {"table": table}})
            continue
        if result.changed:
            redaction.log_result("bronze limpio", result)
    return 1 if failed else 0


def redact_lakehouse(settings: Settings, _once: bool, _stop: threading.Event) -> int:
    lake = Lake.from_settings(settings)
    salt = settings.pii_salt
    for contract in CONTRACTS:
        result = redaction.pseudonymize_silver(lake, contract, salt)
        if result is not None:
            redaction.log_result("silver seudonimizado", result)
    summary = transform.Transformer(lake, settings.worker_language, salt).run_once()
    removed = sum(redaction.purge(lake, uri) for uri in redaction.tables(lake, ("gold",)))
    with httpx.Client(timeout=catalogs.TIMEOUT) as http:
        changes = transform.sync_catalogs(settings, lake, http)
    logger.info(
        "lakehouse limpio",
        extra={
            "fields": {
                "models": len(summary.models),
                "broken": summary.broken,
                "gold_removed_files": removed,
                "catalog_changes": changes,
            }
        },
    )
    return 1 if summary.broken else 0


def scan_personal(settings: Settings, _once: bool, _stop: threading.Event) -> int:
    values = json.loads(sys.stdin.read())["values"]
    lake = Lake.from_settings(settings)
    uris = redaction.tables(lake)
    problems = redaction.scan(lake, uris, values)
    report = {"tables": len(uris), "values": len(values), "problems": problems}
    sys.stdout.write(json.dumps(report, ensure_ascii=False) + "\n")
    return 1 if problems or not uris else 0


def show_contracts() -> int:
    contracts = {
        contract.table: {
            column.name: {"types": POSTGRES_TYPES[column.kind], "required": column.required}
            for column in contract.columns
        }
        for contract in CONTRACTS
    }
    sys.stdout.write(json.dumps(contracts) + "\n")
    return 0


COMMANDS: dict[str, Callable[[Settings, bool, threading.Event], int]] = {
    "ingest": ingest,
    "transform": run_transform,
    "maintain": maintain,
    "maintain-gold": maintain_gold,
    "partition": partition,
    "catalogs": compare_catalogs,
    "redact-bronze": redact_bronze,
    "redact-lakehouse": redact_lakehouse,
    "scan-personal": scan_personal,
}


def main() -> int:
    parser = argparse.ArgumentParser(prog="medallion_worker")
    parser.add_argument("command", nargs="?", default="ingest", choices=[*COMMANDS, "contracts"])
    parser.add_argument("--once", action="store_true")
    arguments = parser.parse_args()
    if arguments.command == "contracts":
        return show_contracts()

    settings = Settings()
    json_logging.configure(settings.log_level)
    stop = threading.Event()

    def request_stop(_signum: int, _frame: FrameType | None) -> None:
        stop.set()

    signal.signal(signal.SIGTERM, request_stop)
    signal.signal(signal.SIGINT, request_stop)
    try:
        return COMMANDS[arguments.command](settings, arguments.once, stop)
    except Exception:
        # Docker reinicia el contenedor y Kafka vuelve a entregar lo no confirmado.
        logger.exception("el worker se detiene por un error")
        return 1


if __name__ == "__main__":
    sys.exit(main())
