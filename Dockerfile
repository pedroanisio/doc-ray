# --- deps stage: install the locked runtime set with uv (BL-114) -----------
# Both stages are digest-pinned (BL-111, same supply-chain rationale as
# BL-039's Postgres pin); tests/test_compose_security.py guards the pins. To
# bump: resolve the new index digest with
#   docker buildx imagetools inspect python:3.12-slim
FROM python:3.14-slim@sha256:cad9a2c871761c413caa6fdd6441c783451e740a48aaeba60ae62a8b53525ef6 AS deps

# The uv binary rides in only for this stage, pinned by digest like the bases.
COPY --from=ghcr.io/astral-sh/uv:0.10.9@sha256:10902f58a1606787602f303954cea099626a4adb02acbac4c69920fe9d278f82 /uv /bin/uv

WORKDIR /app

# git is needed to resolve the one dependency pinned to a commit rather than a
# PyPI release (patent-ocr). It stays in this stage only — the runtime image
# copies /usr/local and never inherits the build toolchain.
RUN apt-get update \
      && apt-get install -y --no-install-recommends git \
      && rm -rf /var/lib/apt/lists/*

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
FROM python:3.14-slim@sha256:cad9a2c871761c413caa6fdd6441c783451e740a48aaeba60ae62a8b53525ef6

WORKDIR /app

# src/ layout: the ingest package lives under src/, so put it on the path.
ENV PYTHONPATH=/app/src

# Tesseract is a RUNTIME dependency of the extractor, not a test-only one: an
# image-only PDF (a scan) has no text layer, and without this the worker's OCR
# fallback cannot run at all — every scanned upload lands as status='failed'
# with "OCR required before ingest", advice no deployment built from this image
# could take. Language packs are the whole cost (~5 MB each); add more here to
# widen DOCRAY_PDF_OCR_LANGUAGE.
#
# The language list is driven by the corpus, not by guesswork. A Latin model run
# over Cyrillic or CJK does not degrade — it emits confident Latin lookalikes at
# zero percent correct script, which is fabricated text that passes every
# downstream check. Ghostscript is here for the same reason: the column-aware
# patent reader (DOCRAY_PATENT_EXTRACTION) needs it to rasterise.
# `frk` covers German-language publications before roughly 1941, which are set
# in Fraktur and unreadable with `deu` alone. Debian names that pack
# `tesseract-ocr-frk`, matching the Tesseract language code used in
# ports/patent_text_source.py ("deu+frk"). There is no
# `tesseract-ocr-deu-frak` in trixie; requesting it fails the build with
# `Unable to locate package` and exit 100.
RUN apt-get update \
      && apt-get install -y --no-install-recommends \
        ghostscript \
        tesseract-ocr \
        tesseract-ocr-ces \
        tesseract-ocr-deu \
        tesseract-ocr-frk \
        tesseract-ocr-eng \
        tesseract-ocr-fra \
        tesseract-ocr-ita \
        tesseract-ocr-jpn \
        tesseract-ocr-nld \
        tesseract-ocr-por \
        tesseract-ocr-rus \
        tesseract-ocr-spa \
        tesseract-ocr-swe \
      && rm -rf /var/lib/apt/lists/*

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
