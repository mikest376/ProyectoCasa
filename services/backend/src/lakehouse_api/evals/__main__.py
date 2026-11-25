"""Mide Text-to-SQL con el proveedor configurado: python -m lakehouse_api.evals [--split test]

Se ejecuta en el contenedor del backend, con Trino, Unity Catalog y el modelo en marcha. Recorre el
mismo flujo que la API: prompt, modelo, validación, corrección y ejecución.
"""

import argparse
import asyncio
import json
import sys
import time
from dataclasses import asdict, dataclass
from typing import Any

import httpx

from lakehouse_api import json_logging
from lakehouse_api.config import Settings
from lakehouse_api.evals.compare import Case, load_cases, percentile, same_result
from lakehouse_api.gold_catalog import GoldCatalog
from lakehouse_api.llm import build_llm_client
from lakehouse_api.pipeline import Pipeline, QuestionFailedError
from lakehouse_api.sql_guard import SqlGuard
from lakehouse_api.tracing import NullTracer
from lakehouse_api.trino_client import TrinoClient


@dataclass(slots=True)
class Outcome:
    identifier: str
    domain: str
    question: str
    correct: bool
    reason: str
    sql: str | None
    attempts: int
    llm_ms: int
    total_ms: int


async def evaluate(case: Case, pipeline: Pipeline, trino: TrinoClient, guard: SqlGuard) -> Outcome:
    references = [(await trino.query(guard.check(sql).sql)).rows for sql in case.sql]
    started = time.monotonic()
    try:
        answer = await pipeline.ask(case.question)
    except QuestionFailedError as error:
        elapsed = round((time.monotonic() - started) * 1000)
        return Outcome(
            case.identifier, case.domain, case.question, False, error.code, error.sql, 2, 0, elapsed
        )
    correct = any(same_result(rows, answer.rows, case.ordered) for rows in references)
    return Outcome(
        case.identifier,
        case.domain,
        case.question,
        correct,
        "" if correct else "resultado distinto",
        answer.sql,
        answer.attempts,
        answer.llm_ms,
        answer.total_ms,
    )


async def run(split: str) -> dict[str, Any]:
    settings = Settings()
    async with httpx.AsyncClient() as http:
        llm = build_llm_client(settings, http)
        catalog = GoldCatalog(
            http,
            settings.unity_catalog_url,
            settings.unity_catalog_name,
            settings.trino_schema,
            settings.catalog_ttl_seconds,
        )
        trino = TrinoClient(
            http,
            settings.trino_url,
            settings.trino_user,
            settings.trino_catalog,
            settings.trino_schema,
            settings.sql_max_rows,
            settings.sql_timeout_seconds,
        )
        tables = {
            table.name: [column.name for column in table.columns]
            for table in await catalog.tables()
        }
        guard = SqlGuard(tables, settings.sql_max_rows)
        pipeline = Pipeline(
            llm, catalog, trino, NullTracer(), settings.sql_max_rows, settings.llm_max_attempts
        )
        outcomes = [await evaluate(case, pipeline, trino, guard) for case in load_cases(split)]
    return summarize(llm.provider, llm.model, split, outcomes)


def summarize(provider: str, model: str, split: str, outcomes: list[Outcome]) -> dict[str, Any]:
    domains = sorted({outcome.domain for outcome in outcomes})
    correct = sum(outcome.correct for outcome in outcomes)
    return {
        "provider": provider,
        "model": model,
        "split": split,
        "questions": len(outcomes),
        "correct": correct,
        "accuracy": round(100 * correct / max(1, len(outcomes)), 1),
        "by_domain": {
            domain: f"{sum(o.correct for o in outcomes if o.domain == domain)}"
            f"/{sum(o.domain == domain for o in outcomes)}"
            for domain in domains
        },
        "first_attempt": sum(o.attempts == 1 and o.correct for o in outcomes),
        "llm_ms_p50": percentile([o.llm_ms for o in outcomes if o.llm_ms], 0.5),
        "llm_ms_p95": percentile([o.llm_ms for o in outcomes if o.llm_ms], 0.95),
        "total_ms_p50": percentile([o.total_ms for o in outcomes], 0.5),
        "total_ms_p95": percentile([o.total_ms for o in outcomes], 0.95),
        "outcomes": [asdict(outcome) for outcome in outcomes],
    }


def main() -> int:
    parser = argparse.ArgumentParser(prog="lakehouse_api.evals")
    parser.add_argument("--split", choices=["dev", "test", "all"], default="all")
    parser.add_argument("--json", action="store_true", help="resultado completo en JSON")
    parser.add_argument("--minimum", type=float, default=0.0, help="precisión mínima, en %%")
    arguments = parser.parse_args()
    json_logging.configure("ERROR")
    report = asyncio.run(run(arguments.split))
    write = sys.stdout.write
    if arguments.json:
        write(json.dumps(report, ensure_ascii=False, indent=1) + "\n")
    else:
        for outcome in report["outcomes"]:
            if not outcome["correct"]:
                write(
                    f"{outcome['identifier']} {outcome['reason']}: {outcome['question']}\n"
                    f"    {outcome['sql']}\n"
                )
        summary = {key: value for key, value in report.items() if key != "outcomes"}
        write(json.dumps(summary, ensure_ascii=False) + "\n")
    return 0 if report["accuracy"] >= arguments.minimum else 1


if __name__ == "__main__":
    sys.exit(main())
