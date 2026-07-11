FROM python:3.12-slim

WORKDIR /app

# src/ layout: the ingest package lives under src/, so put it on the path.
ENV PYTHONPATH=/app/src

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY src ./src
# The runtime migration runner reads db/migrations/*.sql at run time.
COPY db ./db
# Flask corpus browser (compose `webapp` service overrides the entrypoint).
COPY webapp ./webapp

# Default entrypoint for the ingester; the api/migrate services override it.
ENTRYPOINT ["python", "-m", "doc_ray.entrypoints.cli"]
