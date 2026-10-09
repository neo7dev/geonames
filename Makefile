# variables
# Upstream GeoNames dump directory. Used by download-datasets and remote-etag.
# Not the app's GEONAMES_URL, which points at slow-download-server in dev.
GEONAMES_DUMP_URL ?= https://download.geonames.org/export/dump/
PYTHON = python3
UV = uv
UVX = uvx
PROJECT_NAME := $(shell grep -m 1 "name =" pyproject.toml | cut -d '"' -f 2)
VERSION := $(shell grep -m 1 "version =" pyproject.toml | tr -s ' ' | cut -d '"' -f 2)
MESSAGE ?= "manual_revision"
VERSION ?= $(shell grep -m 1 "version =" pyproject.toml | tr -s ' ' | cut -d '"' -f 2)
APP_NAME ?= $(shell grep -m 1 "name =" pyproject.toml | tr -s ' ' | cut -d '"' -f 2)

# Help text comes from comments: `##@ Section` starts a section, and `## text` lines
# directly above a target describe it (extra lines are shown indented below the first).

## Run sync, run, test, lint and clean
all: sync run test lint clean

##@ Help

## Show this help
help:
	@awk ' \
		/^##@ / { printf "\n\033[1m%s\033[0m\n", substr($$0, 5); desc = ""; next } \
		/^## / { desc = desc (desc == "" ? "" : "\n") substr($$0, 4); next } \
		/^[a-zA-Z0-9_%-]+:/ && desc != "" { \
			split($$0, t, ":"); n = split(desc, lines, "\n"); \
			printf "  \033[36m%-22s\033[0m %s\n", t[1], lines[1]; \
			for (i = 2; i <= n; i++) printf "  %-22s   %s\n", "", lines[i]; \
		} \
		{ desc = "" } \
	' $(MAKEFILE_LIST)

## Show project name and version
status:
	@echo "Project: $(PROJECT_NAME)"
	@echo "Version: $(VERSION)"

##@ Environment

## Copy .env.prod to .env
switch2prod:
	@cp .env.prod .env
	@echo "PRODUCTION ENVIRONMENT ACTIVE. Copied .env.prod to .env. Remember to set the appropriate environment variables in .env.prod."

## Print fresh Supabase secrets (passwords, JWT secret, anon/service keys) for .env
supabase-keys:
	@$(PYTHON) supabase/generate-keys.py

## Copy .env.dev to .env
switch2dev:
	@cp .env.dev .env
	@echo "DEVELOPMENT ENVIRONMENT ACTIVE. Copied .env.dev to .env. Remember to set the appropriate environment variables in .env.dev."

##@ Dependencies

## Initialise a uv project
init:
	$(UV) init

## Add a dependency (uv add)
add:
	$(UV) add

## Add a dev dependency (uv add --dev)
add-dev:
	$(UV) add --dev

## Install dependencies
sync:
	$(UV) sync

## Install dependencies including dev
sync-dev:
	$(UV) sync --dev

## Export requirements.txt (no dev, no hashes)
requirements:
	$(UV) export --no-dev --no-hashes --no-emit-project -o requirements.txt

## Export requirements.txt without hashes
requirements-nohash:
	$(UV) export --format requirements-txt --no-hashes > requirements.txt

## Export requirements-dev.txt
requirements-dev:
	$(UV) export --group dev > requirements-dev.txt

## Export requirements.txt from the frozen lockfile
requirements-frozen:
	$(UV) export --frozen --locked > requirements.txt

## Update uv.lock
lock:
	$(UV) lock

##@ Development

## Run tests, then build the package
build: tests sync
	$(UV) build

## Start the API on port 9500
run:
	$(UV) run uvicorn geonames_server.api.app:app --host 0.0.0.0 --port 9500

## Run the test suite (the *_pg.py tests skip without TEST_PG_DSN; see tests-pg)
tests: sync-dev
	$(UV) run pytest tests/ -v

# Throwaway database for the *_pg.py tests, which drop and recreate its schemas.
# Create it once: psql -h db -U postgres -c "create database geonames_test"
TEST_PG_DB ?= geonames_test
env_value = $(strip $(shell sed -n 's/^$(1) *= *//p' .env 2>/dev/null))
TEST_PG_DSN ?= host=$(call env_value,POSTGRES_HOST) port=$(call env_value,POSTGRES_PORT) user=$(call env_value,POSTGRES_USER) password=$(call env_value,POSTGRES_PASSWORD) dbname=$(TEST_PG_DB)

## Run the whole test suite, including the Postgres tests against TEST_PG_DB
## (default geonames_test, on the server in .env)
tests-pg: sync-dev
	@echo "uv run pytest tests/ (TEST_PG_DSN: dbname=$(TEST_PG_DB))"
	@TEST_PG_DSN="$(TEST_PG_DSN)" $(UV) run pytest tests/ -q

## Lint with ruff and apply its fixes
lint:
	$(UV) run ruff check --fix

## Lint with ruff without fixing
lint-check:
	$(UV) run ruff check

## Format the code with ruff
format:
	$(UV) run ruff format

## Check the formatting without changing files
format-check:
	$(UV) run ruff format --check

## Check uv.lock matches pyproject.toml (the image installs from uv.lock)
lock-check:
	$(UV) lock --check

## Run every check before deploying: lint, formatting, lockfile and all tests.
## Run it in the devcontainer, then `make image-check` where Docker runs.
predeploy: lint-check format-check lock-check tests-pg
	@echo "predeploy: all checks passed"

