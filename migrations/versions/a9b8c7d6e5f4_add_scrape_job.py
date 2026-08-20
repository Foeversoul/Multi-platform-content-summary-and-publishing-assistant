"""add scrape_job and scrape_job_item

Revision ID: a9b8c7d6e5f4
Revises: 6750f55e66ca
Create Date: 2026-08-19 00:00:00.000000

"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = 'a9b8c7d6e5f4'
down_revision: str | Sequence[str] | None = '6750f55e66ca'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table('scrape_job',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('status', sa.String(length=16), nullable=False),
    sa.Column('url_count', sa.Integer(), nullable=False),
    sa.Column('succeeded_count', sa.Integer(), nullable=False),
    sa.Column('failed_count', sa.Integer(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('finished_at', sa.DateTime(timezone=True), nullable=True),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_scrape_job_created_at'), 'scrape_job', ['created_at'], unique=False)
    op.create_index(op.f('ix_scrape_job_status'), 'scrape_job', ['status'], unique=False)
    op.create_table('scrape_job_item',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('job_id', sa.Integer(), nullable=False),
    sa.Column('url', sa.String(length=2048), nullable=False),
    sa.Column('status', sa.String(length=16), nullable=False),
    sa.Column('error_code', sa.String(length=64), nullable=True),
    sa.Column('error_message', sa.String(length=1000), nullable=True),
    sa.Column('article_id', sa.Integer(), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('finished_at', sa.DateTime(timezone=True), nullable=True),
    sa.ForeignKeyConstraint(['article_id'], ['article.id'], ),
    sa.ForeignKeyConstraint(['job_id'], ['scrape_job.id'], ),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_scrape_job_item_job_id'), 'scrape_job_item', ['job_id'], unique=False)
    op.create_index(op.f('ix_scrape_job_item_status'), 'scrape_job_item', ['status'], unique=False)
    op.add_column('event_log', sa.Column('error', sa.Text(), nullable=True))


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index(op.f('ix_scrape_job_item_status'), table_name='scrape_job_item')
    op.drop_index(op.f('ix_scrape_job_item_job_id'), table_name='scrape_job_item')
    op.drop_table('scrape_job_item')
    op.drop_index(op.f('ix_scrape_job_status'), table_name='scrape_job')
    op.drop_index(op.f('ix_scrape_job_created_at'), table_name='scrape_job')
    op.drop_table('scrape_job')
    op.drop_column('event_log', 'error')
