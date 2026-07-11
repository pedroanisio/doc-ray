#!/usr/bin/env bash
# doc-ray — automated fresh-install quickstart (docs/TUTORIAL.md, Part 1).
#
# Automates every mechanical step of the tutorial's Part 1: .env bootstrap,
# bringing up Postgres/MinIO/API/worker with health polling, minting a
# write-scoped API token (query + .env patch + restart), ingesting the
# bundled fixture, and polling the job to completion. Safe to re-run —
# every step is idempotent except the final ingest, which adds one more
# document each time you run it (matching what the tutorial's own curl
# commands do).
#
#   scripts/quickstart.sh              # full Part-1 walkthrough
#   scripts/quickstart.sh --no-ingest  # stop after minting the token
#
# What this does NOT automate, on purpose:
#   - Part 2 (installing the AI-agent skill) is already one command
#     (skills/doc-ray-graphql-maximizer/scripts/install_or_update.py).
#   - Starting the webapp is a foreground dev server; this script prints
#     the command instead of backgrounding it silently.
set -euo pipefail
cd "$(dirname "$0")/.."

NO_INGEST=0
for arg in "$@"; do
  case "$arg" in
    --no-ingest) NO_INGEST=1 ;;
    *) echo "unknown argument: $arg" >&2; exit 2 ;;
  esac
done

API_URL="http://localhost:8000"
FIXTURE="static/fixtures/AirFrance447_BEA.pdf"
TOKEN_NAME="dev-token"
POLL_TIMEOUT_SECONDS=180

log()  { echo "==> $*"; }
warn() { echo "!!  $*" >&2; }
die()  { warn "$*"; exit 1; }

require() { command -v "$1" >/dev/null 2>&1 || die "missing required command: $1"; }
require docker
require curl
require jq
require openssl

# --- Step 2: .env bootstrap -------------------------------------------------

if [ ! -f .env ]; then
  log "[env] no .env — copying .env.example"
  cp .env.example .env
fi

current_password=$(grep -E '^POSTGRES_PASSWORD=' .env | head -1 | cut -d= -f2-)
if [ -z "$current_password" ] || [ "$current_password" = "docray" ] || [ "$current_password" = "change-me-docray" ]; then
  generated_password=$(openssl rand -hex 16)
  log "[env] POSTGRES_PASSWORD is unset or the rejected default — generating one"
  if grep -qE '^POSTGRES_PASSWORD=' .env; then
    sed -i.bak "s/^POSTGRES_PASSWORD=.*/POSTGRES_PASSWORD=${generated_password}/" .env && rm -f .env.bak
  else
    echo "POSTGRES_PASSWORD=${generated_password}" >> .env
  fi
  log "[env] generated POSTGRES_PASSWORD saved to .env (not printed here — it's in the file)"
fi

# --- Step 3: Postgres + MinIO ----------------------------------------------

log "[compose] starting postgres, minio, createbucket"
docker compose up -d postgres minio createbucket

wait_for_healthy() {
  local container="$1" tries=0
  until [ "$(docker inspect -f '{{.State.Health.Status}}' "$container" 2>/dev/null)" = "healthy" ]; do
    tries=$((tries + 1))
    [ "$tries" -gt 60 ] && die "$container never became healthy — check: docker compose logs ${container#docray-}"
    sleep 2
  done
}

log "[compose] waiting for docray-postgres and docray-minio to report healthy"
wait_for_healthy docray-postgres
wait_for_healthy docray-minio
log "[compose] postgres + minio healthy"

# --- Step 4: API + worker + backup-scheduler --------------------------------

log "[compose] starting api, worker, backup-scheduler"
docker compose up -d api worker backup-scheduler

log "[api] waiting for /readyz"
tries=0
until curl -sf "${API_URL}/readyz" 2>/dev/null | jq -e '.ready == true' >/dev/null 2>&1; do
  tries=$((tries + 1))
  [ "$tries" -gt 60 ] && die "API never became ready — check: docker compose logs api"
  sleep 2
done
api_version=$(curl -s "${API_URL}/health" | jq -r '.version')
log "[api] ready (schema version ${api_version})"

# --- Step 5: mint a write-scoped API token ----------------------------------
#
# DOCRAY_API_TOKENS is a comma-separated token=principal_id list (config.py's
# _parse_api_tokens) — it may already carry other entries (e.g. a
# DOCRAY_WEBAPP_API_TOKEN binding) that a naive overwrite would silently
# destroy. This only ever appends its own entry, never replaces the line.

existing_tokens=$(grep -E '^DOCRAY_API_TOKENS=' .env | head -1 | cut -d= -f2-)

