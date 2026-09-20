FROM python:3.12-slim

COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

WORKDIR /app

# Dependency manifests copied first so this layer caches independently of source changes
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev

# This service's source only -- sits alongside pyproject.toml/.venv, same layout as local dev
COPY app/orders/*.py ./

EXPOSE 8000
CMD ["uv", "run", "uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
