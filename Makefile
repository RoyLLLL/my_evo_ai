.PHONY: migrate init revision upgrade downgrade run seed-admin seed-client seed-mcp-servers seed-tools seed-all docker-build docker-up docker-down docker-logs lint format install install-dev venv

# Alembic commands
init:
	alembic init alembics

# make alembic-revision message="migration description"
alembic-revision:
	alembic revision --autogenerate -m "$(message)"

# Command to update database to latest version (execute existing migrations)
alembic-upgrade:
	alembic upgrade head

# Command to downgrade one version
alembic-downgrade:
	alembic downgrade -1

# Command to run the server
run:
	uvicorn src.main:app --host 0.0.0.0 --port 8000 --reload --env-file .env --reload-exclude frontend/ --reload-exclude "*.log" --reload-exclude "*.tmp"

# Command to run the server in production mode
run-prod:
	uvicorn src.main:app --host 0.0.0.0 --port 8000 --workers 4

# Command to clean cache in all project folders
clear-cache:
	rm -rf ~/.cache/uv/environments-v2/* && find . -type d -name "__pycache__" -exec rm -r {} +

# Command to create a new migration and apply it
alembic-migrate:
	alembic revision --autogenerate -m "$(message)" && alembic upgrade head

# Command to reset the database
alembic-reset:
	alembic downgrade base && alembic upgrade head
	
# Commands to run seeders
seed-admin:
	python -m scripts.seeders.admin_seeder

seed-client:
	python -m scripts.seeders.client_seeder

seed-mcp-servers:
	python -m scripts.seeders.mcp_server_seeder

seed-tools:
	python -m scripts.seeders.tool_seeder

seed-all:
	python -m scripts.run_seeders

# Docker commands
docker-build:
	docker-compose build

docker-up:
	docker-compose up -d

docker-down:
	docker-compose down

docker-logs:
	docker-compose logs -f

docker-seed:
	docker-compose exec api python -m scripts.run_seeders

# Testing, linting and formatting commands
lint:
	flake8 src/ tests/

format:
	black src/ tests/

# Virtual environment and installation commands
venv:
	python -m venv venv

install:
	pip install -e .

install-dev:
	pip install -e ".[dev]"