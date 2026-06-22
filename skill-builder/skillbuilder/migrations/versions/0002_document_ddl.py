"""add ddl + datasource_id to source_documents

Capture an optional datasource schema with each uploaded document so a default
column-only domain pack can be derived from it (see skillbuilder/schema_pack.py).

Revision ID: 0002_document_ddl
Revises: 0001_initial
"""

from alembic import op
import sqlalchemy as sa

revision = "0002_document_ddl"
down_revision = "0001_initial"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("source_documents", sa.Column("ddl", sa.Text(), nullable=True))
    op.add_column(
        "source_documents", sa.Column("datasource_id", sa.String(), nullable=True)
    )


def downgrade() -> None:
    op.drop_column("source_documents", "datasource_id")
    op.drop_column("source_documents", "ddl")
