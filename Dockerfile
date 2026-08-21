FROM python:3.12-slim AS base

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

WORKDIR /app

RUN pip install --no-cache-dir uv

COPY pyproject.toml ./
COPY src ./src

# Editable install (-e), not a hard copy: docker-compose.yml bind-mounts ./src over /app/src for
# api/worker, and only an editable install actually reads from that live path — a regular
# `pip install .` copies the package into site-packages once at build time, so the bind mount
# becomes cosmetic and every container silently keeps running whatever code existed at the last
# `docker compose build`, no matter how many times you restart it or how much ./src changes.
RUN uv pip install --system --no-cache -e .

COPY alembic ./alembic
COPY alembic.ini ./alembic.ini
COPY scripts ./scripts

EXPOSE 8000

CMD ["uvicorn", "chatfolio.main:app", "--host", "0.0.0.0", "--port", "8000"]
