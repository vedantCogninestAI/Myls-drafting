"""redesign fee table for LLM extraction with hash caching

Revision ID: 2bfb30efb2f3
Revises: c8f27a1e93d4
Create Date: 2026-08-06 00:00:00.000000

Fee scraping switched from deterministic BeautifulSoup table parsing (one
row per form_number + filing_category, with numeric paper_fee/online_fee)
to LLM-based extraction of the whole fee section per form into a single
JSON blob, gated by a content hash of the source page so unchanged pages
are skipped on re-scrape instead of always re-extracted.

The old normalized columns have no equivalent in the new shape, so this
is a hard cutover: existing rows are cleared rather than migrated, and the
draft agent re-populates the table on its next scrape run.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = '2bfb30efb2f3'
down_revision: Union[str, None] = 'c8f27a1e93d4'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("DELETE FROM tb_form_fees_draft_ai")

    op.drop_index('ix_tb_form_fees_draft_ai_form_number', table_name='tb_form_fees_draft_ai')
    op.drop_constraint(
        'uq_tb_form_fees_draft_ai_form_category', 'tb_form_fees_draft_ai', type_='unique'
    )

    op.drop_column('tb_form_fees_draft_ai', 'form_number')
    op.drop_column('tb_form_fees_draft_ai', 'form_title')
    op.drop_column('tb_form_fees_draft_ai', 'filing_category')
    op.drop_column('tb_form_fees_draft_ai', 'filing_category_hash')
    op.drop_column('tb_form_fees_draft_ai', 'paper_fee')
    op.drop_column('tb_form_fees_draft_ai', 'online_fee')
    op.drop_column('tb_form_fees_draft_ai', 'fee_details')

    op.add_column('tb_form_fees_draft_ai', sa.Column('topic_id', sa.String(50), nullable=False))
    op.add_column('tb_form_fees_draft_ai', sa.Column('label', sa.String(500), nullable=False))
    op.add_column('tb_form_fees_draft_ai', sa.Column('extracted_data', sa.JSON(), nullable=False))
    op.add_column('tb_form_fees_draft_ai', sa.Column('hash_id', sa.String(64), nullable=False))

    op.alter_column(
        'tb_form_fees_draft_ai',
        'form_url',
        existing_type=sa.String(2048),
        nullable=False,
    )

    op.create_index(
        'ix_tb_form_fees_draft_ai_topic_id', 'tb_form_fees_draft_ai', ['topic_id'], unique=False
    )


def downgrade() -> None:
    op.execute("DELETE FROM tb_form_fees_draft_ai")

    op.drop_index('ix_tb_form_fees_draft_ai_topic_id', table_name='tb_form_fees_draft_ai')

    op.alter_column(
        'tb_form_fees_draft_ai',
        'form_url',
        existing_type=sa.String(2048),
        nullable=True,
    )

    op.drop_column('tb_form_fees_draft_ai', 'hash_id')
    op.drop_column('tb_form_fees_draft_ai', 'extracted_data')
    op.drop_column('tb_form_fees_draft_ai', 'label')
    op.drop_column('tb_form_fees_draft_ai', 'topic_id')

    op.add_column('tb_form_fees_draft_ai', sa.Column('form_number', sa.String(50), nullable=False))
    op.add_column('tb_form_fees_draft_ai', sa.Column('form_title', sa.String(500), nullable=False))
    op.add_column('tb_form_fees_draft_ai', sa.Column('filing_category', sa.Text(), nullable=False))
    op.add_column(
        'tb_form_fees_draft_ai', sa.Column('filing_category_hash', sa.String(64), nullable=False)
    )
    op.add_column('tb_form_fees_draft_ai', sa.Column('paper_fee', sa.Float(), nullable=True))
    op.add_column('tb_form_fees_draft_ai', sa.Column('online_fee', sa.Float(), nullable=True))
    op.add_column('tb_form_fees_draft_ai', sa.Column('fee_details', sa.JSON(), nullable=False))

    op.create_unique_constraint(
        'uq_tb_form_fees_draft_ai_form_category',
        'tb_form_fees_draft_ai',
        ['form_number', 'filing_category_hash'],
    )
    op.create_index(
        'ix_tb_form_fees_draft_ai_form_number', 'tb_form_fees_draft_ai', ['form_number'], unique=False
    )
