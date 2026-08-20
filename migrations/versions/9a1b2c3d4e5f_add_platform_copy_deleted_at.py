"""add deleted_at soft-delete column to platform_copy (recycle bin)

Revision ID: 9a1b2c3d4e5f
Revises: 2f4a1c6e8b0d
Create Date: 2026-08-19 00:00:00.000000

"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = '9a1b2c3d4e5f'
down_revision: str | Sequence[str] | None = '2f4a1c6e8b0d'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Add deleted_at for recycle-bin soft delete."""
    with op.batch_alter_table("platform_copy") as batch:
        batch.add_column(sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True))
        batch.create_index("ix_platform_copy_deleted_at", ["deleted_at"])


def downgrade() -> None:
    """Drop deleted_at column."""
    with op.batch_alter_table("platform_copy") as batch:
        batch.drop_index("ix_platform_copy_deleted_at")
        batch.drop_column("deleted_at")
