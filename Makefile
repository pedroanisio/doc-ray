# doc-ray operator tasks. `make check` runs the full landing-checklist quality
# gates (CLAUDE.md § "Landing checklist — static gates, not only pytest"); the
# remaining targets wrap stateful operations that need guardrails. Reads
# DATABASE_URL and friends from .env when present.

SHELL := /bin/bash
STAMP := $(shell date -u +%Y%m%dT%H%M%SZ)
DOCRAY_WORKERS ?= 3
# Interpreter for the quality gates. Prefer the project venv (matches
# truncate-db); override with e.g. `make check PYTHON=python3`.
PYTHON ?= $(shell [ -x .venv/bin/python ] && echo .venv/bin/python || echo python3)

# Managed-services (DO Managed Postgres + Spaces) compose invocation. Production
# has its OWN env file (.env.prod) — never the development .env; --env-file
# replaces .env for interpolation. The prod overlay repoints every app service at
# the managed cluster and gates the local postgres/minio out.
COMPOSE_PROD := docker compose --env-file .env.prod -f docker-compose.yml -f docker-compose.prod.yml

.PHONY: help check check-static db-dump truncate-db workers-up workers-status \
        prod-migrate prod-up prod-web prod-tunnel prod-down gpu-up prod-ollama-up pgadmin-up

help: ## List available tasks
	@grep -E '^[a-z][a-zA-Z_-]*:.*## ' $(MAKEFILE_LIST) | awk -F ':.*## ' '{printf "  %-14s %s\n", $$1, $$2}'

check-static: ## Run the static landing gates only (lint, types, size, version) — fast, no DB
	@fail=0; \
	echo "── ruff check (D-011) ──────────────────────────"; \
	  $(PYTHON) -m ruff check src scripts webapp || fail=1; \
	echo "── ruff format --check (D-011) ─────────────────"; \
	  $(PYTHON) -m ruff format --check src scripts webapp || fail=1; \
	echo "── mypy (D-012) ────────────────────────────────"; \
	  $(PYTHON) -m mypy src || fail=1; \
	echo "── file size (D-015) ───────────────────────────"; \
	  $(PYTHON) scripts/check_file_size.py src/doc_ray webapp scripts || fail=1; \
	echo "── version consistency ─────────────────────────"; \
	  $(PYTHON) scripts/check_version_consistency.py || fail=1; \
	if [ $$fail -ne 0 ]; then echo; echo "static gates: FAILED"; exit 1; fi; \
	echo; echo "static gates: PASSED"

check: ## Run ALL landing-checklist gates (static gates + pytest); mirrors CLAUDE.md
	@fail=0; \
	echo "── ruff check (D-011) ──────────────────────────"; \
	  $(PYTHON) -m ruff check src scripts webapp || fail=1; \
	echo "── ruff format --check (D-011) ─────────────────"; \
	  $(PYTHON) -m ruff format --check src scripts webapp || fail=1; \
	echo "── mypy (D-012) ────────────────────────────────"; \
	  $(PYTHON) -m mypy src || fail=1; \
	echo "── file size (D-015) ───────────────────────────"; \
	  $(PYTHON) scripts/check_file_size.py src/doc_ray webapp scripts || fail=1; \
	echo "── version consistency ─────────────────────────"; \
	  $(PYTHON) scripts/check_version_consistency.py || fail=1; \
	echo "── pytest (D-013) ──────────────────────────────"; \
	  $(PYTHON) -m pytest tests src/doc_ray/tests || fail=1; \
	if [ $$fail -ne 0 ]; then echo; echo "make check: FAILED"; exit 1; fi; \
	echo; echo "make check: PASSED"

workers-up: ## Start/reconcile the worker pool (DOCRAY_WORKERS=3 by default)
	docker compose up -d --scale worker=$(DOCRAY_WORKERS) worker

workers-status: ## Show worker replica status
	docker compose ps worker

prod-migrate: ## Apply migrations to the MANAGED cluster (needs managed .env)
	$(COMPOSE_PROD) run --rm migrate

prod-up: prod-migrate ## Migrate, then start the managed stack (api + webapp + workers + backups)
	$(COMPOSE_PROD) up -d --build api webapp worker worker-fast backup-scheduler

prod-web: ## Add the public TLS front door (nginx + self-signed certgen) to a running prod stack
	$(COMPOSE_PROD) up -d --build nginx-certgen nginx

prod-tunnel: ## Start the Cloudflare Tunnel ingress (no public ports; needs CLOUDFLARE_TUNNEL_TOKEN)
	$(COMPOSE_PROD) -f docker-compose.cloudflared.yml up -d cloudflared

prod-down: ## Stop the managed stateless stack (managed data is off-box; nothing is wiped)
	$(COMPOSE_PROD) down

gpu-up: ## Start DOCRAY_WORKERS GPU annotate workers on an H200 box (managed + CDI)
	$(COMPOSE_PROD) -f docker-compose.gpu.yml up -d --build --scale worker=$(DOCRAY_WORKERS) worker

prod-ollama-up: prod-migrate ## Migrate, then run the managed stack with the LOCAL Ollama resolver on the H200 GPU
	# Layered AFTER the prod overlay so the ollama overlay's worker resolver-env and
	# ollama health-dependency win over prod's `depends_on: !reset {}`. Set
	# DOCRAY_OLLAMA_MODEL (the benchmark winner) in .env.prod; the backend defaults
	# to ollama via the overlay. Compose with `make gpu-up` to also put the trf
	# annotate worker on the GPU (independent tenant; both fit the H200).
	$(COMPOSE_PROD) -f docker-compose.ollama.yml -f docker-compose.ollama.gpu.yml \
	  up -d --build ollama ollama-pull api webapp worker worker-fast backup-scheduler

pgadmin-up: ## Start the loopback-only pgAdmin GUI (tunnel to 127.0.0.1:5050)
	docker compose -f docker-compose.pgadmin.yml up -d pgadmin

db-dump: ## Safety-dump the full database to backups/ (pg_dump -Fc)
	docker exec docray-postgres pg_dump -U docray -d docray -Fc -f /tmp/docray-$(STAMP).dump
	@mkdir -p backups
	docker cp docray-postgres:/tmp/docray-$(STAMP).dump backups/docray-$(STAMP).dump
	docker exec docray-postgres rm -f /tmp/docray-$(STAMP).dump
	@echo "dump written: backups/docray-$(STAMP).dump"

truncate-db: ## Wipe the corpus for fresh uploads (CONFIRM=yes required; takes a db-dump first)
ifneq ($(CONFIRM),yes)
	@echo "This wipes every document, annotation, and job (auth, canonical registry,"
	@echo "schema, and backups are preserved). A safety dump is taken first."
	@echo "Run:  make truncate-db CONFIRM=yes"
	@exit 1
else
	$(MAKE) db-dump
	set -a && source .env 2>/dev/null; set +a && \
	  .venv/bin/python scripts/truncate_corpus.py --yes
endif
