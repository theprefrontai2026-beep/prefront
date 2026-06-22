"""SQLAlchemy data model for the Skill Builder.

One ORM, two backends: the same models run on **PostgreSQL** in the deployed
stack (``SKILLBUILDER_DB=postgresql+psycopg://…``) and on **SQLite** for fast
offline tests / local dev (a bare filesystem path, or ``sqlite://`` for
in-memory). ``engine_from_url`` normalizes either form.

The legacy ``rule_json`` / ``artifact_json`` columns stay TEXT (they carry a
JSON *string* that callers produce via ``model_dump_json()``), preserving the
exact :class:`Store` contract. New tables (atoms / unresolved / profiles /
review events) use a JSON column that maps to ``JSONB`` on Postgres.
"""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import (
    JSON,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    create_engine,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.engine import Engine
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, sessionmaker

# JSON that becomes JSONB on Postgres but plain JSON on SQLite.
JSONB_OR_JSON = JSON().with_variant(JSONB, "postgresql")


def now_iso() -> str:
    """ISO-8601 UTC timestamp (matches the old ``datetime('now')`` text)."""
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


class Base(DeclarativeBase):
    pass


# --- existing tables (mirror the previous sqlite schema) ----------------------


class SourceDocument(Base):
    __tablename__ = "source_documents"

    document_id: Mapped[str] = mapped_column(String, primary_key=True)
    file_name: Mapped[str] = mapped_column(String, nullable=False)
    file_type: Mapped[str] = mapped_column(String, nullable=False)
    file_hash: Mapped[str] = mapped_column(String, nullable=False)
    domain: Mapped[str] = mapped_column(String, nullable=False)
    owner: Mapped[str | None] = mapped_column(String)
    version: Mapped[str | None] = mapped_column(String)
    status: Mapped[str] = mapped_column(String, nullable=False, default="uploaded")
    raw_text: Mapped[str | None] = mapped_column(Text)
    uploaded_by: Mapped[str | None] = mapped_column(String)
    uploaded_at: Mapped[str] = mapped_column(String, default=now_iso)
    # Optional datasource schema captured at upload time, used to derive a
    # default (column-only) domain pack so extraction/validation ground in
    # vocabulary the binder can resolve. See schema_pack.py.
    ddl: Mapped[str | None] = mapped_column(Text)
    datasource_id: Mapped[str | None] = mapped_column(String)


class DocumentSection(Base):
    __tablename__ = "document_sections"

    section_id: Mapped[str] = mapped_column(String, primary_key=True)
    document_id: Mapped[str | None] = mapped_column(
        ForeignKey("source_documents.document_id")
    )
    section_path: Mapped[str | None] = mapped_column(String)
    heading: Mapped[str | None] = mapped_column(String)
    page_start: Mapped[int | None] = mapped_column(Integer)
    page_end: Mapped[int | None] = mapped_column(Integer)
    markdown: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[str] = mapped_column(String, default=now_iso)


class PolicyClause(Base):
    __tablename__ = "policy_clauses"

    clause_id: Mapped[str] = mapped_column(String, primary_key=True)
    document_id: Mapped[str | None] = mapped_column(
        ForeignKey("source_documents.document_id")
    )
    section_id: Mapped[str | None] = mapped_column(
        ForeignKey("document_sections.section_id")
    )
    clause_type: Mapped[str | None] = mapped_column(String)
    # Disposition assigned by the classifier (see classifier.py). NULL until set.
    disposition: Mapped[str | None] = mapped_column(String)
    source_text: Mapped[str] = mapped_column(Text, nullable=False)
    page_number: Mapped[int | None] = mapped_column(Integer)
    paragraph_ref: Mapped[str | None] = mapped_column(String)
    created_at: Mapped[str] = mapped_column(String, default=now_iso)


class CandidateRuleRow(Base):
    __tablename__ = "candidate_rules"

    candidate_rule_id: Mapped[str] = mapped_column(String, primary_key=True)
    document_id: Mapped[str | None] = mapped_column(
        ForeignKey("source_documents.document_id")
    )
    clause_id: Mapped[str | None] = mapped_column(
        ForeignKey("policy_clauses.clause_id")
    )
    rule_key: Mapped[str] = mapped_column(String, nullable=False)
    rule_type: Mapped[str] = mapped_column(String, nullable=False)
    rule_json: Mapped[str] = mapped_column(Text, nullable=False)  # JSON string
    confidence: Mapped[float | None] = mapped_column(Float)
    review_status: Mapped[str] = mapped_column(
        String, nullable=False, default="pending"
    )
    reviewer_notes: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[str] = mapped_column(String, default=now_iso)
    updated_at: Mapped[str] = mapped_column(String, default=now_iso)


class ApprovedPolicyRule(Base):
    __tablename__ = "approved_policy_rules"

    policy_rule_id: Mapped[str] = mapped_column(String, primary_key=True)
    rule_key: Mapped[str] = mapped_column(String, nullable=False)
    domain: Mapped[str] = mapped_column(String, nullable=False)
    rule_type: Mapped[str] = mapped_column(String, nullable=False)
    rule_json: Mapped[str] = mapped_column(Text, nullable=False)  # JSON string
    source_document_id: Mapped[str | None] = mapped_column(
        ForeignKey("source_documents.document_id")
    )
    source_clause_id: Mapped[str | None] = mapped_column(
        ForeignKey("policy_clauses.clause_id")
    )
    version: Mapped[str] = mapped_column(String, nullable=False)
    effective_from: Mapped[str | None] = mapped_column(String)
    effective_to: Mapped[str | None] = mapped_column(String)
    status: Mapped[str] = mapped_column(String, nullable=False, default="active")
    approved_by: Mapped[str | None] = mapped_column(String)
    approved_at: Mapped[str | None] = mapped_column(String)
    created_at: Mapped[str] = mapped_column(String, default=now_iso)


class SkillVersion(Base):
    __tablename__ = "skill_versions"

    skill_version_id: Mapped[str] = mapped_column(String, primary_key=True)
    skill_id: Mapped[str] = mapped_column(String, nullable=False)
    version: Mapped[str] = mapped_column(String, nullable=False)
    domain: Mapped[str] = mapped_column(String, nullable=False)
    status: Mapped[str] = mapped_column(String, nullable=False)
    artifact_json: Mapped[str] = mapped_column(Text, nullable=False)  # JSON string
    approved_by: Mapped[str | None] = mapped_column(String)
    approved_at: Mapped[str | None] = mapped_column(String)
    created_at: Mapped[str] = mapped_column(String, default=now_iso)


# --- new tables (added for the full design) -----------------------------------


class ExtractionRun(Base):
    __tablename__ = "extraction_runs"

    extraction_run_id: Mapped[str] = mapped_column(String, primary_key=True)
    document_id: Mapped[str | None] = mapped_column(
        ForeignKey("source_documents.document_id")
    )
    status: Mapped[str] = mapped_column(String, nullable=False, default="running")
    model_name: Mapped[str | None] = mapped_column(String)
    domain_pack: Mapped[str | None] = mapped_column(String)
    started_at: Mapped[str] = mapped_column(String, default=now_iso)
    completed_at: Mapped[str | None] = mapped_column(String)
    error_message: Mapped[str | None] = mapped_column(Text)


class DocumentProfileRow(Base):
    __tablename__ = "document_profiles"

    profile_id: Mapped[str] = mapped_column(String, primary_key=True)
    document_id: Mapped[str | None] = mapped_column(
        ForeignKey("source_documents.document_id")
    )
    profile_json: Mapped[dict] = mapped_column(JSONB_OR_JSON, nullable=False)
    created_at: Mapped[str] = mapped_column(String, default=now_iso)


class PolicyAtomRow(Base):
    __tablename__ = "policy_atoms"

    atom_id: Mapped[str] = mapped_column(String, primary_key=True)
    document_id: Mapped[str | None] = mapped_column(
        ForeignKey("source_documents.document_id")
    )
    clause_id: Mapped[str | None] = mapped_column(
        ForeignKey("policy_clauses.clause_id")
    )
    extraction_run_id: Mapped[str | None] = mapped_column(
        ForeignKey("extraction_runs.extraction_run_id")
    )
    atom_type: Mapped[str] = mapped_column(String, nullable=False)
    atom_json: Mapped[dict] = mapped_column(JSONB_OR_JSON, nullable=False)
    confidence: Mapped[float | None] = mapped_column(Float)
    created_at: Mapped[str] = mapped_column(String, default=now_iso)


class UnresolvedItemRow(Base):
    __tablename__ = "unresolved_items"

    unresolved_id: Mapped[str] = mapped_column(String, primary_key=True)
    document_id: Mapped[str | None] = mapped_column(
        ForeignKey("source_documents.document_id")
    )
    clause_id: Mapped[str | None] = mapped_column(
        ForeignKey("policy_clauses.clause_id")
    )
    candidate_rule_id: Mapped[str | None] = mapped_column(
        ForeignKey("candidate_rules.candidate_rule_id")
    )
    unresolved_type: Mapped[str] = mapped_column(String, nullable=False)
    severity: Mapped[str] = mapped_column(String, nullable=False)
    status: Mapped[str] = mapped_column(String, nullable=False, default="open")
    item_json: Mapped[dict] = mapped_column(JSONB_OR_JSON, nullable=False)
    resolved_by: Mapped[str | None] = mapped_column(String)
    resolved_at: Mapped[str | None] = mapped_column(String)
    resolution_notes: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[str] = mapped_column(String, default=now_iso)


class ReviewEvent(Base):
    __tablename__ = "review_events"

    review_event_id: Mapped[str] = mapped_column(String, primary_key=True)
    document_id: Mapped[str | None] = mapped_column(
        ForeignKey("source_documents.document_id")
    )
    candidate_rule_id: Mapped[str | None] = mapped_column(
        ForeignKey("candidate_rules.candidate_rule_id")
    )
    unresolved_id: Mapped[str | None] = mapped_column(
        ForeignKey("unresolved_items.unresolved_id")
    )
    event_type: Mapped[str] = mapped_column(String, nullable=False)
    actor: Mapped[str] = mapped_column(String, nullable=False)
    before_json: Mapped[dict | None] = mapped_column(JSONB_OR_JSON)
    after_json: Mapped[dict | None] = mapped_column(JSONB_OR_JSON)
    comment: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[str] = mapped_column(String, default=now_iso)


# --- engine helpers -----------------------------------------------------------


def normalize_url(db: str) -> str:
    """Accept a SQLAlchemy URL as-is, or treat a bare path as a SQLite file."""
    if "://" in db:
        return db
    return f"sqlite:///{db}"


def engine_from_url(db: str) -> Engine:
    """Build an engine for a DSN or a sqlite path; enable SQLite FK enforcement."""
    url = normalize_url(db)
    connect_args = {}
    if url.startswith("sqlite"):
        # FastAPI runs sync routes across a threadpool; one shared connection.
        connect_args["check_same_thread"] = False
    engine = create_engine(url, future=True, connect_args=connect_args)
    if url.startswith("sqlite"):
        from sqlalchemy import event

        @event.listens_for(engine, "connect")
        def _fk_on(dbapi_conn, _rec):  # pragma: no cover - trivial
            cur = dbapi_conn.cursor()
            cur.execute("PRAGMA foreign_keys=ON")
            cur.close()

    return engine


def make_session_factory(engine: Engine) -> sessionmaker:
    return sessionmaker(bind=engine, future=True, expire_on_commit=False)
