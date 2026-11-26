FROM ghcr.io/astral-sh/uv:0.13.9@sha256:d3b601f0c7b118bc1753febf7c41cc80981df678445e0254284eecf84db79a7d AS uv

FROM python:3.13.18-slim-bookworm@sha256:f0fa218d5b06f9b9235024fa061cde2cb141f726de52134471053d2d9b5324a3 AS base
ENV PYTHONUNBUFFERED=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never
WORKDIR /app
COPY --from=uv /uv /usr/local/bin/uv
COPY pyproject.toml uv.lock ./

# docker build --target test: lint, formato, tipos y pruebas
FROM base AS test
RUN uv sync --locked --no-install-project
COPY src ./src
COPY tests ./tests
RUN uv sync --locked \
    && uv run --no-sync ruff check . \
    && uv run --no-sync ruff format --check . \
    && uv run --no-sync mypy \
    && uv run --no-sync pytest -q

FROM base AS build
RUN uv sync --locked --no-dev --no-install-project
COPY src ./src
RUN uv sync --locked --no-dev --no-editable

FROM python:3.13.18-slim-bookworm@sha256:f0fa218d5b06f9b9235024fa061cde2cb141f726de52134471053d2d9b5324a3 AS runtime
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PATH=/app/.venv/bin:$PATH
RUN useradd --system --uid 10001 --no-create-home app
COPY --from=build /app/.venv /app/.venv
USER 10001
CMD ["python", "-m", "medallion_worker"]
