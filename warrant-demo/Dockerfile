# The Arcadia console.
#
# Runs on the standard library plus `cryptography` (which the engine needs to
# sign anything). Deliberately no FastAPI: this is the image most likely to be
# started on a stranger's laptop before a meeting, and every dependency is
# another thing that can fail to install at the wrong moment.
#
# Two modes, chosen by env:
#   WARRANT_PDS_URL unset -> decisions from the engine embedded in this process
#   WARRANT_PDS_URL set   -> decisions from warrant-service over HTTP
# The twelve situations decide identically either way; a test asserts it.
FROM python:3.12-slim

WORKDIR /app
COPY warrant/requirements.txt /app/warrant-requirements.txt
RUN pip install --no-cache-dir -r /app/warrant-requirements.txt

COPY warrant /app/warrant
COPY warrant-service/warrantservice /app/warrant-service/warrantservice
COPY warrant-demo /app/warrant-demo

ENV PYTHONPATH=/app/warrant:/app/warrant-service:/app/warrant-demo \
    PYTHONUNBUFFERED=1

RUN useradd --create-home --uid 10002 console
USER console

WORKDIR /app/warrant-demo
EXPOSE 8140
HEALTHCHECK --interval=10s --timeout=3s --retries=5 --start-period=5s \
  CMD python -c "import urllib.request;urllib.request.urlopen('http://localhost:8140/healthz')"

# `--check` at start would be a nice guard but must never stop the demo coming
# up in front of an audience; the server prints the same warning and serves.
CMD ["python", "server.py", "--port", "8140"]
