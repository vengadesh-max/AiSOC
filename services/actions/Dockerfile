FROM python:3.11-slim AS builder

WORKDIR /app

RUN pip install --no-cache-dir poetry==1.7.1

RUN python -m venv /app/.venv
ENV PATH="/app/.venv/bin:$PATH"

COPY pyproject.toml poetry.lock* ./

RUN set -eux; \
    if poetry install --no-interaction --no-ansi --only main --no-root; then \
        echo "[actions] poetry install succeeded"; \
    else \
        echo "[actions] poetry install failed; using pip fallback"; \
        pip install --no-cache-dir \
            "fastapi>=0.109,<0.137" \
            "uvicorn[standard]>=0.27,<0.28" \
            "pydantic>=2.5,<3" \
            "pydantic-settings>=2.1,<3" \
            "redis[hiredis]>=5.0.1,<8.0.0" \
            "asyncpg>=0.29,<0.32" \
            "sqlalchemy[asyncio]>=2.0.25,<3" \
            "structlog>=24.1,<26" \
            "httpx>=0.27,<0.29" \
            "aiokafka>=0.10,<0.15" \
            "python-dateutil>=2.8.2,<3" \
            "cryptography>=42,<43" ; \
    fi

FROM python:3.11-slim AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PATH="/app/.venv/bin:$PATH"

WORKDIR /app

COPY --from=builder /app/.venv /app/.venv

COPY app/ ./app/

RUN python -c "import asyncpg, sqlalchemy, fastapi, aiokafka, redis"

EXPOSE 8085

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8085"]
