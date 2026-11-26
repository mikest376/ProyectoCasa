"""Datos personales con un modelo externo (TD-021) y estado de las dependencias (TD-015)."""

import asyncio

import httpx
import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr

from lakehouse_api.config import Settings
from lakehouse_api.main import create_app
from lakehouse_api.privacy import personal_data
from lakehouse_api.system_health import SystemHealth, check


@pytest.fixture(autouse=True)
def clean_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in ("LLM_PROVIDER", "NIM_API_KEY", "LANGFUSE_PUBLIC_KEY", "LANGFUSE_SECRET_KEY"):
        monkeypatch.delenv(name, raising=False)


# --- TD-021 -------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("question", "label"),
    [
        ("ventas a juan.perez@example.com", "correo electrónico"),
        ("pedidos del cliente que llamó desde el 600 123 456", "teléfono"),
        ("facturas de +34 600123456", "teléfono"),
        ("cliente con DNI 12345678Z", "DNI o NIE"),
        ("cliente con NIE X-1234567-L", "DNI o NIE"),
        ("cobros en ES91 2100 0418 4502 0005 1332", "IBAN"),
        ("pago con la tarjeta 4111 1111 1111 1111", "tarjeta"),
    ],
)
def test_personal_data_is_recognised(question: str, label: str) -> None:
    assert label in personal_data(question)


@pytest.mark.parametrize(
    "question",
    [
        "¿Cuántos pedidos confirmados hay?",
        "Ventas por mes en 2025",
        "Top 5 clientes por importe",
        "Pedidos con importe mayor que 1.000.000",
        "Importe del pedido S00012",
        "Facturas del cliente Cliente_3f9a1c2e",
    ],
)
def test_ordinary_questions_are_not_flagged(question: str) -> None:
    assert personal_data(question) == []


def test_an_external_model_never_receives_personal_data() -> None:
    settings = Settings(llm_provider="nim", nim_api_key=SecretStr("nvapi-clave-de-prueba"))
    with TestClient(create_app(settings)) as client:
        response = client.post("/api/query", json={"question": "ventas a juan.perez@example.com"})
    assert response.status_code == 422
    body = response.json()
    assert body["code"] == "personal_data"
    assert "juan.perez" not in body["message"]


# --- TD-015 -------------------------------------------------------------------------------------


def run(settings: Settings, handler: httpx.MockTransport) -> SystemHealth:
    async def go() -> SystemHealth:
        async with httpx.AsyncClient(transport=handler) as http:
            return await check(http, settings)

    return asyncio.run(go())


def healthy(request: httpx.Request) -> httpx.Response:
    path = request.url.path
    if path == "/v1/info":
        return httpx.Response(200, json={"starting": False})
    if path == "/api/ps":
        model = Settings().ollama_model
        return httpx.Response(200, json={"models": [{"name": model, "size_vram": 2 * 1024**3}]})
    if path.endswith("/catalogs"):
        return httpx.Response(200, json={"catalogs": []})
    return httpx.Response(404)


def test_every_dependency_reports_ok() -> None:
    result = run(Settings(), httpx.MockTransport(healthy))
    assert result.status == "ok"
    details = {item.name: item.detail for item in result.checks}
    assert details == {
        "trino": "en marcha",
        "modelo": "cargado, 2048 MiB en la GPU",
        "unity_catalog": "en marcha",
        "langfuse": "desactivado",
    }


def test_an_unloaded_model_is_not_a_failure() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/ps":
            return httpx.Response(200, json={"models": []})
        if request.url.path == "/api/tags":
            return httpx.Response(200, json={"models": [{"name": Settings().ollama_model}]})
        return healthy(request)

    result = run(Settings(), httpx.MockTransport(handler))
    assert result.status == "ok"
    assert result.checks[1].detail == "disponible; se carga con la primera pregunta"


def test_a_failure_is_reported_without_internal_details() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/v1/info":
            raise httpx.ConnectError("conexión rechazada por trino:8080")
        return healthy(request)

    result = run(Settings(), httpx.MockTransport(handler))
    assert result.status == "fail"
    assert result.checks[0].detail == "sin respuesta"
    assert "trino:8080" not in result.model_dump_json()
    assert "http" not in result.model_dump_json()


def test_tracing_problems_are_only_a_warning() -> None:
    settings = Settings(langfuse_public_key="pk-lf-x", langfuse_secret_key=SecretStr("sk-lf-x"))

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(503) if request.url.path == "/api/public/health" else healthy(request)

    result = run(settings, httpx.MockTransport(handler))
    assert result.status == "warning"
    assert result.checks[3].detail == "no responde"


def test_the_endpoint_answers_503_when_dependencies_are_down() -> None:
    with TestClient(create_app(Settings(trino_host="127.0.0.1", trino_port=9))) as client:
        response = client.get("/api/system/health")
    assert response.status_code == 503
    assert [item["name"] for item in response.json()["checks"]] == [
        "trino",
        "modelo",
        "unity_catalog",
        "langfuse",
    ]
    assert "http://" not in response.text
