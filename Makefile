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

## Build the web UI, then start the API on port 9500 (serves the UI at /ui)
run: ui-build
	$(UV) run uvicorn geonames_server.api.app:app --host 0.0.0.0 --port 9500

## Regenerate openapi.yaml from the app's routes (then make ui-types)
openapi:
	$(UV) run python -m geonames_server.scripts.openapi

## Fail if openapi.yaml is out of date with the app's routes
openapi-check:
	@$(UV) run python -m geonames_server.scripts.openapi --check

## Run the test suite (the *_pg.py tests skip without TEST_PG_DSN, the Redis tests
## without TEST_REDIS_URL; see tests-pg)
tests: sync-dev
	$(UV) run pytest tests/ -v

# Throwaway database for the *_pg.py tests, which drop and recreate its schemas.
# Create it once: psql -h db -U postgres -c "create database geonames_test"
TEST_PG_DB ?= geonames_test
env_value = $(strip $(shell sed -n 's/^$(1) *= *//p' .env 2>/dev/null))
TEST_PG_DSN ?= host=$(call env_value,POSTGRES_HOST) port=$(call env_value,POSTGRES_PORT) user=$(call env_value,POSTGRES_USER) password=$(call env_value,POSTGRES_PASSWORD) dbname=$(TEST_PG_DB)

# Spare Redis database for the rate limit tests, which flush it: REDIS_URL from
# .env with database TEST_REDIS_DB (query options kept). Empty, so the Redis
# tests skip, when .env has no REDIS_URL.
TEST_REDIS_DB ?= 15
env_redis_url = $(call env_value,REDIS_URL)
TEST_REDIS_URL ?= $(if $(env_redis_url),$(shell echo '$(env_redis_url)' | sed -E 's#^([^/]*//[^/?]*)(/[0-9]*)?(\?.*)?$$#\1/$(TEST_REDIS_DB)\3#'))

## Run the whole test suite, including the Postgres tests against TEST_PG_DB
## (default geonames_test, on the server in .env) and the Redis tests against
## database TEST_REDIS_DB (default 15) of REDIS_URL
tests-pg: sync-dev
	@echo "uv run pytest tests/ (TEST_PG_DSN: dbname=$(TEST_PG_DB), TEST_REDIS_URL: $(TEST_REDIS_URL))"
	@TEST_PG_DSN="$(TEST_PG_DSN)" TEST_REDIS_URL="$(TEST_REDIS_URL)" $(UV) run pytest tests/ -q

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

## Check requirements.txt (what the Docker image installs) matches uv.lock
requirements-check:
	@tmp=$$(mktemp -d); \
	$(UV) export --frozen --no-dev --no-hashes --no-emit-project -q -o $$tmp/expected; \
	grep -v '^#' $$tmp/expected > $$tmp/expected.body; grep -v '^#' requirements.txt > $$tmp/current.body; \
	if diff -u $$tmp/current.body $$tmp/expected.body; then rm -rf $$tmp; \
	else rm -rf $$tmp; echo "requirements.txt is out of date with uv.lock: run make requirements"; exit 1; fi

## Run every check before deploying: lint, formatting, lockfile and all tests,
## for the API and the web UI. Run it in the devcontainer, then `make image-check`
## where Docker runs.
predeploy: lint-check format-check lock-check requirements-check openapi-check tests-pg ui-lint ui-test ui-types-check
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

##@ Frontend (web UI in frontend/, served at /ui)

NPM = npm --prefix frontend

## Install the web UI's packages from package-lock.json
ui-install:
	$(NPM) ci

## Start the Vite dev server on 5173 (hot reload); it proxies the API on 9500
ui-dev: frontend/node_modules
	$(NPM) run dev

# Installed only when missing; run make ui-install after package-lock.json changes
frontend/node_modules:
	$(NPM) ci

## Build the web UI into frontend/dist, which the API serves at /ui
ui-build: frontend/node_modules
	$(NPM) run build

## Lint and type-check the web UI
ui-lint: frontend/node_modules
	$(NPM) run lint

