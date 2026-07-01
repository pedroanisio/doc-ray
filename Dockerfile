FROM python:3.12-slim

WORKDIR /app

# src/ layout: the ingest package lives under src/, so put it on the path.
ENV PYTHONPATH=/app/src

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY src ./src

# Default command is overridden by `docker compose run ingester ...`
ENTRYPOINT ["python", "-m", "doc_ray.entrypoints.cli"]
