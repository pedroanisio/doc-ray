# doc-ray operator tasks. Quality gates run as direct commands (see README
# § "Quality gates"); this Makefile only wraps stateful operations that need
# guardrails. Reads DATABASE_URL and friends from .env when present.

SHELL := /bin/bash
STAMP := $(shell date -u +%Y%m%dT%H%M%SZ)
DOCRAY_WORKERS ?= 3

.PHONY: help db-dump truncate-db workers-up workers-status

help: ## List available tasks
	@grep -E '^[a-z][a-zA-Z_-]*:.*## ' $(MAKEFILE_LIST) | awk -F ':.*## ' '{printf "  %-14s %s\n", $$1, $$2}'

workers-up: ## Start/reconcile the worker pool (DOCRAY_WORKERS=3 by default)
	docker compose up -d --scale worker=$(DOCRAY_WORKERS) worker

workers-status: ## Show worker replica status
	docker compose ps worker

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