## Run the web UI's tests once
ui-test: frontend/node_modules
	$(NPM) test

## Regenerate the web UI's API types from openapi.yaml (after `make openapi`)
ui-types: frontend/node_modules
	$(NPM) run gen:api

## Fail if the web UI's API types are out of date with openapi.yaml
ui-types-check: frontend/node_modules
	@cd frontend && npx openapi-typescript ../openapi.yaml -o node_modules/.tmp/schema.d.ts >/dev/null \
		&& cmp -s node_modules/.tmp/schema.d.ts src/api/schema.d.ts \
		|| { echo "ui-types-check: src/api/schema.d.ts is stale, run make ui-types"; exit 1; }

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

# Dev tool parameters:
#   ROLE   seeded users, comma-separated (free, tester, dev, admin; default all)
#   LIMIT  set (LIMIT=1) for the hourly limit
#   QUOTA  set (QUOTA=1) for the monthly quota; neither LIMIT nor QUOTA means both
#   MINUTE set (MINUTE=1) to reset the minute limit too (reset-usage)
#   ARGS   any other options of the script (--help lists them)
comma := ,
ROLE_ARGS = $(foreach r,$(subst $(comma), ,$(ROLE)),--role $(r))
# The windows LIMIT and QUOTA pick, as repeated $(1) options
WINDOW_ARGS = $(if $(LIMIT)$(QUOTA),$(if $(LIMIT),$(1) hour) $(if $(QUOTA),$(1) month),$(1) hour $(1) month)

## Call the API as the seeded users, e.g. ROLE=free ARGS="--count 5", or ARGS=--portal
api-call:
	API_URL=$(API_URL) $(UV) run python -m geonames_server.scripts.api_client $(ROLE_ARGS) $(ARGS)

## Load-test the API as the seeded users, e.g. ROLE=dev ARGS="--concurrency 20 --duration 60"
stress:
	API_URL=$(API_URL) $(UV) run python -m geonames_server.scripts.stress_api load $(ROLE_ARGS) $(ARGS)

## Reset the seeded users' hourly limit and monthly quota (dev only), e.g. ROLE=free LIMIT=1
reset-usage:
	$(UV) run python -m geonames_server.scripts.reset_usage $(ROLE_ARGS) $(call WINDOW_ARGS,--window) $(if $(MINUTE),--window minute) $(ARGS)

## Check the minute limit, and the hourly limit and monthly quota, for real, e.g. ROLE=free QUOTA=1
stress-limits:
	API_URL=$(API_URL) $(UV) run python -m geonames_server.scripts.stress_api limits $(ROLE_ARGS) $(call WINDOW_ARGS,--quota) $(ARGS)

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

## Build the image tagged $(VERSION) and latest. The version comes from
## pyproject.toml, as does the app's own (/health, /docs).
build-image: build-image-version
	docker tag $(IMAGE):$(VERSION) $(IMAGE):latest

## Build the image tagged $(VERSION)
build-image-version:
	docker build --label org.opencontainers.image.version=$(VERSION) -t $(IMAGE):$(VERSION) .

## Build the image and smoke-test it before pushing: the app imports and reports
## the image's version, each migration chain (geonames, app) has exactly one head,
## and the web UI is built in. Tags latest too, so both tags are the checked image. Needs no database.
image-check: build-image
	docker run --rm --entrypoint python $(IMAGE):$(VERSION) -c "import geonames_server.api.app"
	@app_version=$$(docker run --rm --entrypoint python $(IMAGE):$(VERSION) -c "from geonames_server.config.config import settings; print(settings.app_version)" 2>/dev/null | tail -1); \
	if [ "$$app_version" != "$(VERSION)" ]; then echo "image-check: app reports version '$$app_version', image is $(VERSION)"; exit 1; fi
	@for chain in alembic app; do \
		heads=$$(docker run --rm --entrypoint alembic $(IMAGE):$(VERSION) -n $$chain heads 2>/dev/null | grep -c '(head)'); \
		if [ "$$heads" != 1 ]; then echo "image-check: $$chain chain has $$heads heads, expected 1"; exit 1; fi; \
	done
	@docker run --rm --entrypoint python $(IMAGE):$(VERSION) -c "from geonames_server.config.config import settings as s; assert (s.ui_root / 'index.html').is_file(), f'no web UI in {s.ui_root}'; print(f'web UI in {s.ui_root}')"
	@echo "image-check: $(IMAGE):$(VERSION) ok"

