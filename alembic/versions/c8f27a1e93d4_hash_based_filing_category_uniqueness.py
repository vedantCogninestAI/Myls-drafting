"""hash-based filing_category uniqueness

Revision ID: c8f27a1e93d4
Revises: a3d9f1c47b82
Create Date: 2026-07-31 00:00:00.000000

filing_category kept truncating no matter how wide the VARCHAR was made
(hit it at 100, then again at 500, on different USCIS forms) because it's
part of the uq_tb_form_fees_draft_ai_form_category unique index, and
InnoDB caps index key length at 3072 bytes — any fixed VARCHAR size is
eventually beaten by some form's longer category text.

Structural fix: decouple "the data" from "the uniqueness check".
- filing_category becomes TEXT (unbounded, never truncates again)
- new filing_category_hash column (SHA-256 hex, fixed 64 chars) is what
  the unique constraint uses instead, since a fixed-length hash always
  fits the index regardless of how long the real text is
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = 'c8f27a1e93d4'
down_revision: Union[str, None] = 'a3d9f1c47b82'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        'tb_form_fees_draft_ai', sa.Column('filing_category_hash', sa.String(64), nullable=True)
    )
    op.execute(
        "UPDATE tb_form_fees_draft_ai SET filing_category_hash = SHA2(filing_category, 256)"
    )
    op.alter_column(
        'tb_form_fees_draft_ai',
        'filing_category_hash',
        existing_type=sa.String(64),
        nullable=False,
    )

    op.drop_constraint(
        'uq_tb_form_fees_draft_ai_form_category', 'tb_form_fees_draft_ai', type_='unique'
    )
    op.create_unique_constraint(
        'uq_tb_form_fees_draft_ai_form_category',
        'tb_form_fees_draft_ai',
        ['form_number', 'filing_category_hash'],
    )

    op.alter_column(
        'tb_form_fees_draft_ai',
        'filing_category',
        existing_type=sa.String(500),
        type_=sa.Text(),
        existing_nullable=False,
    )


def downgrade() -> None:
    op.alter_column(
        'tb_form_fees_draft_ai',
        'filing_category',
        existing_type=sa.Text(),
        type_=sa.String(500),
        existing_nullable=False,
    )

    op.drop_constraint(
        'uq_tb_form_fees_draft_ai_form_category', 'tb_form_fees_draft_ai', type_='unique'
    )
    op.create_unique_constraint(
        'uq_tb_form_fees_draft_ai_form_category',
        'tb_form_fees_draft_ai',
        ['form_number', 'filing_category'],
    )

    op.drop_column('tb_form_fees_draft_ai', 'filing_category_hash')
