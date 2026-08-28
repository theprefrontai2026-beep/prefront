# Prefront evaluation engine: spans -> Session -> checks -> verdicts/findings.
# Reads the shared `spans` ClickHouse table, writes its own eval_* tables in
# the same database; serves the /eval query API :8120.
FROM python:3.12-slim

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY evalengine ./evalengine

EXPOSE 8120
ENTRYPOINT ["python", "-m", "evalengine"]
