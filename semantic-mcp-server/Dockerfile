# Prefront Semantic MCP Server — runtime that serves query templates over MCP.
FROM python:3.12-slim

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY semanticmcp ./semanticmcp

EXPOSE 8090
# Global flags + subcommand are supplied by docker-compose `command:`,
# e.g. --templates=/artifacts/.../query_templates.yaml serve --http --port=8090
ENTRYPOINT ["python", "-m", "semanticmcp"]