## Remove caches and build artifacts
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

##@ Database

## Autogenerate an Alembic revision (MESSAGE="...")
db-auto-revision:
	alembic revision --autogenerate -m "$(MESSAGE)"

## Create an empty Alembic revision (MESSAGE="...")
db-revision:
	alembic revision -m "$(MESSAGE)"

## Apply all migrations
db-upgrade:
	alembic upgrade head

## Roll back one migration
db-downgrade:
	alembic downgrade -1

## Autogenerate a revision for the app schema (user data) (MESSAGE="...")
db-app-auto-revision:
	alembic -n app revision --autogenerate -m "$(MESSAGE)"

## Create an empty revision for the app schema (user data) (MESSAGE="...")
db-app-revision:
	alembic -n app revision -m "$(MESSAGE)"

## Apply all app schema (user data) migrations
db-app-upgrade:
	alembic -n app upgrade head

## Roll back one app schema (user data) migration
db-app-downgrade:
	alembic -n app downgrade -1

## Create one dev user per tier with known passwords and API keys (needs DEV_SEED_USERS=true)
seed-users:
	$(UV) run python -m geonames_server.scripts.seed_dev_users $(SEED_ARGS)

API_URL ?= http://localhost:9500

# Call /metadata/ (needs a key, not a built dataset) with DEV_API_KEY_<$(1)> from .env
define check_dev_key
	@key=$$(sed -n 's/^DEV_API_KEY_$(1) *= *//p' .env); \
	if [ -z "$$key" ]; then echo "DEV_API_KEY_$(1): not set in .env (copy it from .env.dev)"; exit 1; fi; \
	code=$$(curl -s -o /dev/null -w '%{http_code}' -H "X-API-Key: $$key" "$(API_URL)/metadata/"); \
	case $$code in \
		200) echo "DEV_API_KEY_$(1): ok" ;; \
		000) echo "DEV_API_KEY_$(1): API not reachable at $(API_URL) (start it with make run)"; exit 1 ;; \
		401) echo "DEV_API_KEY_$(1): rejected (401); run make seed-users"; exit 1 ;; \
		*) echo "DEV_API_KEY_$(1): HTTP $$code"; exit 1 ;; \
	esac
endef

## Check one dev user's API key against the running API
## Targets: check-key-free, check-key-tester, check-key-dev, check-key-admin
check-key-%:
	$(call check_dev_key,$(shell echo $* | tr a-z A-Z))

## Check all four dev API keys against the running API (API_URL=..., default http://localhost:9500)
check-keys:
	@fail=0; for role in free tester dev admin; do \
		$(MAKE) -s --no-print-directory check-key-$$role 2>/dev/null || fail=1; \
	done; exit $$fail

##@ GeoNames data

## Set slow-download-server speed (interactive)
## Non-interactive: make download-speed RATE=2m
download-speed:
	@bash .devcontainer/scripts/set-download-speed.sh $(RATE)

## Download datasets into gostatic/ for slow-download-server (interactive)
## Asks whether to also fetch the current daily modifications/deletes pair.
## Non-interactive: make download-datasets DATASETS=cities15000,cities5000 DAILY=yes
## Other source: make download-datasets GEONAMES_DUMP_URL=https://mirror.example.com/dump/
download-datasets:
	@GEONAMES_DUMP_URL="$(GEONAMES_DUMP_URL)" DATASETS="$(DATASETS)" DAILY="$(DAILY)" bash .devcontainer/scripts/download-datasets.sh

## Show ETag and Last-Modified of the remote dataset files
remote-etag:
	@for file in allCountries.zip cities500.zip cities1000.zip cities5000.zip cities15000.zip; do \
		echo "$$file"; \
		curl -sI "$(GEONAMES_DUMP_URL)$$file" | grep -Ei "etag|last-modified"; \
	done

##@ Docker image

IMAGE ?= ghcr.io/neo7dev/$(APP_NAME)

## Build the image tagged latest and $(VERSION)
build-image: build-image-version
	docker build -t $(IMAGE):latest .

## Build the image tagged $(VERSION)
build-image-version:
	docker build -t $(IMAGE):$(VERSION) .

## Build the image and smoke-test it before pushing: the app imports, and each
## migration chain (geonames, app) has exactly one head. Needs no database.
image-check: build-image-version
	docker run --rm --entrypoint python $(IMAGE):$(VERSION) -c "import geonames_server.api.app"
	@for chain in alembic app; do \
		heads=$$(docker run --rm --entrypoint alembic $(IMAGE):$(VERSION) -n $$chain heads 2>/dev/null | grep -c '(head)'); \
		if [ "$$heads" != 1 ]; then echo "image-check: $$chain chain has $$heads heads, expected 1"; exit 1; fi; \
	done
	@echo "image-check: $(IMAGE):$(VERSION) ok"

## Push the image tagged latest and $(VERSION)
push-image: push-image-version
	docker push $(IMAGE):latest

## Push the image tagged $(VERSION)
push-image-version:
	docker push $(IMAGE):$(VERSION)

.PHONY: install install-dev help run tests tests-pg build lint lint-check format format-check lock-check predeploy clean build-image init add add-dev requirements requirements-nohash requirements-dev requirements-frozen lock status supabase-keys switch2prod switch2dev download-speed download-datasets remote-etag migrations db-auto-revision db-revision db-upgrade db-downgrade db-app-auto-revision db-app-revision db-app-upgrade db-app-downgrade seed-users check-keys build-image build-image-version image-check push-image push-image-version
