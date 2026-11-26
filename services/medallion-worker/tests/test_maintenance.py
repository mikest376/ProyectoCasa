"""Mantenimiento de Bronze: compactar, limpiar y particionar sin perder ni cambiar filas."""

from datetime import timedelta
from pathlib import Path

import polars as pl
import pytest
from deltalake.exceptions import DeltaError

from medallion_worker import maintenance
from medallion_worker.sinks import PARTITION
from medallion_worker.storage import Lake
from samples import NOW, event, local_lake, payload, write_bronze

TABLE = "sale_order"


def fill(path: Path, commits: int, start: int = 0, partitioned: list[str] | None = None) -> None:
    for number in range(start, start + commits):
        write_bronze(
            path,
            [event(TABLE, number, payload(TABLE, number + 1), lsn=100 + number)],
            now=NOW + timedelta(hours=number),
            partitioned=partitioned or [],
        )


def content(lake: Lake) -> pl.DataFrame:
    frame = lake.read(lake.bronze(TABLE)).sort("_offset")
    return frame.drop(PARTITION, strict=False)


def properties(lake: Lake) -> dict[str, str | None]:
    return dict(lake.open(lake.bronze(TABLE)).metadata().configuration)


@pytest.mark.parametrize("partitioned", [[], [TABLE]])
def test_compaction_reduces_files_and_keeps_every_row(
    tmp_path: Path, partitioned: list[str]
) -> None:
    lake = local_lake(tmp_path)
    fill(tmp_path, 12, partitioned=partitioned)
    before = content(lake)
    result = maintenance.maintain(lake, lake.bronze(TABLE))
    assert result.files_before == 12
    assert result.files_after < 4
    assert content(lake).equals(before)
    assert properties(lake)["delta.appendOnly"] == "true"


def test_few_files_are_left_alone(tmp_path: Path) -> None:
    lake = local_lake(tmp_path)
    fill(tmp_path, 3)
    assert maintenance.maintain(lake, lake.bronze(TABLE)).files_after == 3


def test_vacuum_respects_the_retention(tmp_path: Path) -> None:
    lake = local_lake(tmp_path)
    fill(tmp_path, 10)
    uri = lake.bronze(TABLE)
    assert maintenance.maintain(lake, uri).vacuumed == 0
    # Los ficheros sustituidos siguen en disco: se puede volver a una versión anterior.
    assert len(list(Path(uri).glob("*.parquet"))) == 11
    with pytest.raises(DeltaError):
        lake.open(uri).vacuum(retention_hours=1, dry_run=False)


def test_ingestion_continues_after_compaction(tmp_path: Path) -> None:
    lake = local_lake(tmp_path)
    fill(tmp_path, 10)
    maintenance.maintain(lake, lake.bronze(TABLE))
    fill(tmp_path, 2, start=10)
    assert content(lake)["_offset"].to_list() == list(range(12))


def test_compaction_and_ingestion_at_the_same_time(tmp_path: Path) -> None:
    """Optimize parte de una versión y, antes de que confirme, la ingesta añade un commit."""
    lake = local_lake(tmp_path)
    fill(tmp_path, 10)
    stale = lake.open(lake.bronze(TABLE))
    fill(tmp_path, 1, start=10)
    stale.optimize.compact()
    assert content(lake)["_offset"].to_list() == list(range(11))


def test_partitioning_keeps_rows_types_and_protection(tmp_path: Path) -> None:
    lake = local_lake(tmp_path)
    fill(tmp_path, 30)
    before = content(lake)
    assert maintenance.partition(lake, TABLE) is True
    table = lake.open(lake.bronze(TABLE))
    assert maintenance.is_partitioned(table)
    assert content(lake).equals(before)
    frame = lake.read(lake.bronze(TABLE))
    assert frame[PARTITION].to_list() == frame["_ingested_at"].dt.date().to_list()
    assert properties(lake)["delta.appendOnly"] == "true"
    assert properties(lake)["delta.enableExpiredLogCleanup"] == "false"
    with pytest.raises(DeltaError):
        table.delete("_offset = 0")


def test_partitioning_is_done_once_and_ingestion_follows_it(tmp_path: Path) -> None:
    lake = local_lake(tmp_path)
    fill(tmp_path, 3)
    assert maintenance.partition(lake, TABLE) is True
    assert maintenance.partition(lake, TABLE) is False
    # La tabla manda: aunque la configuración no la incluya, se escribe con su partición.
    fill(tmp_path, 2, start=3)
    assert content(lake)["_offset"].to_list() == list(range(5))
    assert lake.read(lake.bronze(TABLE))[PARTITION].null_count() == 0


def test_an_unpartitioned_table_is_still_written_as_it_is(tmp_path: Path) -> None:
    lake = local_lake(tmp_path)
    fill(tmp_path, 1)
    fill(tmp_path, 1, start=1, partitioned=[TABLE])
    assert PARTITION not in lake.read(lake.bronze(TABLE)).columns


def test_missing_tables_are_skipped(tmp_path: Path) -> None:
    assert maintenance.partition(local_lake(tmp_path), "no_existe") is False


def test_one_failure_does_not_stop_the_rest(tmp_path: Path) -> None:
    lake = local_lake(tmp_path)
    fill(tmp_path, 9)
    assert maintenance.run(lake, [lake.bronze("no_existe"), lake.bronze(TABLE)]) == (1, 1)
