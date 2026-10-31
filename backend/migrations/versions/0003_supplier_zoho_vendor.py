"""supplier zoho vendor

Revision ID: 0003
Revises: 0002
Create Date: 2026-11-01
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0003"
down_revision: Union[str, None] = "0002"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("suppliers") as batch:
        batch.add_column(sa.Column("zoho_vendor_id", sa.String(length=64), nullable=True))
        batch.add_column(sa.Column("zoho_vendor_organization_id", sa.String(length=32), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("suppliers") as batch:
        batch.drop_column("zoho_vendor_organization_id")
        batch.drop_column("zoho_vendor_id")