# Prefront Semantic Layer — design-time compiler (runs as a one-shot build job).
FROM python:3.12-slim

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY semanticlayer ./semanticlayer

# Args (build/validate/serve ...) are supplied by docker-compose `command:`.
ENTRYPOINT ["python", "-m", "semanticlayer"]
