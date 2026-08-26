# Image for the LoanPro demo services: the app's own tool API served over MCP
# (app_mcp_server.py), the agent that calls it as an MCP client
# (ungoverned_server.py), and the scenario orchestrator (demo_server.py).
# No Prefront engine code is involved — LoanPro is an ungoverned deployment.
FROM python:3.12-slim
# starlette + uvicorn: the app API serves MCP over SSE.
# mcp <2 is deliberate — 2.0 removed the low-level Server decorators.
RUN pip install --no-cache-dir "openai>=1.40" "psycopg[binary]>=3.1" pyyaml "mcp>=1.24,<2" \
    "starlette>=0.37" "uvicorn>=0.29" \
    "arize-phoenix-otel>=0.6" "openinference-instrumentation-openai>=0.1.20" \
    "openinference-instrumentation-mcp>=1.1"
WORKDIR /app
COPY . /app
