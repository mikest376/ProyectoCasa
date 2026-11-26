"""El recorrido completo, con Unity Catalog, Trino, Ollama y Langfuse simulados."""

import asyncio
import json
from collections.abc import Awaitable, Callable, Iterator
from decimal import Decimal
from typing import Any

import httpx
import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr

from lakehouse_api import charts
from lakehouse_api.charts import ColumnInfo
from lakehouse_api.config import Settings
from lakehouse_api.gold_catalog import CatalogUnavailableError, GoldCatalog
from lakehouse_api.llm import ChatMessage, Completion
from lakehouse_api.main import create_app
from lakehouse_api.pipeline import (
    BusyError,
    ModelOutputError,
    Pipeline,
    QuestionFailedError,
    parse_proposal,
)
from lakehouse_api.prompt import OUTPUT_SCHEMA, build_messages, missing_column
from lakehouse_api.tracing import LangfuseTracer, Trace, events
from lakehouse_api.trino_client import TrinoClient, TrinoError, TrinoTimeoutError

SECRET_ROW = "Cliente Confidencial"
GOOD_SQL = "SELECT customer_name, sum(amount_total) AS total FROM gold.fact_sales_orders GROUP BY 1"


def proposal(sql: str = GOOD_SQL, **values: Any) -> str:
    data = {
        "explanation": "Suma por cliente.",
        "sql": sql,
        "chart_type": "bar",
        "x_axis": "customer_name",
        "y_axis": "total",
        "title": "Ventas por cliente",
    }
    return json.dumps(data | values, ensure_ascii=False)


def unity_table(name: str, columns: list[tuple[str, str, str]]) -> dict[str, Any]:
    return {
        "name": name,
        "comment": f"Descripción de {name}",
        "storage_location": f"s3://gold/ventas/{name}",
        "columns": [
            {"name": n, "type_text": t, "comment": c, "position": p}
            for p, (n, t, c) in reversed(list(enumerate(columns)))
        ],
    }


class World:
    """Un solo transporte HTTP para todos los servicios."""

    def __init__(self) -> None:
        self.completions: list[str] = [proposal()]
        self.prompts: list[dict[str, Any]] = []
        self.sql: list[str] = []
        self.trino_headers: list[httpx.Headers] = []
        self.trino_error: dict[str, Any] | None = None
        self.trino_hangs = False
        self.cancelled: list[str] = []
        self.traces: list[dict[str, Any]] = []
        self.unity_down = False
        self.rows: list[list[Any]] = [[SECRET_ROW, "1250.10"], ["Otro", "3.00"]]

    async def __call__(self, request: httpx.Request) -> httpx.Response:
        handler: Callable[[httpx.Request], Awaitable[httpx.Response]] = getattr(
            self, request.url.host.replace("-", "_")
        )
        return await handler(request)

    async def unity_catalog(self, request: httpx.Request) -> httpx.Response:
        if self.unity_down:
            return httpx.Response(503)
        assert request.url.params["schema_name"] == "gold"
        if "page_token" not in request.url.params:
            first = unity_table("dim_customer", [("customer_name", "STRING", "Nombre")])
            return httpx.Response(200, json={"tables": [first], "next_page_token": "2"})
        second = unity_table(
            "fact_sales_orders",
            [
                ("customer_name", "STRING", "Nombre del cliente"),
                ("order_date", "date", "Fecha"),
                ("amount_total", "decimal(18,2)", "Importe total"),
            ],
        )
        return httpx.Response(200, json={"tables": [second], "next_page_token": None})

    async def ollama(self, request: httpx.Request) -> httpx.Response:
        self.prompts.append(json.loads(request.content))
        text = self.completions.pop(0) if len(self.completions) > 1 else self.completions[0]
        return httpx.Response(
            200, json={"message": {"content": text}, "prompt_eval_count": 900, "eval_count": 80}
        )

    async def trino(self, request: httpx.Request) -> httpx.Response:
        if request.method == "DELETE":
            self.cancelled.append(str(request.url))
            return httpx.Response(204)
        if request.method == "POST":
            self.sql.append(request.content.decode())
            self.trino_headers.append(request.headers)
            return httpx.Response(200, json={"nextUri": "http://trino:8080/v1/statement/1"})
        if self.trino_hangs:
            await asyncio.sleep(5)
        if self.trino_error:
            return httpx.Response(200, json={"error": self.trino_error})
        columns = [
            {"name": "customer_name", "type": "varchar"},
            {"name": "total", "type": "decimal(38,2)"},
        ]
        return httpx.Response(200, json={"columns": columns, "data": self.rows})

    async def langfuse(self, request: httpx.Request) -> httpx.Response:
        assert request.headers["authorization"].startswith("Basic ")
        self.traces.append(json.loads(request.content))
        return httpx.Response(207, json={"successes": [], "errors": []})


