# Prefront OOB ingestion — pulls traces from Phoenix (and/or receives an OTLP
# fan-out) and stores every span in ClickHouse; serves the /oob query API the
# UI's Observability tab reads.
FROM python:3.12-slim

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY oobingest ./oobingest

EXPOSE 8110
ENTRYPOINT ["python", "-m", "oobingest"]
