FROM python:3.12-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY ingest ./ingest

# Default command is overridden by `docker compose run ingester ...`
ENTRYPOINT ["python", "-m", "ingest.cli"]
