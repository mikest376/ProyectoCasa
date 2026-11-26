import asyncio
import json
from collections.abc import Callable
from typing import Any

import httpx
import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr, ValidationError

from lakehouse_api.config import Settings
from lakehouse_api.llm import ChatMessage, LLMError, build_llm_client
from lakehouse_api.main import create_app

KEY = "nvapi-clave-de-prueba"
MESSAGES = [ChatMessage("system", "Eres un asistente SQL"), ChatMessage("user", "¿Ventas?")]
SCHEMA = {"type": "object", "properties": {"sql": {"type": "string"}}}
Handler = Callable[[httpx.Request], httpx.Response]


@pytest.fixture(autouse=True)
def clean_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in ("LLM_PROVIDER", "NIM_API_KEY", "NIM_MODEL", "OLLAMA_MODEL", "NIM_BASE_URL"):
        monkeypatch.delenv(name, raising=False)


def complete(settings: Settings, handler: Handler) -> str:
    async def go() -> str:
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
            completion = await build_llm_client(settings, http).complete(MESSAGES, SCHEMA)
            return completion.text

    return asyncio.run(go())


def nim_settings(**kwargs: Any) -> Settings:
    return Settings(llm_provider="nim", nim_api_key=SecretStr(KEY), **kwargs)


def test_default_provider_is_local() -> None:
    settings = Settings()
    assert settings.llm_provider == "ollama"
    assert settings.ollama_model == "qwen2.5-coder:3b-instruct-q4_K_M"


@pytest.mark.parametrize("key", [None, "", "CHANGE_ME"])
def test_nim_requires_a_key(key: str | None) -> None:
    secret = None if key is None else SecretStr(key)
    with pytest.raises(ValidationError, match="NIM_API_KEY"):
        Settings(llm_provider="nim", nim_api_key=secret)


def test_nim_rejects_plain_http_and_hides_the_key() -> None:
    with pytest.raises(ValidationError) as info:
        nim_settings(nim_base_url="http://example.org/v1")
    assert KEY not in str(info.value)
    assert KEY not in repr(nim_settings())


def test_ollama_request_and_response() -> None:
    seen: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen.update(url=str(request.url), body=json.loads(request.content), headers=request.headers)
        return httpx.Response(200, json={"message": {"content": " SELECT 1 "}})

    assert complete(Settings(), handler) == "SELECT 1"
    assert seen["url"] == "http://ollama:11434/api/chat"
    assert seen["body"]["stream"] is False
    # Salida restringida al esquema y sin azar: la misma pregunta da el mismo SQL.
    assert seen["body"]["format"] == SCHEMA
    assert seen["body"]["options"] == {
        "temperature": 0,
        "seed": 7,
        "num_ctx": 4096,
        "num_predict": 450,
    }
    assert seen["body"]["messages"][1] == {"role": "user", "content": "¿Ventas?"}
    assert "authorization" not in seen["headers"]


def test_nim_request_and_response_discards_reasoning() -> None:
    seen: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen.update(url=str(request.url), auth=request.headers["authorization"])
        message = {"reasoning_content": "pienso...", "content": "SELECT 2"}
        return httpx.Response(200, json={"choices": [{"message": message}]})

    assert complete(nim_settings(), handler) == "SELECT 2"
    assert seen["url"] == "https://integrate.api.nvidia.com/v1/chat/completions"
    assert seen["auth"] == f"Bearer {KEY}"


@pytest.mark.parametrize(
    "response",
    [
        httpx.Response(401, json={"detail": "x"}),
        httpx.Response(429),
        httpx.Response(200, content=b"no json"),
        httpx.Response(200, json=[]),
        httpx.Response(200, json={"choices": []}),
        httpx.Response(200, json={"choices": [{"message": {"content": None}}]}),
    ],
)
def test_nim_errors_never_leak_the_key(response: httpx.Response) -> None:
    with pytest.raises(LLMError) as info:
        complete(nim_settings(), lambda _request: response)
    assert KEY not in str(info.value)


def test_connection_errors_are_translated() -> None:
    def refuse(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused", request=request)

    def slow(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("slow", request=request)

    with pytest.raises(LLMError, match="conectar"):
        complete(Settings(), refuse)
    with pytest.raises(LLMError, match="tiempo de espera"):
        complete(Settings(), slow)


def test_api_reports_health_and_provider() -> None:
    with TestClient(create_app(Settings())) as client:
        assert client.get("/api/health").json() == {"status": "ok"}
        info = client.get("/api/llm").json()
    assert info["provider"] == "ollama"
    assert info["external"] is False


def test_api_marks_nim_as_external_without_exposing_the_key() -> None:
    with TestClient(create_app(nim_settings())) as client:
        response = client.get("/api/llm")
    assert response.json()["external"] is True
    assert KEY not in response.text


def test_documentation_is_outside_the_public_prefix() -> None:
    with TestClient(create_app(Settings())) as client:
        assert client.get("/api/docs").status_code == 404
        assert client.get("/api/openapi.json").status_code == 404
