"""add opencli fields to source

Revision ID: b7f2a1c9d3e4
Revises: 9a1b2c3d4e5f
Create Date: 2026-08-20 00:00:00.000000

"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = 'b7f2a1c9d3e4'
down_revision: str | Sequence[str] | None = '9a1b2c3d4e5f'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column('source', sa.Column('site', sa.String(length=64), nullable=True))
    op.add_column('source', sa.Column('command', sa.String(length=256), nullable=True))
    op.add_column('source', sa.Column('limit', sa.Integer(), nullable=True))
    op.add_column('source', sa.Column('args', sa.JSON(), nullable=True))
    op.add_column('source', sa.Column('profile', sa.String(length=64), nullable=True))
    op.add_column('source', sa.Column('opencli_bin', sa.String(length=256), nullable=True))


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column('source', 'opencli_bin')
    op.drop_column('source', 'profile')
    op.drop_column('source', 'args')
    op.drop_column('source', 'limit')
    op.drop_column('source', 'command')
    op.drop_column('source', 'site')
