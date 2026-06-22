# Prefront PII analyzer — Presidio + a small spaCy model, behind FastAPI.
FROM python:3.11-slim

WORKDIR /app

# presidio-analyzer pulls spaCy; install the SMALL English model (~12MB) — we
# only run name-based PatternRecognizers, so the heavy en_core_web_lg isn't needed.
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt \
    && python -m spacy download en_core_web_sm

COPY app.py .

EXPOSE 8020
CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "8020"]
