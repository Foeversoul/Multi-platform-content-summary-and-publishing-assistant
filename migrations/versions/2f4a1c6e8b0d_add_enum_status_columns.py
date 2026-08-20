"""status/verdict columns to SQLAlchemy Enum (Q2/E5)

Revision ID: 2f4a1c6e8b0d
Revises: 1787d080898a
Create Date: 2026-08-19 00:00:00.000000

"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = '2f4a1c6e8b0d'
down_revision: str | Sequence[str] | None = 'a9b8c7d6e5f4'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# 各状态列允许的取值（与 StrEnum.value 对齐），DB 层校验非法状态（Q2/E5）
_COLUMN_ENUMS = [
    ("article", "status", ["pending", "crawled", "summarized", "adapted", "reviewed", "published", "failed", "dead_letter", "rejected"]),
    ("event_log", "status", ["queued", "processed", "dead", "discarded"]),
    ("summary", "status", ["pending", "summarized", "failed"]),
    ("platform_copy", "status", ["pending", "adapted", "reviewed"]),
    ("review", "verdict", ["pending", "pass", "reject"]),
    ("publish", "status", ["pending", "published", "skipped"]),
    ("scrape_job", "status", ["pending", "validating", "crawling", "succeeded", "failed", "partial"]),
    ("scrape_job_item", "status", ["pending", "validated", "crawling", "succeeded", "failed"]),
]


def _enum_type(values: list[str], name: str) -> sa.Enum:
    return sa.Enum(*values, name=name, native_enum=False, create_constraint=True, validate_strings=True)


def upgrade() -> None:
    """Upgrade schema: replace String status columns with Enum (+ CHECK constraint)."""
    for table, column, values in _COLUMN_ENUMS:
        with op.batch_alter_table(table) as batch:
            batch.alter_column(column, type_=_enum_type(values, f"{table}_{column}"))


def downgrade() -> None:
    """Downgrade schema: back to plain String columns."""
    lengths = {
        ("article", "status"): 32,
        ("event_log", "status"): 32,
        ("summary", "status"): 32,
        ("platform_copy", "status"): 32,
        ("review", "verdict"): 16,
        ("publish", "status"): 16,
        ("scrape_job", "status"): 16,
        ("scrape_job_item", "status"): 16,
    }
    for (table, column), length in lengths.items():
        with op.batch_alter_table(table) as batch:
            batch.alter_column(column, type_=sa.String(length=length))
