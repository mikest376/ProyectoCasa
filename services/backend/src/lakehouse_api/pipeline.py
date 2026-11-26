"""De la pregunta a los datos: esquema, modelo, validación, ejecución y traza."""

import asyncio
import json
import logging
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from lakehouse_api import charts, prompt
from lakehouse_api.charts import ChartSpec, ColumnInfo
from lakehouse_api.gold_catalog import GoldCatalog
from lakehouse_api.llm import ChatMessage, Completion, LLMClient
from lakehouse_api.sql_guard import SqlGuard, SqlRejectedError
from lakehouse_api.tracing import Generation, Trace, Tracer
from lakehouse_api.trino_client import QueryResult, TrinoClient, TrinoError

logger = logging.getLogger(__name__)


class ModelOutputError(ValueError):
    """La respuesta del modelo no tiene el formato pedido."""


class QuestionFailedError(RuntimeError):
    """No se ha podido responder. Incluye el último SQL propuesto, si lo hubo."""

    def __init__(self, code: str, message: str, sql: str | None = None) -> None:
        super().__init__(message)
        self.code = code
        self.sql = sql


class BusyError(RuntimeError):
    """Ya hay demasiadas preguntas en espera."""


@dataclass(frozen=True, slots=True)
class Proposal:
    sql: str
    chart_type: str | None
    x_axis: str | None
    y_axis: str | None
    title: str
    explanation: str


@dataclass(frozen=True, slots=True)
class Answer:
    question: str
    title: str
    explanation: str
    sql: str
    tables: tuple[str, ...]
    chart: ChartSpec
    columns: list[ColumnInfo]
    rows: list[list[Any]]
    truncated: bool
    attempts: int
    llm_ms: int
    query_ms: int
    total_ms: int
    trace_id: str


def parse_proposal(text: str) -> Proposal:
    """Admite el JSON pedido, también si el modelo lo envuelve en un bloque de código."""
    start, end = text.find("{"), text.rfind("}")
    if start < 0 or end <= start:
        raise ModelOutputError("la respuesta no es JSON")
    try:
        data = json.loads(text[start : end + 1])
    except json.JSONDecodeError as error:
        raise ModelOutputError("la respuesta no es JSON") from error
    if not isinstance(data, dict) or not isinstance(data.get("sql"), str):
        raise ModelOutputError("la respuesta no contiene SQL")

    def text_or_none(name: str) -> str | None:
        value = data.get(name)
        return value.strip() if isinstance(value, str) and value.strip() else None

    return Proposal(
        sql=data["sql"],
        chart_type=text_or_none("chart_type"),
        x_axis=text_or_none("x_axis"),
        y_axis=text_or_none("y_axis"),
        title=(text_or_none("title") or "Resultado")[:120],
        explanation=(text_or_none("explanation") or "")[:400],
    )


