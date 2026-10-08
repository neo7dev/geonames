# variables
PYTHON = python3
UV = uv
UVX = uvx
PROJECT_NAME := $(shell grep -m 1 "name =" pyproject.toml | cut -d '"' -f 2)
VERSION := $(shell grep -m 1 "version =" pyproject.toml | tr -s ' ' | cut -d '"' -f 2)
MESSAGE ?= "manual_revision"
VERSION ?= $(shell grep -m 1 "version =" pyproject.toml | tr -s ' ' | cut -d '"' -f 2)
APP_NAME ?= $(shell grep -m 1 "name =" pyproject.toml | tr -s ' ' | cut -d '"' -f 2)

all: sync run test lint clean

status:
	@echo "Project: $(PROJECT_NAME)"
	@echo "Version: $(VERSION)"

switch2prod:
	@cp .env.prod .env
	@echo "PRODUCTION ENVIRONMENT ACTIVE. Copied .env.prod to .env. Remember to set the appropriate environment variables in .env.prod."

switch2dev:
	@cp .env.dev .env
	@echo "DEVELOPMENT ENVIRONMENT ACTIVE. Copied .env.dev to .env. Remember to set the appropriate environment variables in .env.dev."

init:
	$(UV) init

add:
	$(UV) add

add-dev:
	$(UV) add --dev

sync:
	$(UV) sync

sync-dev:
	$(UV) sync --dev

requirements:
	$(UV) export --format requirements-txt > requirements.txt

requirements-nohash:
	$(UV) export --format requirements-txt --no-hashes > requirements.txt

requirements-dev:
	$(UV) export --group dev > requirements-dev.txt

requirements-frozen:
	$(UV) export --frozen --locked > requirements.txt

build: tests sync
	$(UV) build

lock:
	$(UV) lock

run:
	$(UV) run uvicorn geonames_server.api.app:app --host 0.0.0.0 --port 9500

tests: sync-dev
	$(UV) run pytest tests/ -v

lint:
	$(UVX) ruff --fix

lint-check:
	$(UVX) ruff check

db-auto-revision:
	alembic revision --autogenerate -m "$(MESSAGE)"

db-revision:
	alembic revision -m "$(MESSAGE)"

db-upgrade:
	alembic upgrade head

db-downgrade:
	alembic downgrade -1

remote-etag:
	@echo "allcountries.zip"
	@curl -sI "http://download.geonames.org/export/dump/allCountries.zip" | grep -Ei "etag|last-modified"
	@echo "cities500.zip"
	@curl -sI "http://download.geonames.org/export/dump/cities500.zip" | grep -Ei "etag|last-modified"
	@echo "cities1000.zip"
	@curl -sI "http://download.geonames.org/export/dump/cities1000.zip" | grep -Ei "etag|last-modified"
	@echo "cities5000.zip"
	@curl -sI "http://download.geonames.org/export/dump/cities5000.zip" | grep -Ei "etag|last-modified"
	@echo "cities15000.zip"
	@curl -sI "http://download.geonames.org/export/dump/cities15000.zip" | grep -Ei "etag|last-modified"

clean:
	rm -rf __pycache__
	rm -rf .pytest_cache
	rm -rf .ruff_cache
	rm -rf .mypy_cache
	rm -rf dist
	rm -rf build
	find . -type d -name "__pycache__" -exec rm -rf {} +
	find . -type d -name ".pytest_cache" -exec rm -rf {} +
	find . -type d -name ".ruff_cache" -exec rm -rf {} +
	find . -type d -name ".mypy_cache" -exec rm -rf {} +

build-image: build-image-version
	docker build -t ghcr.io/neo7dev/$(APP_NAME):latest .

build-image-version:
	docker build -t ghcr.io/neo7dev/$(APP_NAME):$(VERSION) .

push-image: push-image-version
	docker push ghcr.io/neo7dev/$(APP_NAME):latest

push-image-version:
	docker push ghcr.io/neo7dev/$(APP_NAME):$(VERSION)

.PHONY: install install-dev run tests build lint lint-check clean build-image init add add-dev requirements requirements-nohash requirements-dev requirements-frozen lock status switch2prod switch2dev remote-etag migrations db-auto-revision db-revision db-upgrade db-downgrade build-image build-image-version push-image push-image-version