FROM python:3.12-slim@sha256:423ed6ab25b1921a477529254bfeeabf5855151dc2c3141699a1bfc852199fbf

COPY --from=ghcr.io/astral-sh/uv:0.10.9@sha256:10902f58a1606787602f303954cea099626a4adb02acbac4c69920fe9d278f82 /uv /bin/uv

WORKDIR /app

ENV PYTHONPATH=/app/src:/app:/app/scripts:/app/src/doc_ray/tests \
    UV_COMPILE_BYTECODE=1

# git resolves the one dependency pinned to a commit rather than a PyPI release
# (patent-ocr, requirements.txt). The runtime Dockerfile installs it for the same
# reason; omitting it here left `uv pip install` failing on this image alone, so
# the containerised suite could not build at all.
RUN apt-get update \
      && apt-get install -y --no-install-recommends \
        git \
        tesseract-ocr \
        tesseract-ocr-eng \
      && rm -rf /var/lib/apt/lists/*

COPY requirements.txt requirements-dev.txt ./
RUN uv pip install --system -r requirements.txt -r requirements-dev.txt

COPY pyproject.toml ./
COPY Dockerfile docker-compose*.yml ./
COPY .dockerignore .env.example .env.prod.example .env.test.example .gitignore ./
# PURPOSE.md and DISCLAIMER.md travel with CLAUDE.md: the doc-hygiene tests
# assert that CLAUDE.md's references to them resolve, which they cannot if the
# governing documents are absent from the image.
COPY .pre-commit-config.yaml CLAUDE.md PURPOSE.md DISCLAIMER.md ./
COPY .github ./.github
COPY README.md CHANGELOG.md engineering_decisions.yml uv.lock Makefile ./
COPY src ./src
COPY tests ./tests
COPY scripts ./scripts
COPY config ./config
COPY static/fixtures ./static/fixtures
COPY admin-console ./admin-console
COPY webapp ./webapp
COPY db ./db
COPY docs ./docs
COPY docker ./docker
COPY nginx ./nginx

RUN useradd --system --create-home --home-dir /home/docray-test --uid 10002 \
      --shell /usr/sbin/nologin docray-test
USER docray-test
ENV HOME=/home/docray-test

ENTRYPOINT ["python", "-m", "pytest"]