class Pipeline:
    def __init__(
        self,
        llm: LLMClient,
        catalog: GoldCatalog,
        trino: TrinoClient,
        tracer: Tracer,
        max_rows: int,
        max_attempts: int = 2,
        max_waiting: int = 4,
    ) -> None:
        self._llm = llm
        self._catalog = catalog
        self._trino = trino
        self._tracer = tracer
        self._max_rows = max_rows
        self._max_attempts = max_attempts
        self._max_waiting = max_waiting
        # La GPU atiende una petición a la vez: las demás esperan, hasta un límite.
        self._turn = asyncio.Semaphore(1)
        self._waiting = 0
        self._background: set[asyncio.Task[None]] = set()

    async def ask(self, question: str) -> Answer:
        if self._waiting >= self._max_waiting:
            raise BusyError
        self._waiting += 1
        try:
            async with self._turn:
                return await self._answer(question)
        finally:
            self._waiting -= 1

    async def _answer(self, question: str) -> Answer:
        started = time.monotonic()
        tables = await self._catalog.tables()
        guard = SqlGuard(
            {table.name: [column.name for column in table.columns] for table in tables},
            self._max_rows,
        )
        messages = prompt.build_messages(tables, question)
        trace = Trace(question, self._llm.provider, self._llm.model)
        llm_ms = 0
        failure: QuestionFailedError | None = None
        try:
            for attempt in range(1, self._max_attempts + 1):
                generation, elapsed = await self._generate(messages, trace)
                llm_ms += elapsed
                try:
                    proposal = parse_proposal(generation.completion)
                    trace.sql = proposal.sql
                    safe = guard.check(proposal.sql)
                    trace.sql = safe.sql
                    result = await self._execute(safe.sql, trace)
                except (ModelOutputError, SqlRejectedError, TrinoError) as error:
                    # Los tres mensajes describen el SQL, sin datos: van al modelo y a la traza.
                    generation.error = str(error)[:300]
                    failure = _failure(error, trace.sql)
                    if isinstance(error, TrinoError) and not error.repairable:
                        raise failure from error
                    messages = prompt.repair_messages(tables, question, trace.sql, str(error)[:300])
                    continue
                columns = charts.describe(result.columns)
                return Answer(
                    question=question,
                    title=proposal.title,
                    explanation=proposal.explanation,
                    sql=safe.sql,
                    tables=safe.tables,
                    chart=charts.choose(
                        proposal.chart_type,
                        proposal.x_axis,
                        proposal.y_axis,
                        columns,
                        len(result.rows),
                    ),
                    columns=columns,
                    rows=result.rows,
                    truncated=len(result.rows) >= self._max_rows,
                    attempts=attempt,
                    llm_ms=llm_ms,
                    query_ms=result.milliseconds,
                    total_ms=round((time.monotonic() - started) * 1000),
                    trace_id=trace.identifier,
                )
            assert failure is not None  # noqa: S101
            raise failure
        except QuestionFailedError as error:
            trace.status = error.code
            raise
        except Exception as error:
            trace.status = type(error).__name__
            raise
        finally:
            trace.metadata |= {
                "provider": self._llm.provider,
                "attempts": len(trace.generations),
                "llm_ms": llm_ms,
                "total_ms": round((time.monotonic() - started) * 1000),
            }
            self._trace(trace)

    async def _generate(self, messages: list[ChatMessage], trace: Trace) -> tuple[Generation, int]:
        started = datetime.now(tz=UTC)
        clock = time.monotonic()
        completion: Completion = await self._llm.complete(messages, prompt.OUTPUT_SCHEMA)
        generation = Generation(
            prompt=[message.as_dict() for message in messages],
            completion=completion.text,
            model=self._llm.model,
            started=started,
            ended=datetime.now(tz=UTC),
            prompt_tokens=completion.prompt_tokens,
            completion_tokens=completion.completion_tokens,
        )
        trace.generations.append(generation)
        return generation, round((time.monotonic() - clock) * 1000)

    async def _execute(self, sql: str, trace: Trace) -> QueryResult:
        trace.query_started = datetime.now(tz=UTC)
        try:
            return await self._trino.query(sql)
        finally:
            trace.query_ended = datetime.now(tz=UTC)

    def _trace(self, trace: Trace) -> None:
        """Se envía aparte: la respuesta no espera a Langfuse."""
        task = asyncio.create_task(self._tracer.send(trace))
        self._background.add(task)
        task.add_done_callback(self._background.discard)
        logger.info(
            "pregunta procesada",
            extra={
                "fields": {
                    "trace": trace.identifier,
                    "status": trace.status,
                    "attempts": len(trace.generations),
                    "llm_ms": trace.metadata["llm_ms"],
                    "total_ms": trace.metadata["total_ms"],
                }
            },
        )


def _failure(error: Exception, sql: str | None) -> QuestionFailedError:
    if isinstance(error, SqlRejectedError):
        return QuestionFailedError("sql_rejected", f"SQL rechazado: {error}", sql)
    if isinstance(error, TrinoError):
        if error.name == "TIMEOUT":
            return QuestionFailedError("timeout", str(error), sql)
        if error.name == "UNAVAILABLE":
            return QuestionFailedError("trino_unavailable", "Trino no responde", sql)
        return QuestionFailedError(
            "query_failed", f"La consulta no se puede ejecutar ({error.name})", sql
        )
    return QuestionFailedError("bad_output", "El modelo no ha devuelto una consulta válida", sql)
