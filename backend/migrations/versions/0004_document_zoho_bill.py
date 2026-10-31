"""document zoho bill

Revision ID: 0004
Revises: 0003
Create Date: 2026-11-01
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0004"
down_revision: Union[str, None] = "0003"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("documents", sa.Column("zoho_bill_id", sa.String(length=64), nullable=True))
    op.add_column("documents", sa.Column("zoho_bill_organization_id", sa.String(length=32), nullable=True))
    op.add_column("documents", sa.Column("zoho_sent_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("documents", sa.Column("zoho_sent_by", sa.String(length=100), nullable=True))


def downgrade() -> None:
    op.drop_column("documents", "zoho_sent_by")
    op.drop_column("documents", "zoho_sent_at")
    op.drop_column("documents", "zoho_bill_organization_id")
    op.drop_column("documents", "zoho_bill_id")