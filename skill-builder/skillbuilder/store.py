"""Persistence for the Skill Builder (SQLAlchemy over Postgres or SQLite).

The public surface is unchanged from the original sqlite implementation — the
same method names and return shapes (plain dicts, with a decoded ``rule`` key on
candidate rows) — so ``api.py`` / ``cli.py`` are agnostic to the backend. The
engine is chosen from ``SKILLBUILDER_DB``: a full SQLAlchemy URL
(``postgresql+psycopg://…``) or a bare path treated as a SQLite file.

Design rule honored: documents are immutable. Re-uploading the same content
(same file_hash at the same version) returns the existing row instead of
overwriting it. Sections/clauses are UPSERTed (stable deterministic ids) so a
re-extraction never deletes a clause a candidate/approved rule still references.
"""

from __future__ import annotations

import hashlib
import json
import uuid
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterable, Optional

from sqlalchemy import delete, inspect, select

from . import db as dbm
from .db import (
    ApprovedPolicyRule,
    Base,
    CandidateRuleRow,
    DocumentProfileRow,
    DocumentSection,
    ExtractionRun,
    PolicyAtomRow,
    PolicyClause,
    SourceDocument,
    SkillVersion,
    UnresolvedItemRow,
    now_iso,
)


def _uuid() -> str:
    return uuid.uuid4().hex


def _as_dict(obj: Any) -> dict[str, Any]:
    """Map an ORM instance's columns to a plain dict (like ``dict(sqlite3.Row)``)."""
    return {c.key: getattr(obj, c.key) for c in inspect(obj).mapper.column_attrs}


