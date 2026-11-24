"""Aplicación FastAPI. Arranque: uvicorn --factory lakehouse_api.main:create_app"""

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Annotated

import httpx
from fastapi import APIRouter, Depends, FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from lakehouse_api import json_logging
from lakehouse_api.config import Settings
from lakehouse_api.llm import LLMClient, build_llm_client

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api")


class Health(BaseModel):
    status: str


class LLMInfo(BaseModel):
    provider: str
    model: str
    external: bool


def get_llm(request: Request) -> LLMClient:
    llm: LLMClient = request.app.state.llm
    return llm


@router.get("/health")
async def health() -> Health:
    return Health(status="ok")


@router.get("/llm")
async def llm_info(llm: Annotated[LLMClient, Depends(get_llm)]) -> LLMInfo:
    """Qué modelo responde y si el prompt sale del host. La interfaz lo muestra."""
    return LLMInfo(provider=llm.provider, model=llm.model, external=llm.external)


def create_app(settings: Settings | None = None) -> FastAPI:
    config = settings or Settings()
    json_logging.configure(config.log_level)

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        async with httpx.AsyncClient() as http:
            app.state.llm = build_llm_client(config, http)
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
    app.include_router(router)
    return app
