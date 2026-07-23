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
COMPOSE_ADMIN := docker compose --env-file .env.prod -f admin-console/docker-compose.yml

# Full managed overlay set (prod + LOCAL Ollama resolver on the H200 GPU) — the
# SAME layering as `prod-ollama-up`. Naming every overlay keeps the ollama/worker
# service definitions present so a targeted `up <svc>` never treats siblings as
# orphans (the cloudflared-drop / Error-1033 lesson). The cloudflared overlay is
# deliberately excluded: its token is a hard `${...:?}` requirement interpolated
# file-wide, and the tunnel keeps running untouched (orphan warning, never removed
# without --remove-orphans).
COMPOSE_PROD_OLLAMA := $(COMPOSE_PROD) -f docker-compose.ollama.yml -f docker-compose.ollama.gpu.yml

# MCP connector server overlay on top of the managed+ollama stack. The `mcp`
# service front-ends the in-network API (api:8000) and is loopback-bound on
# :8080; a Cloudflare public-hostname rule routes doc-ray.com/mcp -> mcp:8080.
COMPOSE_PROD_MCP := $(COMPOSE_PROD_OLLAMA) -f docker-compose.mcp.yml

.PHONY: help check check-static prod-check db-dump truncate-db workers-up workers-status \
        prod-migrate prod-migrate-host prod-up prod-web prod-admin-console-up \
        prod-admin-console-down prod-tunnel prod-down gpu-up prod-ollama-up \
        prod-refresh-ui prod-restart-ui mcp-up pgadmin-up

help: ## List available tasks
	@grep -E '^[a-z][a-zA-Z_-]*:.*## ' $(MAKEFILE_LIST) | awk -F ':.*## ' '{printf "  %-17s %s\n", $$1, $$2}'

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
	echo "── read-path aggregates (D-024) ────────────────"; \
	  $(PYTHON) scripts/check_read_path_aggregates.py || fail=1; \
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
	echo "── read-path aggregates (D-024) ────────────────"; \
	  $(PYTHON) scripts/check_read_path_aggregates.py || fail=1; \
	echo "── pytest (D-013) ──────────────────────────────"; \
	  $(PYTHON) -m pytest tests src/doc_ray/tests || fail=1; \
	if [ $$fail -ne 0 ]; then echo; echo "make check: FAILED"; exit 1; fi; \
	echo; echo "make check: PASSED"

# Runtime verification of a live deployment — the counterpart to `check`, which
# only proves the source tree is sound. Reads .env.prod itself (the file wins over
# the ambient shell, same rule as COMPOSE_PROD's --env-file) and probes the real
# dependencies, ending with a REAL message handed to the SMTP2GO relay: nothing
# else in the stack exercises outbound mail, and a silently broken relay turns
# every registration into a 503.
prod-check: ## Verify the live prod deployment (config, managed DB, Spaces, API, webapp, MCP + public /mcp, Cloudflare tunnel, public URL, SMTP2GO send). TO=<email> picks the recipient; NO_SEND=1 checks the relay without delivering; JSON=1 for machine output; ENV_FILE=.env.uat to target another deployment
	@PYTHONPATH=src $(PYTHON) scripts/prod_check.py \
	  --env-file $(if $(ENV_FILE),$(ENV_FILE),.env.prod) \
	  $(if $(TO),--to $(TO)) $(if $(NO_SEND),--no-send) $(if $(JSON),--json)

workers-up: ## Start/reconcile the worker pool (DOCRAY_WORKERS=3 by default)
	docker compose up -d --scale worker=$(DOCRAY_WORKERS) worker

workers-status: ## Show worker replica status
	docker compose ps worker

prod-migrate: ## Apply migrations to the MANAGED cluster via the migrate container (needs managed .env)
	$(COMPOSE_PROD) run --rm migrate

prod-migrate-host: ## Apply migrations to the MANAGED cluster from the REPO working tree (reliable; use when the container path reports "0 applied" against a schema that is actually behind)
	DATABASE_URL="$$(grep -E '^DATABASE_URL=' .env.prod | cut -d= -f2-)" PYTHONPATH=src $(PYTHON) -m doc_ray.entrypoints.migrate

prod-up: prod-migrate ## Migrate, then start the managed stack (api + webapp + workers + backups)
	$(COMPOSE_PROD) up -d --build api webapp worker worker-fast backup-scheduler

prod-web: ## Add the public TLS front door (nginx + self-signed certgen) to a running prod stack
	$(COMPOSE_PROD) up -d --build nginx-certgen nginx

prod-admin-console-up: ## Start the loopback-only React admin console against the prod stack
	$(COMPOSE_ADMIN) up -d --build admin-console

prod-admin-console-down: ## Stop the separate React admin console stack
	$(COMPOSE_ADMIN) down

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

prod-refresh-ui: ## Rebuild + recreate api + webapp to refresh the UI (templates, static assets, cover route) — preserves cloudflared/ollama/worker routing
	$(COMPOSE_PROD_OLLAMA) up -d --build api webapp
	@echo "UI refreshed: api + webapp rebuilt and recreated (cloudflared/ollama/workers left running)."

prod-restart-ui: ## Restart api + webapp WITHOUT rebuilding (bounce the running images; use prod-refresh-ui to pick up code changes)
	$(COMPOSE_PROD_OLLAMA) restart api webapp

mcp-up: ## Start the MCP connector server (loopback :8080) on the managed stack — then route doc-ray.com/mcp -> http://mcp:8080 in the Cloudflare tunnel (token tunnel = dashboard step)
	$(COMPOSE_PROD_MCP) up -d --build mcp
	@echo "MCP server up on 127.0.0.1:$${MCP_PORT:-8080}. Public /mcp still needs a Cloudflare public-hostname rule: path /mcp -> http://mcp:8080 (or a dedicated subdomain)."

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