class Store:
    """Thin data-access layer over a SQLAlchemy engine."""

    def __init__(self, path: str | Path = "skillbuilder.db") -> None:
        self.url = dbm.normalize_url(str(path))
        self._engine = dbm.engine_from_url(str(path))
        # Dev/SQLite: create tables on boot. On Postgres, Alembic owns the
        # schema, but create_all is idempotent and harmless if already migrated.
        Base.metadata.create_all(self._engine)
        self._Session = dbm.make_session_factory(self._engine)

    def close(self) -> None:
        self._engine.dispose()

    @contextmanager
    def _session(self):
        s = self._Session()
        try:
            yield s
            s.commit()
        except Exception:
            s.rollback()
            raise
        finally:
            s.close()

    # -- documents ------------------------------------------------------------

    def add_document(
        self,
        *,
        file_name: str,
        file_type: str,
        raw_text: str,
        domain: str,
        owner: Optional[str] = None,
        version: Optional[str] = None,
        uploaded_by: Optional[str] = None,
        document_id: Optional[str] = None,
        ddl: Optional[str] = None,
        datasource_id: Optional[str] = None,
    ) -> dict[str, Any]:
        """Insert a document, or return the existing row if content is identical.

        Identity is (file_hash, version): same bytes at the same version is the
        same immutable document. New content -> new row.
        """
        file_hash = "sha256:" + hashlib.sha256(raw_text.encode("utf-8")).hexdigest()
        with self._session() as s:
            existing = s.scalars(
                select(SourceDocument).where(SourceDocument.file_hash == file_hash)
            ).all()
            for row in existing:
                if (row.version or "") == (version or ""):
                    return _as_dict(row)

            doc = SourceDocument(
                document_id=document_id or _uuid(),
                file_name=file_name,
                file_type=file_type,
                file_hash=file_hash,
                domain=domain,
                owner=owner,
                version=version,
                status="uploaded",
                raw_text=raw_text,
                uploaded_by=uploaded_by,
                ddl=ddl,
                datasource_id=datasource_id,
            )
            s.add(doc)
            s.flush()
            return _as_dict(doc)

    def get_document(self, document_id: str) -> dict[str, Any]:
        with self._session() as s:
            doc = s.get(SourceDocument, document_id)
            if doc is None:
                raise KeyError(f"document not found: {document_id}")
            return _as_dict(doc)

    def list_documents(self) -> list[dict[str, Any]]:
        with self._session() as s:
            rows = s.scalars(
                select(SourceDocument).order_by(SourceDocument.uploaded_at.desc())
            ).all()
            return [_as_dict(r) for r in rows]

    def set_document_status(self, document_id: str, status: str) -> None:
        with self._session() as s:
            doc = s.get(SourceDocument, document_id)
            if doc is not None:
                doc.status = status

    def delete_document(self, document_id: str) -> None:
        """Delete a document and everything derived from it (children first)."""
        with self._session() as s:
            if s.get(SourceDocument, document_id) is None:
                raise KeyError(document_id)
            # Child rows first to satisfy FK constraints.
            s.execute(
                delete(UnresolvedItemRow).where(
                    UnresolvedItemRow.document_id == document_id
                )
            )
            s.execute(
                delete(CandidateRuleRow).where(
                    CandidateRuleRow.document_id == document_id
                )
            )
            s.execute(
                delete(ApprovedPolicyRule).where(
                    ApprovedPolicyRule.source_document_id == document_id
                )
            )
            s.execute(
                delete(PolicyClause).where(PolicyClause.document_id == document_id)
            )
            s.execute(
                delete(DocumentSection).where(
                    DocumentSection.document_id == document_id
                )
            )
            s.execute(
                delete(SourceDocument).where(
                    SourceDocument.document_id == document_id
                )
            )

    # -- sections / clauses ---------------------------------------------------

    def replace_sections(self, document_id: str, sections: Iterable[Any]) -> int:
        with self._session() as s:
            s.execute(
                delete(DocumentSection).where(
                    DocumentSection.document_id == document_id
                )
            )
            n = 0
            for sec in sections:
                s.add(
                    DocumentSection(
                        section_id=f"{document_id}:{sec.section_id}",
                        document_id=document_id,
                        section_path=sec.section_path,
                        heading=sec.heading,
                        page_start=sec.page_start,
                        page_end=sec.page_end,
                        markdown=sec.markdown,
                    )
                )
                n += 1
        return n

    def persist_structure(
        self, document_id: str, sections: Iterable[Any], clauses: Iterable[Any]
    ) -> tuple[int, int]:
        """UPSERT sections AND clauses in one transaction (FK-safe re-runs)."""
        with self._session() as s:
            ns = 0
            for sec in sections:
                sid = f"{document_id}:{sec.section_id}"
                row = s.get(DocumentSection, sid)
                if row is None:
                    row = DocumentSection(section_id=sid, document_id=document_id)
                    s.add(row)
                row.section_path = sec.section_path
                row.heading = sec.heading
                row.page_start = sec.page_start
                row.page_end = sec.page_end
                row.markdown = sec.markdown
                ns += 1
            nc = 0
            for cl in clauses:
                cid = f"{document_id}:{cl.clause_id}"
                row = s.get(PolicyClause, cid)
                if row is None:
                    row = PolicyClause(clause_id=cid, document_id=document_id)
                    s.add(row)
                row.section_id = (
                    f"{document_id}:{cl.section_id}" if cl.section_id else None
                )
                row.clause_type = cl.clause_type
                row.source_text = cl.source_text
                row.page_number = cl.page_number
                row.paragraph_ref = cl.paragraph_ref
                # disposition is set later by the classifier; preserve if present.
                disp = getattr(cl, "disposition", None)
                if disp is not None:
                    row.disposition = disp
                nc += 1
        return ns, nc

    def replace_clauses(self, document_id: str, clauses: Iterable[Any]) -> int:
        with self._session() as s:
            s.execute(
                delete(PolicyClause).where(PolicyClause.document_id == document_id)
            )
            n = 0
            for cl in clauses:
                s.add(
                    PolicyClause(
                        clause_id=f"{document_id}:{cl.clause_id}",
                        document_id=document_id,
                        section_id=f"{document_id}:{cl.section_id}"
                        if cl.section_id
                        else None,
                        clause_type=cl.clause_type,
                        disposition=getattr(cl, "disposition", None),
                        source_text=cl.source_text,
                        page_number=cl.page_number,
                        paragraph_ref=cl.paragraph_ref,
                    )
                )
                n += 1
        return n

    def list_clauses(self, document_id: str) -> list[dict[str, Any]]:
        with self._session() as s:
            rows = s.scalars(
                select(PolicyClause)
                .where(PolicyClause.document_id == document_id)
                .order_by(PolicyClause.clause_id)
            ).all()
            return [_as_dict(r) for r in rows]

    def set_clause_disposition(self, document_id: str, clause_id: str, disposition: str) -> None:
        """Set a clause's disposition (clause_id is the bare, un-prefixed id)."""
        with self._session() as s:
            row = s.get(PolicyClause, f"{document_id}:{clause_id}")
            if row is not None:
                row.disposition = disposition

    # -- candidate rules ------------------------------------------------------

    def replace_candidate_rules(
        self, document_id: str, candidates: Iterable[Any]
    ) -> int:
        with self._session() as s:
            s.execute(
                delete(CandidateRuleRow).where(
                    CandidateRuleRow.document_id == document_id
                )
            )
            n = 0
            for cand in candidates:
                s.add(
                    CandidateRuleRow(
                        candidate_rule_id=_uuid(),
                        document_id=document_id,
                        clause_id=f"{document_id}:{cand.source_clause_id}"
                        if cand.source_clause_id
                        else None,
                        rule_key=cand.rule_key,
                        rule_type=cand.rule_type,
                        rule_json=cand.model_dump_json(),
                        confidence=cand.confidence,
                        review_status=cand.review_status,
                    )
                )
                n += 1
        return n

    def list_candidate_rules(
        self, document_id: Optional[str] = None
    ) -> list[dict[str, Any]]:
        with self._session() as s:
            stmt = select(CandidateRuleRow)
            if document_id:
                stmt = stmt.where(CandidateRuleRow.document_id == document_id)
            stmt = stmt.order_by(CandidateRuleRow.created_at)
            return [self._row_with_rule(r) for r in s.scalars(stmt).all()]

    def get_candidate_rule(self, candidate_rule_id: str) -> dict[str, Any]:
        with self._session() as s:
            row = s.get(CandidateRuleRow, candidate_rule_id)
            if row is None:
                raise KeyError(f"candidate rule not found: {candidate_rule_id}")
            return self._row_with_rule(row)

    def set_review_status(
        self,
        candidate_rule_id: str,
        status: str,
        *,
        notes: Optional[str] = None,
        rule_json: Optional[str] = None,
    ) -> dict[str, Any]:
        with self._session() as s:
            row = s.get(CandidateRuleRow, candidate_rule_id)
            if row is None:
                raise KeyError(f"candidate rule not found: {candidate_rule_id}")
            row.review_status = status
            row.reviewer_notes = notes
            if rule_json is not None:
                row.rule_json = rule_json
                row.rule_key = json.loads(rule_json).get("rule_key", row.rule_key)
            row.updated_at = now_iso()
            return self._row_with_rule(row)

    @staticmethod
    def _row_with_rule(row: CandidateRuleRow) -> dict[str, Any]:
        d = _as_dict(row)
        d["rule"] = json.loads(d["rule_json"])
        return d

    # -- approved rules / skill versions --------------------------------------

    def add_approved_rule(
        self,
        *,
        rule_key: str,
        domain: str,
        rule_type: str,
        rule_json: str,
        source_document_id: str,
        source_clause_id: Optional[str],
        version: str,
        effective_from: Optional[str],
        approved_by: Optional[str],
        approved_at: Optional[str],
    ) -> str:
        pid = _uuid()
        with self._session() as s:
            s.add(
                ApprovedPolicyRule(
                    policy_rule_id=pid,
                    rule_key=rule_key,
                    domain=domain,
                    rule_type=rule_type,
                    rule_json=rule_json,
                    source_document_id=source_document_id,
                    source_clause_id=source_clause_id,
                    version=version,
                    effective_from=effective_from,
                    status="active",
                    approved_by=approved_by,
                    approved_at=approved_at,
                )
            )
        return pid

    def add_skill_version(
        self,
        *,
        skill_id: str,
        version: str,
        domain: str,
        status: str,
        artifact_json: str,
        approved_by: Optional[str] = None,
        approved_at: Optional[str] = None,
    ) -> str:
        sid = _uuid()
        with self._session() as s:
            s.add(
                SkillVersion(
                    skill_version_id=sid,
                    skill_id=skill_id,
                    version=version,
                    domain=domain,
                    status=status,
                    artifact_json=artifact_json,
                    approved_by=approved_by,
                    approved_at=approved_at,
                )
            )
        return sid

    # -- extraction runs / profiles / atoms -----------------------------------

    def add_extraction_run(
        self,
        document_id: str,
        *,
        model_name: Optional[str] = None,
        domain_pack: Optional[str] = None,
    ) -> str:
        rid = _uuid()
        with self._session() as s:
            s.add(ExtractionRun(
                extraction_run_id=rid, document_id=document_id, status="running",
                model_name=model_name, domain_pack=domain_pack,
            ))
        return rid

    def complete_extraction_run(
        self, run_id: str, *, status: str = "completed", error: Optional[str] = None
    ) -> None:
        with self._session() as s:
            row = s.get(ExtractionRun, run_id)
            if row is not None:
                row.status = status
                row.completed_at = now_iso()
                row.error_message = error

    def save_profile(self, document_id: str, profile: dict) -> str:
        """Replace the document's profile with the latest one."""
        pid = _uuid()
        with self._session() as s:
            s.execute(
                delete(DocumentProfileRow).where(
                    DocumentProfileRow.document_id == document_id
                )
            )
            s.add(DocumentProfileRow(
                profile_id=pid, document_id=document_id, profile_json=profile
            ))
        return pid

    def get_profile(self, document_id: str) -> Optional[dict]:
        with self._session() as s:
            row = s.scalars(
                select(DocumentProfileRow).where(
                    DocumentProfileRow.document_id == document_id
                )
            ).first()
            return row.profile_json if row else None

    def replace_atoms(
        self, document_id: str, atoms: Iterable[Any], *, run_id: Optional[str] = None
    ) -> int:
        """Replace a document's policy atoms (atoms are PolicyAtom models)."""
        with self._session() as s:
            s.execute(
                delete(PolicyAtomRow).where(PolicyAtomRow.document_id == document_id)
            )
            n = 0
            for a in atoms:
                s.add(PolicyAtomRow(
                    atom_id=f"{document_id}:{a.atom_id}",
                    document_id=document_id,
                    clause_id=None,  # bare clause id kept in atom_json
                    extraction_run_id=run_id,
                    atom_type=a.atom_type,
                    atom_json=a.model_dump(),
                    confidence=a.confidence,
                ))
                n += 1
        return n

    def list_atoms(self, document_id: Optional[str] = None) -> list[dict[str, Any]]:
        with self._session() as s:
            stmt = select(PolicyAtomRow)
            if document_id:
                stmt = stmt.where(PolicyAtomRow.document_id == document_id)
            stmt = stmt.order_by(PolicyAtomRow.created_at)
            return [{**_as_dict(r), "atom": r.atom_json} for r in s.scalars(stmt).all()]

    # -- unresolved items -----------------------------------------------------

    def replace_unresolved_items(self, document_id: str, items: Iterable[Any]) -> int:
        """Replace a document's unresolved items (items are UnresolvedItem models)."""
        with self._session() as s:
            s.execute(
                delete(UnresolvedItemRow).where(
                    UnresolvedItemRow.document_id == document_id
                )
            )
            n = 0
            for it in items:
                data = it.model_dump()
                s.add(
                    UnresolvedItemRow(
                        # per-run ids (u_001) are unique only within a document.
                        unresolved_id=f"{document_id}:{it.unresolved_id}",
                        document_id=document_id,
                        clause_id=None,  # linkage kept in item_json.source
                        candidate_rule_id=None,
                        unresolved_type=it.type,
                        severity=it.severity,
                        status=it.status,
                        item_json=data,
                    )
                )
                n += 1
        return n

    def list_unresolved_items(
        self, document_id: Optional[str] = None
    ) -> list[dict[str, Any]]:
        with self._session() as s:
            stmt = select(UnresolvedItemRow)
            if document_id:
                stmt = stmt.where(UnresolvedItemRow.document_id == document_id)
            stmt = stmt.order_by(UnresolvedItemRow.created_at)
            return [self._unresolved_dict(r) for r in s.scalars(stmt).all()]

    def resolve_unresolved_item(
        self,
        unresolved_id: str,
        *,
        status: str = "resolved",
        resolved_by: Optional[str] = None,
        notes: Optional[str] = None,
    ) -> dict[str, Any]:
        with self._session() as s:
            row = s.get(UnresolvedItemRow, unresolved_id)
            if row is None:
                raise KeyError(f"unresolved item not found: {unresolved_id}")
            row.status = status
            row.resolved_by = resolved_by
            row.resolved_at = now_iso()
            row.resolution_notes = notes
            data = dict(row.item_json or {})
            data["status"] = status
            row.item_json = data
            return self._unresolved_dict(row)

    @staticmethod
    def _unresolved_dict(row: UnresolvedItemRow) -> dict[str, Any]:
        d = _as_dict(row)
        d["item"] = row.item_json
        return d

    # -- skill versions -------------------------------------------------------

    def list_skill_versions(self) -> list[dict[str, Any]]:
        cols = (
            "skill_version_id",
            "skill_id",
            "version",
            "domain",
            "status",
            "approved_by",
            "approved_at",
            "created_at",
        )
        with self._session() as s:
            rows = s.scalars(
                select(SkillVersion).order_by(SkillVersion.created_at.desc())
            ).all()
            return [{c: getattr(r, c) for c in cols} for r in rows]
