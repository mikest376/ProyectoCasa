"""Aplicación FastAPI. Arranque: uvicorn --factory lakehouse_api.main:create_app"""

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Annotated, Any

import httpx
from fastapi import APIRouter, Depends, FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field, field_validator

from lakehouse_api import json_logging
from lakehouse_api.charts import ChartSpec, ColumnInfo
from lakehouse_api.config import Settings
from lakehouse_api.examples import EXAMPLES, ExampleGroup
from lakehouse_api.gold_catalog import CatalogUnavailableError, GoldCatalog
from lakehouse_api.llm import LLMClient, LLMError, build_llm_client
from lakehouse_api.pipeline import BusyError, Pipeline, QuestionFailedError
from lakehouse_api.tracing import build_tracer
from lakehouse_api.trino_client import TrinoClient

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api")

MAX_QUESTION = 300
STATUS_BY_CODE = {
    "sql_rejected": 422,
    "bad_output": 422,
    "query_failed": 422,
    "timeout": 504,
    "trino_unavailable": 503,
}


class Health(BaseModel):
    status: str


class LLMInfo(BaseModel):
    provider: str
    model: str
    external: bool


class Question(BaseModel):
    question: str = Field(min_length=3, max_length=MAX_QUESTION)

    @field_validator("question")
    @classmethod
    def _clean(cls, value: str) -> str:
        text = " ".join("".join(c for c in value if c.isprintable() or c.isspace()).split())
        if len(text) < 3:
            raise ValueError("la pregunta está vacía")
        return text


class Timings(BaseModel):
    llm_ms: int
    query_ms: int
    total_ms: int


class QueryResponse(BaseModel):
    question: str
    title: str
    explanation: str
    sql: str
    tables: list[str]
    chart: ChartSpec
    columns: list[ColumnInfo]
    # Los decimales llegan como texto exacto: la interfaz solo los convierte para dibujar.
    rows: list[list[Any]]
    row_count: int
    truncated: bool
    attempts: int
    provider: str
    model: str
    external: bool
    timings: Timings
    trace_id: str


class Problem(BaseModel):
    code: str
    message: str
    sql: str | None = None


class TableInfo(BaseModel):
    name: str
    domain: str
    description: str
    columns: list[str]


def get_llm(request: Request) -> LLMClient:
    llm: LLMClient = request.app.state.llm
    return llm


def get_pipeline(request: Request) -> Pipeline:
    pipeline: Pipeline = request.app.state.pipeline
    return pipeline


def get_catalog(request: Request) -> GoldCatalog:
    catalog: GoldCatalog = request.app.state.catalog
    return catalog


@router.get("/health")
async def health() -> Health:
    return Health(status="ok")


@router.get("/llm")
async def llm_info(llm: Annotated[LLMClient, Depends(get_llm)]) -> LLMInfo:
    """Qué modelo responde y si el prompt sale del host. La interfaz lo muestra."""
    return LLMInfo(provider=llm.provider, model=llm.model, external=llm.external)


@router.get("/examples")
async def examples() -> list[ExampleGroup]:
    return EXAMPLES


@router.get("/tables")
async def tables(catalog: Annotated[GoldCatalog, Depends(get_catalog)]) -> list[TableInfo]:
    """Qué se puede preguntar: las tablas de Gold y sus columnas, sin datos."""
    return [
        TableInfo(
            name=table.name,
            domain=table.domain,
            description=table.description,
            columns=[column.name for column in table.columns],
        )
        for table in await catalog.tables()
    ]


@router.post(
    "/query",
    responses={code: {"model": Problem} for code in (422, 429, 502, 503, 504)},
)
async def query(
    body: Question,
    pipeline: Annotated[Pipeline, Depends(get_pipeline)],
    llm: Annotated[LLMClient, Depends(get_llm)],
) -> QueryResponse:
    answer = await pipeline.ask(body.question)
    return QueryResponse(
        question=answer.question,
        title=answer.title,
        explanation=answer.explanation,
        sql=answer.sql,
        tables=list(answer.tables),
        chart=answer.chart,
        columns=answer.columns,
        rows=answer.rows,
        row_count=len(answer.rows),
        truncated=answer.truncated,
        attempts=answer.attempts,
        provider=llm.provider,
        model=llm.model,
        external=llm.external,
        timings=Timings(llm_ms=answer.llm_ms, query_ms=answer.query_ms, total_ms=answer.total_ms),
        trace_id=answer.trace_id,
    )


def _problem(status: int, code: str, message: str, sql: str | None = None) -> JSONResponse:
    return JSONResponse(Problem(code=code, message=message, sql=sql).model_dump(), status)


def _register_errors(app: FastAPI) -> None:
    @app.exception_handler(QuestionFailedError)
    async def failed(_request: Request, error: QuestionFailedError) -> JSONResponse:
        return _problem(STATUS_BY_CODE.get(error.code, 500), error.code, str(error), error.sql)

    @app.exception_handler(BusyError)
    async def busy(_request: Request, _error: BusyError) -> JSONResponse:
        return _problem(429, "busy", "Hay otras preguntas en curso. Inténtalo en unos segundos")

    @app.exception_handler(LLMError)
    async def llm_error(_request: Request, error: LLMError) -> JSONResponse:
        return _problem(502, "llm_unavailable", str(error))

    @app.exception_handler(CatalogUnavailableError)
    async def catalog_error(_request: Request, error: CatalogUnavailableError) -> JSONResponse:
        return _problem(503, "catalog_unavailable", str(error))


def create_app(settings: Settings | None = None) -> FastAPI:
    config = settings or Settings()
    json_logging.configure(config.log_level)

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        async with httpx.AsyncClient() as http:
            app.state.llm = build_llm_client(config, http)
            app.state.catalog = GoldCatalog(
                http,
                config.unity_catalog_url,
                config.unity_catalog_name,
                config.trino_schema,
                config.catalog_ttl_seconds,
            )
            trino = TrinoClient(
                http,
                config.trino_url,
                config.trino_user,
                config.trino_catalog,
                config.trino_schema,
                config.sql_max_rows,
                config.sql_timeout_seconds,
            )
            app.state.trino = trino
            app.state.pipeline = Pipeline(
                app.state.llm,
                app.state.catalog,
                trino,
                build_tracer(config, http),
                config.sql_max_rows,
                config.llm_max_attempts,
            )
            logger.info(
                "API iniciada",
                extra={
                    "fields": {
                        "provider": app.state.llm.provider,
                        "model": app.state.llm.model,
                        "external": app.state.llm.external,
                    }
                },
            )
            yield

    # La documentación queda fuera de /api: Caddy no la publica.
    app = FastAPI(title="Odoo Data Lakehouse API", redoc_url=None, lifespan=lifespan)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=config.cors_origins,
        allow_methods=["GET", "POST"],
        allow_headers=["Content-Type"],
    )
    _register_errors(app)
    app.include_router(router)
    return app