if echo "$existing_tokens" | tr ',;' '\n' | grep -qE "^${TOKEN_NAME}="; then
  log "[token] .env already has a '${TOKEN_NAME}' token — leaving it alone"
else
  log "[token] looking up the bootstrap-admin principal"
  admin_id=$(docker compose exec -T postgres psql -U docray -d docray -t -A -c \
    "SELECT id FROM principals WHERE display_name = 'bootstrap-admin';" | tr -d '[:space:]')
  [ -n "$admin_id" ] || die "no bootstrap-admin principal found — did migrations run? check: docker compose logs api"

  new_entry="${TOKEN_NAME}=${admin_id}"
  if [ -n "$existing_tokens" ]; then
    combined="${existing_tokens},${new_entry}"
    sed -i.bak "s#^DOCRAY_API_TOKENS=.*#DOCRAY_API_TOKENS=${combined}#" .env && rm -f .env.bak
  elif grep -qE '^DOCRAY_API_TOKENS=' .env; then
    sed -i.bak "s#^DOCRAY_API_TOKENS=.*#DOCRAY_API_TOKENS=${new_entry}#" .env && rm -f .env.bak
  else
    echo "DOCRAY_API_TOKENS=${new_entry}" >> .env
  fi
  log "[token] appended ${TOKEN_NAME}=<bootstrap-admin id> to DOCRAY_API_TOKENS in .env (existing entries preserved)"

  log "[compose] restarting api to pick up the new token"
  docker compose up -d api
  tries=0
  until curl -sf "${API_URL}/readyz" 2>/dev/null | jq -e '.ready == true' >/dev/null 2>&1; do
    tries=$((tries + 1))
    [ "$tries" -gt 60 ] && die "API never became ready after restart — check: docker compose logs api"
    sleep 2
  done
fi

if [ "$NO_INGEST" -eq 1 ]; then
  log "[done] --no-ingest given, stopping before Step 7. Token: ${TOKEN_NAME}"
  exit 0
fi

# --- Step 7: ingest the bundled fixture -------------------------------------

[ -f "$FIXTURE" ] || die "fixture not found: $FIXTURE"

log "[ingest] uploading ${FIXTURE}"
ingest_response=$(curl -s -H "Authorization: Bearer ${TOKEN_NAME}" \
  -F 'operations={"query":"mutation($f:Upload!){ ingestDocument(file:$f, pipeline:true){ id status kind } }","variables":{"f":null}}' \
  -F 'map={"0":["variables.f"]}' \
  -F "0=@${FIXTURE}" \
  "${API_URL}/graphql")

job_id=$(echo "$ingest_response" | jq -r '.data.ingestDocument.id // empty')
if [ -z "$job_id" ]; then
  warn "ingest did not return a job id — full response:"
  echo "$ingest_response" >&2
  die "ingest failed"
fi
log "[ingest] queued as job ${job_id}"

# --- Step 8: poll the job to completion -------------------------------------

log "[poll] waiting for job ${job_id} to finish (up to ${POLL_TIMEOUT_SECONDS}s)"
elapsed=0
status="queued"
while true; do
  job_response=$(curl -s -X POST -H 'content-type: application/json' -H "Authorization: Bearer ${TOKEN_NAME}" \
    -d "{\"query\":\"query(\$id:ID!){ job(id:\$id){ status kind error } }\",\"variables\":{\"id\":\"${job_id}\"}}" \
    "${API_URL}/graphql")
  status=$(echo "$job_response" | jq -r '.data.job.status')
  case "$status" in
    succeeded) break ;;
    failed)
      error=$(echo "$job_response" | jq -r '.data.job.error')
      die "job ${job_id} failed: ${error}"
      ;;
  esac
  elapsed=$((elapsed + 3))
  [ "$elapsed" -ge "$POLL_TIMEOUT_SECONDS" ] && die "job ${job_id} still '${status}' after ${POLL_TIMEOUT_SECONDS}s — check: docker compose logs worker"
  sleep 3
done
log "[poll] job succeeded"

# --- Verification ------------------------------------------------------------

log "[verify] querying the ingested document"
curl -s -X POST -H 'content-type: application/json' -H "Authorization: Bearer ${TOKEN_NAME}" \
  -d '{"query":"{ documents(limit:5){ id title status charCount pageCount tokenCount } }"}' \
  "${API_URL}/graphql" | jq '.data.documents'

cat <<EOF

==> Part 1 complete. Next:
    - Start the webapp:  pip install -r requirements.txt && python -m webapp.app
      then open http://localhost:5001
    - Install the AI-agent skill (Part 2):
      python3 skills/doc-ray-graphql-maximizer/scripts/install_or_update.py --dest ~/.claude/skills/doc-ray-graphql-maximizer
EOF
