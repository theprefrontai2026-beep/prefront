# Prefront Skill Builder — design-time FastAPI service.
FROM python:3.12-slim

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY alembic.ini .
COPY skillbuilder ./skillbuilder

# Published skill registry lives on a mounted volume; the database is Postgres
# in the stack (SKILLBUILDER_DB DSN from compose). The default below is a local
# SQLite file for standalone runs.
ENV SKILLBUILDER_DB=/data/skillbuilder.db \
    SKILLBUILDER_REGISTRY=/data/skills
EXPOSE 8000

# Apply migrations (no-op if already at head) then serve.
CMD ["sh", "-c", "alembic upgrade head && uvicorn skillbuilder.api:app --host 0.0.0.0 --port 8000"]
