"""Limpieza única de los datos personales ya guardados, y su verificación (TD-004).

Excepción a la regla 4 de CLAUDE.md, aprobada por el usuario el 2026-11-26. Debezium ha dejado
de capturar los datos personales; lo que ya estaba en el lakehouse se limpia una vez:

- Bronze: se quitan del evento las columnas que ahora se excluyen. No cambia ninguna otra fila
  ni ninguna coordenada (topic, partición, offset, LSN). Con la ingesta detenida.
- Silver: las columnas personales reciben su seudónimo, sin tocar la marca de agua. Con la
  transformación detenida; Gold se reconstruye después.
- Después se borran los ficheros sustituidos y el registro anterior (_delta_log): las
  estadísticas de Delta pueden llevar el principio de esos valores.

`scan` lo comprueba sin mostrar los valores que busca.
"""

import json
import logging
import re
from collections.abc import Iterable, Sequence
from dataclasses import dataclass

import polars as pl
from deltalake import write_deltalake
from deltalake.exceptions import DeltaError
from polars.exceptions import PolarsError

from medallion_worker.contracts import Contract
from medallion_worker.events import as_text
from medallion_worker.maintenance import ZSTD
from medallion_worker.pseudonyms import personal_values
from medallion_worker.silver import WATERMARK
from medallion_worker.storage import Lake

logger = logging.getLogger(__name__)

LOG_RETENTION = "delta.logRetentionDuration"
DEFAULT_LOG_RETENTION = "interval 30 days"
COORDINATES = ["_topic", "_partition", "_offset", "source_lsn"]


class RedactionError(RuntimeError):
    """La tabla resultante no coincide con la original: se ha restaurado."""


class Exclusions:
    """Las expresiones de infra/debezium/column-exclude.list, contra esquema.tabla.columna."""

    def __init__(self, lines: Iterable[str]) -> None:
        self._patterns = [
            re.compile(line.strip())
            for line in lines
            if line.strip() and not line.lstrip().startswith("#")
        ]
        self._cache: dict[tuple[str, str], bool] = {}

    def __bool__(self) -> bool:
        return bool(self._patterns)

    def excluded(self, table: str, column: str) -> bool:
        key = (table, column)
        if key not in self._cache:
            name = f"public.{table}.{column}"
            self._cache[key] = any(pattern.fullmatch(name) for pattern in self._patterns)
        return self._cache[key]


def redact_text(text: str | None, table: str, exclusions: Exclusions) -> str | None:
    """El mismo evento sin las columnas excluidas. Si no hay nada que quitar, el texto original."""
    if text is None:
        return None
    value = json.loads(text)
    if not isinstance(value, dict):
        return text
    kept = {key: item for key, item in value.items() if not exclusions.excluded(table, key)}
    return text if len(kept) == len(value) else as_text(kept)


@dataclass(frozen=True, slots=True)
class RedactionResult:
    table: str
    rows: int
    changed: int
    removed_files: int


def redact_bronze(lake: Lake, table: str, exclusions: Exclusions) -> RedactionResult:
    """Reescribe una tabla Bronze sin las columnas excluidas, en un único commit atómico."""
    uri = lake.bronze(table)
    original = lake.open(uri)
    before = lake.read(uri)
    after = before.with_columns(
        pl.Series(
            column,
            [redact_text(text, table, exclusions) for text in before[column].to_list()],
            dtype=pl.String,
        )
        for column in ("before", "after")
    )
    changed = int(
        (
            before["before"].ne_missing(after["before"])
            | before["after"].ne_missing(after["after"])
        ).sum()
    )
    if not changed:
        return RedactionResult(table, before.height, 0, 0)

    partitions = original.metadata().partition_columns or None
    # appendOnly también impide reescribir: se levanta solo mientras dura el cambio.
    original.alter.set_table_properties({"delta.appendOnly": "false"})
    try:
        write_deltalake(
            uri,
            after,
            mode="overwrite",
            partition_by=partitions,
            storage_options=lake.options,
            writer_properties=ZSTD,
        )
        written = lake.read(uri).select(after.columns).sort(COORDINATES)
        others = [name for name in after.columns if name not in ("before", "after")]
        same_rows = written.height == before.height and written.select(others).equals(
            before.select(others).sort(COORDINATES)
        )
        clean = all(
            redact_text(text, table, exclusions) == text
            for column in ("before", "after")
            for text in written[column].to_list()
        )
        if not (same_rows and clean):
            lake.open(uri).restore(original.version())
            raise RedactionError(
                f"{table}: el resultado no coincide; se restaura la versión previa"
            )
    finally:
        lake.open(uri).alter.set_table_properties({"delta.appendOnly": "true"})
    return RedactionResult(table, before.height, changed, purge(lake, uri))


def pseudonymize_silver(lake: Lake, contract: Contract, salt: bytes) -> RedactionResult | None:
    """Pone en su sitio los seudónimos de una tabla Silver. La marca de agua no cambia."""
    personal = [column for column in contract.columns if column.pseudonym is not None]
    uri = lake.silver(contract.domain, contract.table)
    if not personal or not lake.exists(uri):
        return None
    mark = lake.open(uri).transaction_version(WATERMARK)
    frame = lake.read(uri)
    values = [personal_values(contract, key, salt) for key in frame["id"].to_list()]
    updated = frame.with_columns(
        pl.Series(column.name, [row[column.name] for row in values], dtype=pl.String)
        for column in personal
    )
    write_deltalake(uri, updated, mode="overwrite", storage_options=lake.options)
    if lake.open(uri).transaction_version(WATERMARK) != mark:
        raise RedactionError(f"{contract.table}: la marca de agua ha cambiado")
    return RedactionResult(contract.table, frame.height, frame.height, purge(lake, uri))


