# Image for the SecureBank demo services (ungoverned LLM-SQL + the orchestrator).
# The Prefront engine package (semanticmcp) is NOT copied — it's mounted at
# /engine so the governed agent always runs the live engine code.
FROM python:3.12-slim
RUN pip install --no-cache-dir "openai>=1.40" "psycopg[binary]>=3.1" pyyaml "mcp>=1.0"
WORKDIR /app
COPY . /app
