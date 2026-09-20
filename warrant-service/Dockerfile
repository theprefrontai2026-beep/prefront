# The Policy Decision Service.
#
# Build context is the REPO ROOT so the image can copy both `warrant/` (the
# engine) and `warrant-service/` (the socket). The split is the architecture,
# not a packaging accident: the engine is installable on its own by anything
# that wants to embed it, and this image just happens to put both in one place.
FROM python:3.12-slim

WORKDIR /app
COPY warrant-service/requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r /app/requirements.txt

COPY warrant /app/warrant
COPY warrant-service /app/warrant-service

ENV PYTHONPATH=/app/warrant:/app/warrant-service \
    PYTHONUNBUFFERED=1 \
    WARRANT_PORT=8150

# Runs as a non-root user: this process holds the Mission Authority's signing
# key, which is the one secret in the system whose compromise lets an attacker
# mint consent.
RUN useradd --create-home --uid 10001 warrant
USER warrant

EXPOSE 8150
HEALTHCHECK --interval=10s --timeout=3s --retries=5 --start-period=5s \
  CMD python -c "import urllib.request;urllib.request.urlopen('http://localhost:8150/healthz')"

CMD ["python", "-m", "warrantservice"]
