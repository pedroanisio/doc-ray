#!/usr/bin/env sh
set -eu

PROJECT_NAME="${DOCRAY_TEST_PROJECT_NAME:-docray-test}"
ENV_FILE="${DOCRAY_TEST_ENV_FILE:-.env.test}"
COMPOSE_FILE="docker-compose.test.yml"

if [ ! -f "$ENV_FILE" ]; then
  cp .env.test.example "$ENV_FILE"
  password="$(openssl rand -hex 16 2>/dev/null || python -c 'import secrets; print(secrets.token_hex(16))')"
  if sed --version >/dev/null 2>&1; then
    sed -i "s/^TEST_POSTGRES_PASSWORD=.*/TEST_POSTGRES_PASSWORD=${password}/" "$ENV_FILE"
  else
    sed -i '' "s/^TEST_POSTGRES_PASSWORD=.*/TEST_POSTGRES_PASSWORD=${password}/" "$ENV_FILE"
  fi
  echo "Created ${ENV_FILE} with a generated TEST_POSTGRES_PASSWORD."
fi

if [ "$#" -gt 0 ]; then
  TEST_PYTEST_ARGS="$*"
  export TEST_PYTEST_ARGS
fi

compose() {
  docker compose --env-file "$ENV_FILE" --project-name "$PROJECT_NAME" -f "$COMPOSE_FILE" "$@"
}

compose down --volumes --remove-orphans
set +e
compose up --build --abort-on-container-exit --exit-code-from test-runner test-runner
status=$?
set -e
compose down --volumes --remove-orphans
exit "$status"
