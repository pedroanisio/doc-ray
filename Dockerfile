# --- deps stage: install the locked runtime set with uv (BL-114) -----------
# Both stages are digest-pinned (BL-111, same supply-chain rationale as
# BL-039's Postgres pin); tests/test_compose_security.py guards the pins. To
# bump: resolve the new index digest with
#   docker buildx imagetools inspect python:3.12-slim
FROM python:3.12-slim@sha256:423ed6ab25b1921a477529254bfeeabf5855151dc2c3141699a1bfc852199fbf AS deps

# The uv binary rides in only for this stage, pinned by digest like the bases.
COPY --from=ghcr.io/astral-sh/uv:0.10.9@sha256:10902f58a1606787602f303954cea099626a4adb02acbac4c69920fe9d278f82 /uv /bin/uv

WORKDIR /app

# Install straight into the image's Python prefix: the final stage copies
# /usr/local wholesale and needs no venv or PATH changes. --locked fails the
# build if uv.lock drifted from pyproject/requirements.txt (the point of
# BL-114); --no-install-project keeps this layer cacheable independently of
# src/ (the package rides PYTHONPATH at runtime, it is never pip-installed).
ENV UV_PROJECT_ENVIRONMENT=/usr/local \
    UV_COMPILE_BYTECODE=1
COPY pyproject.toml requirements.txt uv.lock ./
# setuptools resolves the project's dynamic version from doc_ray.__version__
# while uv validates the lock; ship just that file so this layer's cache still
# survives every other src/ change.
COPY src/doc_ray/__init__.py src/doc_ray/__init__.py
RUN uv sync --locked --no-install-project --no-dev

# --- runtime stage ----------------------------------------------------------
FROM python:3.12-slim@sha256:423ed6ab25b1921a477529254bfeeabf5855151dc2c3141699a1bfc852199fbf

WORKDIR /app

# src/ layout: the ingest package lives under src/, so put it on the path.
ENV PYTHONPATH=/app/src

COPY --from=deps /usr/local /usr/local

COPY src ./src
# The runtime migration runner reads db/migrations/*.sql at run time.
COPY db ./db
# Flask corpus browser (compose `webapp` service overrides the entrypoint).
COPY webapp ./webapp

# Least-privilege runtime (BL-112): the worker/api parse untrusted uploads
# (PDF/EPUB/DOCX), so no service built from this image runs as root. /app and
# site-packages stay root-owned (read-only to the app); runtime writes go to
# tempdirs or $HOME. /certs is pre-owned so a fresh `nginxcerts` named volume
# inherits docray ownership and the nginx-certgen one-shot can write unprivileged.
RUN useradd --system --create-home --home-dir /home/docray --uid 10001 \
      --shell /usr/sbin/nologin docray \
    && mkdir -p /certs && chown docray:docray /certs
USER docray
ENV HOME=/home/docray

# Default entrypoint for the ingester; the api/migrate services override it.
ENTRYPOINT ["python", "-m", "doc_ray.entrypoints.cli"]
