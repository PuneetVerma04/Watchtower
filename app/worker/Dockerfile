FROM python:3.12-slim

COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

WORKDIR /app

# Dependency manifests copied first so this layer caches independently of source changes
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev

# Run from the venv directly: `uv run` would re-sync at startup and reinstall dev dependencies
ENV PATH="/app/.venv/bin:$PATH" \
    PYTHONUNBUFFERED=1

# This service's source only
COPY app/worker/*.py ./

CMD ["python", "main.py"]