def purge(lake: Lake, uri: str) -> int:
    """Borra los ficheros sustituidos y el registro anterior al último checkpoint.

    Sin vuelta atrás: tras esto, la tabla no se puede llevar a una versión anterior.
    """
    table = lake.open(uri)
    # full: también los ficheros sin referencia en el registro, no solo los marcados como borrados.
    removed = table.vacuum(
        retention_hours=0, enforce_retention_duration=False, dry_run=False, full=True
    )
    previous = table.metadata().configuration.get(LOG_RETENTION)
    table.alter.set_table_properties({LOG_RETENTION: "interval 0 seconds"})
    table = lake.open(uri)
    table.create_checkpoint()
    table.cleanup_metadata()
    lake.open(uri).alter.set_table_properties({LOG_RETENTION: previous or DEFAULT_LOG_RETENTION})
    return len(removed)


# --- Verificación -------------------------------------------------------------------------------


def _lines(pattern: str, lake: Lake) -> pl.DataFrame:
    """Cada línea de los ficheros que coinciden, como texto, con su ruta. Vacío si no hay."""
    try:
        return pl.scan_csv(
            pattern,
            has_header=False,
            separator="\x1f",
            quote_char=None,
            new_columns=["line"],
            infer_schema=False,
            truncate_ragged_lines=True,
            include_file_paths="path",
            storage_options=lake.options,
        ).collect()
    except (OSError, PolarsError):
        return pl.DataFrame(schema={"line": pl.String, "path": pl.String})


def tables(lake: Lake, layers: Iterable[str] = ("bronze", "silver", "gold")) -> list[str]:
    """Tablas Delta de las capas pedidas, deducidas de sus registros."""
    roots = {
        "bronze": lake.bronze("*"),
        "silver": lake.silver("*", "*"),
        "gold": lake.gold("*", "*"),
    }
    found: set[str] = set()
    for layer in layers:
        paths = _lines(f"{roots[layer]}/_delta_log/*.json", lake)["path"].unique().to_list()
        found.update(path.rsplit("/_delta_log/", 1)[0] for path in paths)
    return sorted(found)


def scan(lake: Lake, uris: Iterable[str], values: Sequence[str]) -> list[str]:
    """Dónde aparece alguno de los valores: datos, registro, checkpoints o ficheros sobrantes.

    Los problemas nombran la tabla y el lugar, nunca el valor.
    """
    needles = sorted({value for value in values if value})
    problems: list[str] = []
    for uri in uris:
        try:
            frame = lake.read(uri)
        except (OSError, DeltaError, PolarsError) as error:
            problems.append(f"{uri}: no se puede leer ({type(error).__name__})")
            continue
        for name, dtype in frame.schema.items():
            if dtype == pl.String and needles:
                hits = int(frame[name].str.contains_any(needles).sum())
                if hits:
                    problems.append(f"{uri}: {hits} filas con datos personales en {name}")
        log = _lines(f"{uri}/_delta_log/*.json", lake)
        if needles and log.height:
            hits = int(log["line"].str.contains_any(needles).sum())
            if hits:
                problems.append(f"{uri}: {hits} líneas del registro con datos personales")
        try:
            checkpoints = pl.read_parquet(
                f"{uri}/_delta_log/*.checkpoint*.parquet", storage_options=lake.options
            )
        except FileNotFoundError:
            checkpoints = pl.DataFrame()
        except (OSError, PolarsError) as error:
            # Una tabla sin checkpoint todavía: en S3, el patrón no encuentra nada.
            if "expanded paths were empty" not in str(error):
                problems.append(f"{uri}: checkpoint ilegible ({type(error).__name__})")
            checkpoints = pl.DataFrame()
        if needles and checkpoints.height:
            text = checkpoints.write_ndjson()
            if any(needle in text for needle in needles):
                problems.append(f"{uri}: el checkpoint contiene datos personales")
        problems.extend(_replaced_files(lake, uri, needles))
    return problems


def _replaced_files(lake: Lake, uri: str, needles: list[str]) -> list[str]:
    """Ficheros que ya no forman parte de la tabla pero siguen guardados, con alguno de los valores.

    VACUUM los enumera; el checkpoint conserva la lápida de los ya borrados, así que solo cuenta
    lo que aún se puede abrir.
    """
    candidates = lake.open(uri).vacuum(
        retention_hours=0, enforce_retention_duration=False, dry_run=True, full=True
    )
    problems: list[str] = []
    for candidate in candidates:
        path = (
            candidate
            if candidate.startswith((uri, "/")) or "://" in candidate
            else f"{uri}/{candidate}"
        )
        try:
            old = pl.read_parquet(path, storage_options=lake.options)
        except FileNotFoundError:
            continue
        except (OSError, PolarsError) as error:
            problems.append(f"{uri}: fichero sustituido ilegible ({type(error).__name__})")
            continue
        if any(
            old[name].str.contains_any(needles).any()
            for name, dtype in old.schema.items()
            if dtype == pl.String and needles
        ):
            problems.append(f"{uri}: un fichero sustituido aún contiene datos personales")
    return problems


def bronze_tables(lake: Lake, uris: Iterable[str]) -> list[str]:
    """Nombre de tabla de las rutas de Bronze."""
    prefix = lake.bronze("")
    return [uri.removeprefix(prefix) for uri in uris if uri.startswith(prefix)]


def log_result(message: str, result: RedactionResult) -> None:
    logger.info(
        message,
        extra={
            "fields": {
                "table": result.table,
                "rows": result.rows,
                "changed": result.changed,
                "removed_files": result.removed_files,
            }
        },
    )