## Push the image tagged latest and $(VERSION)
push-image: push-image-version
	docker push $(IMAGE):latest

## Push the image tagged $(VERSION)
push-image-version:
	docker push $(IMAGE):$(VERSION)

##@ GitHub (run in the dev container)

# The GitHub account that owns this repository. gh-auth compares the logged-in
# account with it: the host's gh is a different account, and gh's own error
# does not say so.
GH_OWNER ?= neo7dev

# Colours only on a terminal, so piped output and CI logs stay plain.
ifneq (,$(findstring xterm,$(TERM)))
  BOLD   := $(shell tput bold)
  RED    := $(shell tput setaf 1)
  GREEN  := $(shell tput setaf 2)
  YELLOW := $(shell tput setaf 3)
  BLUE   := $(shell tput setaf 4)
  RESET  := $(shell tput sgr0)
endif
SAY  := printf "$(BLUE)==>$(RESET) $(BOLD)%s$(RESET)\n"
OK   := printf "$(GREEN)  ok$(RESET) %s\n"
WARN := printf "$(YELLOW)  !!$(RESET) %s\n"

# Nothing here reads, echoes, masks or length-checks GH_TOKEN: presence is tested
# with [ -n ] and identity is asked of GitHub. `gh auth status` is not used
# because it prints a masked token, which still discloses part of it.

## Check that gh is authenticated as the account that owns this repository
gh-auth:
	@$(SAY) "gh authentication"
	@if ! command -v gh >/dev/null 2>&1; then \
	  printf "  %-14s $(RED)%s$(RESET)\n" "gh" "not installed"; \
	  $(WARN) "these targets run inside the dev container - reopen the repo in it"; exit 1; fi
	@printf "  %-14s %s\n" "gh" "$$(gh --version | head -1)"
	@if [ -z "$${GH_TOKEN:-}" ]; then \
	  printf "  %-14s $(YELLOW)%s$(RESET)\n" "GH_TOKEN" "unset or empty"; \
	  printf "  %-14s %s\n" "" "the container reads it from GEONAMES_GH_TOKEN on the host"; \
	  printf "  %-14s %s\n" "" "exported for VS Code by ~/.zsh/host/macbook.secrets.zsh"; \
	  printf "  %-14s %s\n" "" "if that export exists: quit VS Code fully and relaunch - Reload"; \
	  printf "  %-14s %s\n" "" "Window and Rebuild Container both reuse the cached shell env"; \
	else \
	  printf "  %-14s $(GREEN)%s$(RESET)\n" "GH_TOKEN" "present"; \
	fi
	@login=""; \
	 if out="$$(gh api user --jq .login 2>/dev/null)"; then login="$$(printf '%s' "$$out" | head -1)"; fi; \
	 case "$$login" in *[!A-Za-z0-9-]*) login="" ;; esac; \
	 if [ -z "$$login" ]; then \
	   printf "  %-14s $(RED)%s$(RESET)\n" "identity" "not authenticated"; \
	   if [ -n "$${GH_TOKEN:-}" ]; then \
	     $(WARN) "GH_TOKEN is set but GitHub rejected it - expired or revoked"; \
	     $(WARN) "after rotating it the container still holds the old value until"; \
	     $(WARN) "VS Code is fully quit and relaunched"; \
	   else \
	     $(WARN) "no credential at all - see GH_TOKEN above"; \
	   fi; \
	   exit 1; \
	 elif [ "$$login" != "$(GH_OWNER)" ]; then \
	   printf "  %-14s $(RED)%s$(RESET)\n" "identity" "$$login"; \
	   $(WARN) "this repository belongs to $(GH_OWNER) - $$login is not a collaborator"; \
	   $(WARN) "gh pr create would fail with 'must be a collaborator'"; \
	   $(WARN) "run make inside the dev container - the host gh is a different account"; \
	   exit 1; \
	 else \
	   printf "  %-14s $(GREEN)%s$(RESET)\n" "identity" "$$login"; \
	 fi
	@$(OK) "gh can write to $(GH_OWNER)"

