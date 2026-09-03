"""scope documents and published skills to a subject application

Until now skill-builder had no isolation key at all: every uploaded policy
document and every extracted rule lived in one undivided pool, so two
applications onboarding different policies interleaved in Policy Studio with
nothing to separate them (application_isolation_design.md §6b).

Only TWO columns are needed, not one per table. Of the eleven tables, ten hang
off `source_documents` by foreign key — sections, clauses, candidate rules,
approved rules, extraction runs, profiles, atoms, unresolved items and review
events all reach it through `document_id` — so they inherit the scope
transitively. `skill_versions` is the single orphan root (no FK) and needs its
own.

Deliberately NOT reusing `domain`, which already exists on these tables: that is
the name of a grounding VOCABULARY pack (skillbuilder/domain_packs/*.yaml,
default "general", currently disabled), not an application. Two applications can
share one pack and one application may need several, so folding them together
would repeat exactly the application/datasource conflation the design untangled.
Nor `datasource_id`, already on source_documents: an application HAS MANY
datasources (§7.1), so a datasource cannot identify the application.

Nullable, with no backfill. A row predating this column is UNATTRIBUTED and is
reported as such rather than being assigned to whichever application happens to
be selected — the same rule eval-engine's app_id follows. A deployment that
never sets it keeps working exactly as before (Hard Rule 9).

Revision ID: 0003_app_id
Revises: 0002_document_ddl
"""

from alembic import op
import sqlalchemy as sa

revision = "0003_app_id"
down_revision = "0002_document_ddl"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("source_documents", sa.Column("app_id", sa.String(), nullable=True))
    op.add_column("skill_versions", sa.Column("app_id", sa.String(), nullable=True))
    # Every scoped read filters on it, and the pool is document-shaped, so the
    # index earns its keep on the list endpoints.
    op.create_index("ix_source_documents_app_id", "source_documents", ["app_id"])
    op.create_index("ix_skill_versions_app_id", "skill_versions", ["app_id"])


def downgrade() -> None:
    op.drop_index("ix_skill_versions_app_id", table_name="skill_versions")
    op.drop_index("ix_source_documents_app_id", table_name="source_documents")
    op.drop_column("skill_versions", "app_id")
    op.drop_column("source_documents", "app_id")
