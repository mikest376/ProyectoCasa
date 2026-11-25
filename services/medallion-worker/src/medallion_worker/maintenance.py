"""Mantenimiento diario: compacta, limpia y, una sola vez, particiona Bronze (TD-007).

En Bronze se ejecuta con un usuario de MinIO propio: el consumidor de ingesta no puede borrar. La
compactación reescribe ficheros sin cambiar filas (dataChange=false) y es compatible con la
ingesta simultánea. VACUUM solo borra lo que lleva más de una semana fuera de la tabla.
"""

import logging
from collections.abc import Callable, Iterable
from dataclasses import dataclass

import polars as pl
from deltalake import DeltaTable, WriterProperties, write_deltalake

from medallion_worker.sinks import PARTITION, with_partition
from medallion_worker.storage import Lake

logger = logging.getLogger(__name__)

# Con menos ficheros no compensa reescribir.
MIN_FILES = 8
RETENTION_HOURS = 168
ZSTD = WriterProperties(compression="ZSTD")


class MaintenanceError(RuntimeError):
    """La tabla resultante no coincide con la original."""


@dataclass(frozen=True, slots=True)
class MaintenanceResult:
    uri: str
    files_before: int
    files_after: int
    vacuumed: int
    logs: bool


def maintain(
    lake: Lake, uri: str, *, min_files: int = MIN_FILES, retention_hours: int = RETENTION_HOURS
) -> MaintenanceResult:
    table = lake.open(uri)
    before = len(table.file_uris())
    if before >= min_files:
        table.optimize.compact(writer_properties=ZSTD)
        table = lake.open(uri)
    vacuumed = table.vacuum(retention_hours=retention_hours, dry_run=False)
    # Registro: un checkpoint reciente permite borrar los commits ya caducados.
    table = lake.open(uri)
    table.create_checkpoint()
    table.cleanup_metadata()
    return MaintenanceResult(uri, before, len(table.file_uris()), len(vacuumed), True)


def run(
    lake: Lake, uris: Iterable[str], alive: Callable[[], None] = lambda: None
) -> tuple[int, int]:
    """Devuelve (tablas correctas, tablas con error). Un error no detiene las demás."""
    done = failed = before = after = vacuumed = 0
    for uri in uris:
        alive()
        try:
            result = maintain(lake, uri)
        except Exception:
            failed += 1
            logger.exception("mantenimiento fallido", extra={"fields": {"uri": uri}})
            continue
        done += 1
        before += result.files_before
        after += result.files_after
        vacuumed += result.vacuumed
        if result.files_after != result.files_before or result.vacuumed:
            logger.info(
                "tabla mantenida",
                extra={
                    "fields": {
                        "uri": uri,
                        "files_before": result.files_before,
                        "files_after": result.files_after,
                        "vacuumed": result.vacuumed,
                    }
                },
            )
    logger.info(
        "mantenimiento terminado",
        extra={
            "fields": {
                "tables": done,
                "failed": failed,
                "files_before": before,
                "files_after": after,
                "vacuumed": vacuumed,
            }
        },
    )
    return done, failed


def partition(lake: Lake, table: str) -> bool:
    """Particiona por fecha de ingesta una tabla Bronze que aún no lo está.

    Un único commit atómico con las mismas filas: si algo falla, la tabla sigue como estaba. Hay
    que ejecutarlo con medallion-worker detenido. Devuelve False si no había nada que hacer.
    """
    uri = lake.bronze(table)
    if not lake.exists(uri):
        return False
    original = lake.open(uri)
    if original.metadata().partition_columns == [PARTITION]:
        return False
    before = lake.read(uri)
    # appendOnly también impide reescribir: se levanta solo mientras dura el cambio.
    original.alter.set_table_properties({"delta.appendOnly": "false"})
    try:
        write_deltalake(
            uri,
            with_partition(before),
            mode="overwrite",
            schema_mode="overwrite",
            partition_by=[PARTITION],
            storage_options=lake.options,
            writer_properties=ZSTD,
        )
        after = lake.read(uri)
        keys = ["_topic", "_partition", "_offset", "source_lsn"]
        same = after.height == before.height and after.select(before.columns).sort(keys).equals(
            before.sort(keys)
        )
        if not same:
            lake.open(uri).restore(original.version())
            raise MaintenanceError(
                f"{table}: el contenido no coincide; se restaura la versión previa"
            )
    finally:
        lake.open(uri).alter.set_table_properties({"delta.appendOnly": "true"})
    logger.info(
        "tabla particionada",
        extra={"fields": {"table": table, "rows": after.height, "days": _days(after)}},
    )
    return True


def _days(frame: pl.DataFrame) -> int:
    return frame[PARTITION].n_unique()


def is_partitioned(table: DeltaTable) -> bool:
    return table.metadata().partition_columns == [PARTITION]