## Check the git identity and that origin is reachable for pushing
git-auth:
	@$(SAY) "git authentication"
	@printf "  %-14s %s\n" "user.name" "$$(git config user.name || echo '(unset)')"
	@printf "  %-14s %s\n" "user.email" "$$(git config user.email || echo '(unset)')"
	@if [ -z "$$(git config user.name)" ] || [ -z "$$(git config user.email)" ]; then \
	  $(WARN) "this repo is owned by a different account than the host default -"; \
	  $(WARN) "set them repo-locally rather than relying on the global config"; \
	fi
	@url="$$(git remote get-url origin 2>/dev/null || true)"; \
	 if [ -z "$$url" ]; then printf "  %-14s $(RED)%s$(RESET)\n" "origin" "no remote"; exit 1; fi; \
	 printf "  %-14s %s\n" "origin" "$$url"; \
	 case "$$url" in \
	   git@github.com-neo7:*) ;; \
	   git@github.com:*) $(WARN) "plain github.com selects the host's default key - this repo" ; \
	                     $(WARN) "needs the github.com-neo7 ssh alias" ;; \
	 esac
	@if [ -n "$${SSH_AUTH_SOCK:-}" ] && [ -S "$${SSH_AUTH_SOCK:-}" ]; then \
	   printf "  %-14s $(GREEN)%s$(RESET)\n" "ssh-agent" "forwarded"; \
	 else \
	   printf "  %-14s $(YELLOW)%s$(RESET)\n" "ssh-agent" "not forwarded"; \
	   printf "  %-14s %s\n" "" "VS Code forwards it to terminals it opens; a plain"; \
	   printf "  %-14s %s\n" "" "'docker exec' does not - push from a VS Code terminal"; \
	 fi
# Reachability and emptiness are separate questions. `ls-remote --exit-code` exits 2
# when no refs MATCH, so on a repository that exists but has no commits yet it is
# indistinguishable from an auth failure - which reported "origin unreachable" on a
# correctly configured new repo, sending you to debug an agent that was fine.
	@refs=$$(git ls-remote --heads origin 2>/dev/null); rc=$$?; \
	 if [ $$rc -ne 0 ]; then \
	   printf "  %-14s $(RED)%s$(RESET)\n" "push access" "origin unreachable"; \
	   $(WARN) "the host keys are mounted read-only and the agent does the signing"; \
	   $(WARN) "so this usually means the agent is missing rather than a bad key"; \
	   $(WARN) "check in a VS Code terminal: ssh-add -l"; \
	   exit 1; \
	 elif [ -z "$$refs" ]; then \
	   printf "  %-14s $(GREEN)%s$(RESET)\n" "push access" "origin reachable (no branches yet)"; \
	   $(OK) "git can reach origin; it has no commits, so nothing to compare against"; \
	 else \
	   printf "  %-14s $(GREEN)%s$(RESET)\n" "push access" "origin reachable"; \
	   $(OK) "git can push to origin"; \
	 fi

.PHONY: install install-dev help run api-call stress stress-limits reset-usage tests tests-pg build lint lint-check format format-check lock-check requirements-check predeploy clean openapi openapi-check ui-install ui-dev ui-build ui-lint ui-test ui-types ui-types-check build-image init add add-dev requirements requirements-nohash requirements-dev requirements-frozen lock status supabase-keys switch2prod switch2dev download-speed download-datasets remote-etag migrations db-auto-revision db-revision db-upgrade db-downgrade db-app-auto-revision db-app-revision db-app-upgrade db-app-downgrade seed-users check-keys build-image build-image-version image-check push-image push-image-version gh-auth git-auth
