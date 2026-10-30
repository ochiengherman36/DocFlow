"""create tables

Revision ID: 0001
Revises:
Create Date: 2026-10-31
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0001"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

JSON_TYPE = sa.JSON().with_variant(postgresql.JSONB(), "postgresql")
BIG_ID = sa.BigInteger().with_variant(sa.Integer(), "sqlite")


def upgrade() -> None:
    op.create_table(
        "suppliers",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("name_key", sa.String(length=255), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_suppliers"),
        sa.UniqueConstraint("name_key", name="uq_suppliers_name_key"),
    )

    op.create_table(
        "documents",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("filename", sa.String(length=255), nullable=False),
        sa.Column("file_hash", sa.String(length=64), nullable=False),
        sa.Column("file_size", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=16), server_default="pending", nullable=False),
        sa.Column("current_data", JSON_TYPE, nullable=False),
        sa.Column("supplier_key", sa.String(length=255), nullable=True),
        sa.Column("invoice_number_key", sa.String(length=100), nullable=True),
        sa.Column("supplier_id", sa.Integer(), nullable=True),
        sa.Column("decided_by", sa.String(length=100), nullable=True),
        sa.Column("decided_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("reject_reason", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint("status IN ('pending', 'approved', 'rejected')", name="status_valid"),
        sa.ForeignKeyConstraint(
            ["supplier_id"], ["suppliers.id"], name="fk_documents_supplier_id_suppliers", ondelete="SET NULL"
        ),
        sa.PrimaryKeyConstraint("id", name="pk_documents"),
    )
    op.create_index("ix_documents_file_hash", "documents", ["file_hash"], unique=True)
    op.create_index("ix_documents_invoice_key", "documents", ["supplier_key", "invoice_number_key"])
    op.create_index(
        "uq_documents_approved_invoice",
        "documents",
        ["supplier_key", "invoice_number_key"],
        unique=True,
        postgresql_where=sa.text("status = 'approved'"),
        sqlite_where=sa.text("status = 'approved'"),
    )

    op.create_table(
        "extractions",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("document_id", sa.Uuid(), nullable=False),
        sa.Column("data", JSON_TYPE, nullable=False),
        sa.Column("model", sa.String(length=100), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(
            ["document_id"], ["documents.id"], name="fk_extractions_document_id_documents", ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id", name="pk_extractions"),
        sa.UniqueConstraint("document_id", name="uq_extractions_document_id"),
    )

    op.create_table(
        "audit_log",
        sa.Column("id", BIG_ID, autoincrement=True, nullable=False),
        sa.Column("document_id", sa.Uuid(), nullable=False),
        sa.Column("extraction_id", sa.Uuid(), nullable=True),
        sa.Column("action", sa.String(length=32), nullable=False),
        sa.Column("actor", sa.String(length=100), nullable=False),
        sa.Column("details", JSON_TYPE, nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(
            ["document_id"], ["documents.id"], name="fk_audit_log_document_id_documents", ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["extraction_id"], ["extractions.id"], name="fk_audit_log_extraction_id_extractions", ondelete="SET NULL"
        ),
        sa.PrimaryKeyConstraint("id", name="pk_audit_log"),
    )
    op.create_index("ix_audit_log_document_time", "audit_log", ["document_id", "created_at"])


def downgrade() -> None:
    op.drop_index("ix_audit_log_document_time", table_name="audit_log")
    op.drop_table("audit_log")
    op.drop_table("extractions")
    op.drop_index("uq_documents_approved_invoice", table_name="documents")
    op.drop_index("ix_documents_invoice_key", table_name="documents")
    op.drop_index("ix_documents_file_hash", table_name="documents")
    op.drop_table("documents")
    op.drop_table("suppliers")