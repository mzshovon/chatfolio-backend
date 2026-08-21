.PHONY: up down restart logs migrate revision test lint typecheck fmt

up:
	docker compose up --build

down:
	docker compose down

# api/worker use an editable install against the bind-mounted ./src (see Dockerfile), so a
# source change only needs a restart, not a rebuild — reserve `up`/`--build` for dependency
# changes (pyproject.toml) or a first run.
restart:
	docker compose restart api worker

logs:
	docker compose logs -f api worker

migrate:
	docker compose exec api alembic upgrade head

revision:
	docker compose exec api alembic revision --autogenerate -m "$(m)"

test:
	uv run pytest

lint:
	uv run ruff check .

fmt:
	uv run ruff format .

typecheck:
	uv run mypy src
