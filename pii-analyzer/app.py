"""Prefront PII analyzer — a small Presidio service that guesses which schema
fields are PII from their NAMES (and types) alone.

Design-time only, no row data: the Data Graph sends the list of
``{table, column, type}`` and gets back a best-guess PII entity + confidence per
column. We drive Presidio's AnalyzerEngine with a registry of custom
column-name PatternRecognizers (the built-in recognizers match values like a
real SSN/email, which never appear in a bare column name), so detection is
predictable and value-free.

    POST /pii/analyze   {"fields": [{"table","column","type"}]}
                        -> {"results": [{"table","column","entity","label","score"}]}
    GET  /healthz
"""

from __future__ import annotations

import re
from typing import List, Optional

from fastapi import FastAPI
from pydantic import BaseModel, Field

from presidio_analyzer import AnalyzerEngine, Pattern, PatternRecognizer, RecognizerRegistry
from presidio_analyzer.nlp_engine import NlpEngineProvider

# entity, friendly label, score, name-regex (matched against the humanized name)
SPECS = [
    ("EMAIL_ADDRESS",     "Email",          0.85, r"\b(e[\s-]?mail)\b"),
    ("PHONE_NUMBER",      "Phone",          0.80, r"\b(phone|mobile|telephone|fax|msisdn)\b"),
    ("US_SSN",            "SSN",            0.90, r"\b(ssn|social security( number)?)\b"),
    ("CREDIT_CARD",       "Credit card",    0.85, r"\b(credit card|card (number|no)|ccnum|cc number)\b"),
    ("US_BANK_NUMBER",    "Bank account",   0.80, r"\b(iban|routing( number)?|account (number|no)|acct (number|no)|bank account)\b"),
    ("PERSON",            "Name",           0.70, r"\b((first|last|full|sur|given|middle|maiden) name|fname|lname|surname)\b"),
    ("DATE_TIME",         "Date of birth",  0.80, r"\b(dob|date of birth|birth ?date|birthday)\b"),
    ("IP_ADDRESS",        "IP address",     0.80, r"\b(ip( address| addr)?|ipaddr)\b"),
    ("LOCATION",          "Address",        0.60, r"\b(address|street|zip ?code|postal ?code|postcode)\b"),
    ("US_DRIVER_LICENSE", "Driver license", 0.80, r"\b(driver'?s? licen[cs]e|driving licen[cs]e|dl (number|no)|licen[cs]e (number|no))\b"),
    ("US_PASSPORT",       "Passport",       0.85, r"\b(passport( number| no)?)\b"),
    ("NRP",               "Demographic",    0.55, r"\b(nationality|ethnicity|religion|gender|marital status)\b"),
]
ENTITIES = sorted({s[0] for s in SPECS})
LABELS = {s[0]: s[1] for s in SPECS}

_CAMEL = re.compile(r"(?<=[a-z0-9])(?=[A-Z])")


def humanize(name: str) -> str:
    """`date_of_birth` / `dateOfBirth` -> `date of birth` for the recognizers."""
    s = _CAMEL.sub(" ", name or "")
    s = re.sub(r"[_\-.]+", " ", s)
    return re.sub(r"\s+", " ", s).strip().lower()


def _build_analyzer() -> AnalyzerEngine:
    registry = RecognizerRegistry()  # empty — only our name recognizers, no built-ins
    for entity, _label, score, regex in SPECS:
        registry.add_recognizer(PatternRecognizer(
            supported_entity=entity,
            name=f"{entity}_colname",
            patterns=[Pattern(name=f"{entity}_name", regex=regex, score=score)],
        ))
    nlp_engine = NlpEngineProvider(nlp_configuration={
        "nlp_engine_name": "spacy",
        "models": [{"lang_code": "en", "model_name": "en_core_web_sm"}],
    }).create_engine()
    return AnalyzerEngine(registry=registry, nlp_engine=nlp_engine, supported_languages=["en"])


analyzer = _build_analyzer()
app = FastAPI(title="Prefront PII Analyzer", version="0.1.0")


class FieldIn(BaseModel):
    table: Optional[str] = None
    column: str
    type: Optional[str] = None


class AnalyzeBody(BaseModel):
    fields: List[FieldIn] = Field(default_factory=list)


@app.get("/healthz")
def healthz():
    return {"status": "ok", "entities": ENTITIES}


@app.post("/pii/analyze")
def analyze(body: AnalyzeBody):
    results = []
    for f in body.fields:
        text = humanize(f.column)
        if not text:
            continue
        found = analyzer.analyze(text=text, entities=ENTITIES, language="en")
        if not found:
            continue
        best = max(found, key=lambda r: r.score)
        results.append({
            "table": f.table,
            "column": f.column,
            "entity": best.entity_type,
            "label": LABELS.get(best.entity_type, best.entity_type),
            "score": round(float(best.score), 2),
        })
    return {"results": results}
