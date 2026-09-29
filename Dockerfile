FROM python:3.12-slim

COPY --from=ghcr.io/astral-sh/uv:0.11 /uv /usr/local/bin/uv

WORKDIR /app
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy PYTHONUNBUFFERED=1

COPY pyproject.toml uv.lock README.md ./
RUN uv sync --frozen --no-dev --no-install-project

COPY threads_only_bot ./threads_only_bot
RUN uv sync --frozen --no-dev

USER nobody
CMD ["/app/.venv/bin/python", "-m", "threads_only_bot"]