@pytest.fixture
def world() -> World:
    return World()


@pytest.fixture
def client(world: World, monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    original = httpx.AsyncClient

    def build(**kwargs: Any) -> httpx.AsyncClient:
        return original(transport=httpx.MockTransport(world), **kwargs)

    monkeypatch.setattr("lakehouse_api.main.httpx.AsyncClient", build)
    settings = Settings(
        sql_max_rows=50,
        sql_timeout_seconds=1,
        langfuse_public_key="pk-lf-x",
        langfuse_secret_key=SecretStr("sk-lf-x"),
    )
    with TestClient(create_app(settings)) as result:
        yield result


def ask(client: TestClient, question: str = "¿Ventas por cliente?") -> Any:
    return client.post("/api/query", json={"question": question})


# --- Respuesta ----------------------------------------------------------------------------------


def test_a_question_is_answered_with_data_sql_and_provider(
    client: TestClient, world: World
) -> None:
    response = ask(client)
    assert response.status_code == 200, response.text
    data = response.json()
    assert data["sql"].startswith("SELECT customer_name, SUM(amount_total) AS total")
    assert data["sql"].endswith("LIMIT 50")
    assert "lakehouse.gold.fact_sales_orders" in data["sql"]
    assert data["tables"] == ["fact_sales_orders"]
    assert data["chart"] == {"type": "bar", "x_axis": "customer_name", "y_axis": "total"}
    assert data["columns"] == [
        {"name": "customer_name", "kind": "text"},
        {"name": "total", "kind": "number"},
    ]
    # Decimal exacto, como texto.
    assert Decimal(data["rows"][0][1]) == Decimal("1250.10")
    assert (data["provider"], data["external"], data["attempts"]) == ("ollama", False, 1)
    assert set(data["timings"]) == {"llm_ms", "query_ms", "total_ms"}
    assert world.sql == [data["sql"]]
    assert world.trino_headers[0]["x-trino-user"] == "text2sql"


def test_the_prompt_only_contains_gold_metadata_and_the_question(
    client: TestClient, world: World
) -> None:
    ask(client, "¿Cuánto vendemos?")
    body = world.prompts[0]
    assert body["format"] == OUTPUT_SCHEMA
    assert body["options"]["num_ctx"] == 4096
    assert body["options"]["temperature"] == 0
    system = body["messages"][0]["content"]
    assert "gold.fact_sales_orders: Descripción de fact_sales_orders" in system
    assert "  amount_total decimal: Importe total" in system
    assert "silver" not in system
    last = body["messages"][-1]
    assert last["role"] == "user"
    assert last["content"].startswith("¿Cuánto vendemos?")


def test_rows_never_reach_the_model_or_the_traces(client: TestClient, world: World) -> None:
    world.completions = [proposal("SELECT nada FROM gold.no_existe"), proposal()]
    assert ask(client).status_code == 200
    assert SECRET_ROW not in json.dumps(world.prompts, ensure_ascii=False)
    assert world.traces
    assert SECRET_ROW not in json.dumps(world.traces, ensure_ascii=False)
    assert "1250.10" not in json.dumps(world.traces)


# --- Corrección y rechazo -----------------------------------------------------------------------


def test_a_rejected_query_is_corrected_once(client: TestClient, world: World) -> None:
    world.completions = [proposal("SELECT * FROM silver.res_users"), proposal()]
    data = ask(client).json()
    assert data["attempts"] == 2
    assert len(world.sql) == 1
    last = world.prompts[1]["messages"][-1]["content"]
    assert "solo se puede consultar el esquema gold" in last


@pytest.mark.parametrize(
    "sql",
    [
        "DROP TABLE gold.dim_customer",
        "SELECT * FROM system.runtime.queries",
        "SELECT * FROM silver.res_users",
        "SELECT 1; DELETE FROM gold.dim_customer",
    ],
)
def test_dangerous_sql_is_never_executed(client: TestClient, world: World, sql: str) -> None:
    world.completions = [proposal(sql)]
    response = ask(client, "Borra la tabla de clientes")
    assert response.status_code == 422
    assert response.json()["code"] == "sql_rejected"
    assert response.json()["sql"] == sql
    assert world.sql == []


def test_analysis_errors_are_sent_back_to_the_model(client: TestClient, world: World) -> None:
    world.trino_error = {
        "errorName": "COLUMN_NOT_FOUND",
        "errorLocation": {"lineNumber": 1, "columnNumber": 8},
        "message": "Column 'x' cannot be resolved",
    }
    response = ask(client)
    assert response.status_code == 422
    assert response.json()["code"] == "query_failed"
    assert len(world.prompts) == 2
    correction = world.prompts[1]["messages"][-1]["content"]
    assert "Column 'x' cannot be resolved" in correction


def test_a_missing_column_points_to_its_table(client: TestClient, world: World) -> None:
    world.trino_error = {
        "errorName": "COLUMN_NOT_FOUND",
        "errorLocation": {"lineNumber": 1, "columnNumber": 8},
        "message": "line 1:8: Column 'order_date' cannot be resolved",
    }
    ask(client)
    correction = world.prompts[1]["messages"][-1]["content"]
    assert "La columna order_date está en otra tabla" in correction
    assert "gold.fact_sales_orders(customer_name, order_date, amount_total)" in correction
    assert missing_column("otra cosa") is None


@pytest.mark.parametrize(
    ("name", "message", "attempts"),
    [
        # Mensajes reales de Trino 484.
        ("INVALID_FUNCTION_ARGUMENT", f'Invalid format: "{SECRET_ROW}"', 2),
        ("INVALID_FUNCTION_ARGUMENT", f"Cannot convert value to JSON: '{SECRET_ROW}'", 2),
        ("INVALID_CAST_ARGUMENT", f"Cannot cast '{SECRET_ROW}' to INT", 2),
        ("GENERIC_INTERNAL_ERROR", f"fallo al leer {SECRET_ROW}", 1),
        # Un nombre de análisis sin posición en la consulta no da el mensaje por bueno.
        ("NOT_SUPPORTED", f"valor no admitido: {SECRET_ROW}", 2),
    ],
)
def test_execution_errors_never_carry_data(
    client: TestClient, world: World, name: str, message: str, attempts: int
) -> None:
    """Trino cita valores de filas al fallar: no llegan al modelo, a la traza ni a la respuesta."""
    world.trino_error = {"errorName": name, "message": message}
    response = ask(client)
    assert (response.status_code, response.json()["code"]) == (422, "query_failed")
    assert len(world.prompts) == attempts
    assert world.traces
    for place in (response.json(), world.prompts, world.traces):
        assert SECRET_ROW not in json.dumps(place, ensure_ascii=False)


def test_execution_errors_are_corrected_with_a_fixed_hint(client: TestClient, world: World) -> None:
    world.trino_error = {
        "errorName": "INVALID_FUNCTION_ARGUMENT",
        "message": "'mes' is not a valid DATE field",
    }
    ask(client)
    correction = world.prompts[1]["messages"][-1]["content"]
    assert "las unidades de fecha son 'day', 'week', 'month'" in correction
    assert "valid DATE field" not in correction


def test_output_that_is_not_json_is_corrected(client: TestClient, world: World) -> None:
    world.completions = ["lo siento, no puedo", proposal()]
    assert ask(client).json()["attempts"] == 2


def test_slow_queries_are_cancelled(client: TestClient, world: World) -> None:
    world.trino_hangs = True
    response = ask(client)
    assert response.status_code == 504
    assert response.json()["code"] == "timeout"
    assert world.cancelled == ["http://trino:8080/v1/statement/1"]


def test_missing_catalog_is_reported(client: TestClient, world: World) -> None:
    world.unity_down = True
    response = ask(client)
    assert (response.status_code, response.json()["code"]) == (503, "catalog_unavailable")


@pytest.mark.parametrize("question", ["", "  ", "ab", "x" * 301, "\x00\x01\x02"])
def test_questions_are_validated(client: TestClient, world: World, question: str) -> None:
    assert ask(client, question).status_code == 422
    assert world.prompts == []


def test_only_json_is_accepted(client: TestClient) -> None:
    """Un formulario de otro sitio no puede lanzar preguntas con la sesión del navegador."""
    response = client.post(
        "/api/query",
        content='{"question": "ventas"}',
        headers={"Content-Type": "text/plain"},
    )
    assert response.status_code == 422


def test_examples_and_tables_have_no_data(client: TestClient) -> None:
    domains = {group["domain"] for group in client.get("/api/examples").json()}
    assert domains == {"ventas", "facturacion", "inventario", "crm", "maestros"}
    tables = client.get("/api/tables").json()
    assert [table["name"] for table in tables] == ["dim_customer", "fact_sales_orders"]
    assert tables[1]["domain"] == "ventas"
    assert tables[1]["columns"] == ["customer_name", "order_date", "amount_total"]


# --- Piezas -------------------------------------------------------------------------------------


def test_traces_describe_the_whole_attempt(client: TestClient, world: World) -> None:
    world.completions = [proposal("SELECT * FROM silver.res_users"), proposal()]
    trace_id = ask(client).json()["trace_id"]
    [payload] = world.traces
    kinds = [event["type"] for event in payload["batch"]]
    assert kinds == ["trace-create", "generation-create", "generation-create", "span-create"]
    trace, first, second, _ = (event["body"] for event in payload["batch"])
    assert trace["id"] == first["traceId"] == trace_id
    assert trace["metadata"]["attempts"] == 2
    assert first["level"] == "ERROR"
    assert second["usage"] == {"input": 900, "output": 80, "unit": "TOKENS"}


def test_a_broken_langfuse_does_not_matter() -> None:
    async def go() -> None:
        transport = httpx.MockTransport(lambda _request: httpx.Response(500))
        async with httpx.AsyncClient(transport=transport) as http:
            await LangfuseTracer(http, "http://langfuse:3000", "a", "b").send(
                Trace("¿?", "ollama", "modelo")
            )

    asyncio.run(go())
    assert events(Trace("¿?", "ollama", "modelo"))[0]["type"] == "trace-create"


@pytest.mark.parametrize(
    "text", ["", "no hay json", "{roto", '{"sql": 5}', "[]", '{"title": "sin sql"}']
)
def test_bad_model_output(text: str) -> None:
    with pytest.raises(ModelOutputError):
        parse_proposal(text)


def test_json_inside_a_code_block_is_understood() -> None:
    parsed = parse_proposal(f"```json\n{proposal()}\n```")
    assert parsed.sql == GOOD_SQL
    assert parse_proposal('{"sql": "SELECT 1", "x_axis": ""}').x_axis is None


def column(name: str, kind: str) -> ColumnInfo:
    return ColumnInfo.model_validate({"name": name, "kind": kind})


NAME, TOTAL = column("customer_name", "text"), column("total", "number")
MONTH, KEY = column("month", "date"), column("customer_id", "number")


@pytest.mark.parametrize(
    ("proposed", "x", "y", "columns", "rows", "expected"),
    [
        ("bar", "customer_name", "total", [NAME, TOTAL], 5, ("bar", "customer_name", "total")),
        ("line", "month", "total", [MONTH, TOTAL], 5, ("line", "month", "total")),
        ("area", "month", "total", [MONTH, TOTAL], 5, ("area", "month", "total")),
        # Ejes inventados por el modelo: se deducen.
        ("bar", "nada", "nada", [NAME, TOTAL], 5, ("bar", "customer_name", "total")),
        ("kpi", None, None, [MONTH, TOTAL], 5, ("line", "month", "total")),
        # Una línea necesita fechas.
        ("line", "customer_name", "total", [NAME, TOTAL], 5, ("bar", "customer_name", "total")),
        ("bar", "month", "total", [MONTH, TOTAL], 5, ("bar", "month", "total")),
        ("bar", None, "total", [TOTAL], 1, ("kpi", None, "total")),
        ("table", None, None, [NAME, TOTAL], 1, ("kpi", None, "total")),
        ("table", "customer_name", "total", [NAME, TOTAL], 5, ("bar", "customer_name", "total")),
        ("table", None, None, [NAME, MONTH, TOTAL], 5, ("table", "month", "total")),
        ("bar", "customer_name", "total", [NAME, TOTAL], 0, ("table", None, None)),
        ("bar", "customer_name", None, [NAME], 5, ("table", None, None)),
        # Un identificador no es una medida.
        ("bar", "customer_name", "customer_id", [NAME, KEY], 5, ("table", None, None)),
        ("bar", "customer_name", "total", [NAME, TOTAL], 41, ("table", "customer_name", "total")),
    ],
)
def test_charts_fit_the_result(
    proposed: str,
    x: str | None,
    y: str | None,
    columns: list[ColumnInfo],
    rows: int,
    expected: tuple[str, str | None, str | None],
) -> None:
    spec = charts.choose(proposed, x, y, columns, rows)
    assert (spec.type, spec.x_axis, spec.y_axis) == expected


@pytest.mark.parametrize(
    ("kind", "expected"),
    [
        ("decimal(18,2)", "number"),
        ("bigint", "number"),
        ("timestamp(3) with time zone", "date"),
        ("date", "date"),
        ("boolean", "boolean"),
        ("varchar(10)", "text"),
    ],
)
def test_trino_types(kind: str, expected: str) -> None:
    assert charts.kind_of(kind) == expected


class SlowLLM:
    provider, model, external = "ollama", "modelo", False

    async def complete(
        self, messages: list[ChatMessage], schema: dict[str, Any] | None = None
    ) -> Completion:
        await asyncio.sleep(0.05)
        return Completion(proposal())


def test_too_many_questions_at_once_are_refused(world: World) -> None:
    async def go() -> list[Any]:
        async with httpx.AsyncClient(transport=httpx.MockTransport(world)) as http:
            catalog = GoldCatalog(http, "http://unity-catalog:8080", "lakehouse", "gold", 60)
            trino = TrinoClient(http, "http://trino:8080", "text2sql", "lakehouse", "gold", 10, 5)
            tracer = LangfuseTracer(http, "http://langfuse:3000", "a", "b")
            pipeline = Pipeline(SlowLLM(), catalog, trino, tracer, 10, max_waiting=2)  # type: ignore[arg-type]
            return await asyncio.gather(
                *(pipeline.ask("¿Ventas?") for _ in range(4)), return_exceptions=True
            )

    results = asyncio.run(go())
    assert sum(isinstance(item, BusyError) for item in results) == 2


def test_the_catalog_survives_a_short_outage(world: World) -> None:
    async def go() -> None:
        now = [0.0]
        async with httpx.AsyncClient(transport=httpx.MockTransport(world)) as http:
            catalog = GoldCatalog(
                http, "http://unity-catalog:8080/", "lakehouse", "gold", 60, lambda: now[0]
            )
            assert len(await catalog.tables()) == 2
            world.unity_down, now[0] = True, 120.0
            assert len(await catalog.tables()) == 2
            empty = GoldCatalog(http, "http://unity-catalog:8080", "lakehouse", "gold", 60)
            with pytest.raises(CatalogUnavailableError):
                await empty.tables()

    asyncio.run(go())


def test_results_are_cut_at_the_maximum(world: World) -> None:
    world.rows = [["x", "1.00"]] * 30

    async def go() -> int:
        async with httpx.AsyncClient(transport=httpx.MockTransport(world)) as http:
            trino = TrinoClient(http, "http://trino:8080", "text2sql", "lakehouse", "gold", 10, 5)
            return len((await trino.query("SELECT 1")).rows)

    assert asyncio.run(go()) == 10


def test_error_types() -> None:
    assert TrinoError("COLUMN_NOT_FOUND", "x").repairable is True
    assert TrinoError("DIVISION_BY_ZERO", "x").repairable is True
    assert TrinoError("GENERIC_INTERNAL_ERROR", "x").repairable is False
    assert TrinoTimeoutError().repairable is False
    assert QuestionFailedError("timeout", "x").sql is None
    assert build_messages([], "¿Hola?")[0].role == "system"
