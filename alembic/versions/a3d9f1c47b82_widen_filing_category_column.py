"""widen filing_category column

Revision ID: a3d9f1c47b82
Revises: f6c2a80b9d3e
Create Date: 2026-07-31 00:00:00.000000

tb_form_fees_draft_ai.filing_category was String(100), too narrow for
some USCIS category text (e.g. the IntegrityFund fee page), causing
MySQL to silently truncate it on insert. Widened to String(500) to
match form_title's width on the same table.

Capped at 500, not wider: filing_category is part of the
uq_tb_form_fees_draft_ai_form_category unique index together with
form_number (String(50)). InnoDB's max key length is 3072 bytes, and
this DB uses utf8mb4 (4 bytes/char) — form_number(50) + filing_category(N)
must satisfy 200 + 4*N <= 3072, i.e. N <= 718. String(1000) was tried
first and failed with "Specified key was too long; max key length is
3072 bytes".
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = 'a3d9f1c47b82'
down_revision: Union[str, None] = 'f6c2a80b9d3e'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.alter_column(
        'tb_form_fees_draft_ai',
        'filing_category',
        existing_type=sa.String(100),
        type_=sa.String(500),
        existing_nullable=False,
    )


def downgrade() -> None:
    op.alter_column(
        'tb_form_fees_draft_ai',
        'filing_category',
        existing_type=sa.String(500),
        type_=sa.String(100),
        existing_nullable=False,
    )
