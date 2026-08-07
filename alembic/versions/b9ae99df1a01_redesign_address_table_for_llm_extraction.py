"""redesign address table for LLM extraction with hash caching

Revision ID: b9ae99df1a01
Revises: 2bfb30efb2f3
Create Date: 2026-08-06 00:00:00.000000

Same redesign as tb_form_fees_draft_ai (see 2bfb30efb2f3): address scraping
switched from deterministic BeautifulSoup table/accordion parsing (one row
per form_number + filing_scenario + lockbox_name) to LLM-based extraction
of the whole "Where to File" section per form into a single JSON blob,
gated by a content hash of that section so unchanged pages are skipped on
re-scrape instead of always re-extracted.

The old normalized columns have no equivalent in the new shape, so this is
a hard cutover: existing rows are cleared rather than migrated, and the
address scraper re-populates the table on its next scrape run.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = 'b9ae99df1a01'
down_revision: Union[str, None] = '2bfb30efb2f3'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("DELETE FROM tb_form_address_draft_ai")

    op.drop_index('ix_tb_form_address_draft_ai_form_number', table_name='tb_form_address_draft_ai')
    op.drop_constraint(
        'uq_tb_form_address_draft_ai_form_scenario_lockbox',
        'tb_form_address_draft_ai',
        type_='unique',
    )

    op.drop_column('tb_form_address_draft_ai', 'filing_scenario')
    op.drop_column('tb_form_address_draft_ai', 'applies_to')
    op.drop_column('tb_form_address_draft_ai', 'lockbox_name')
    op.drop_column('tb_form_address_draft_ai', 'usps_address')
    op.drop_column('tb_form_address_draft_ai', 'courier_address')
    op.drop_column('tb_form_address_draft_ai', 'address_details')

    op.add_column('tb_form_address_draft_ai', sa.Column('extracted_data', sa.JSON(), nullable=False))
    op.add_column('tb_form_address_draft_ai', sa.Column('hash_id', sa.String(64), nullable=False))

    op.alter_column(
        'tb_form_address_draft_ai',
        'form_url',
        existing_type=sa.String(2048),
        nullable=False,
    )

    op.create_index(
        'ix_tb_form_address_draft_ai_form_number',
        'tb_form_address_draft_ai',
        ['form_number'],
        unique=False,
    )


def downgrade() -> None:
    op.execute("DELETE FROM tb_form_address_draft_ai")

    op.drop_index('ix_tb_form_address_draft_ai_form_number', table_name='tb_form_address_draft_ai')

    op.alter_column(
        'tb_form_address_draft_ai',
        'form_url',
        existing_type=sa.String(2048),
        nullable=True,
    )

    op.drop_column('tb_form_address_draft_ai', 'hash_id')
    op.drop_column('tb_form_address_draft_ai', 'extracted_data')

    op.add_column(
        'tb_form_address_draft_ai', sa.Column('filing_scenario', sa.String(150), nullable=False)
    )
    op.add_column('tb_form_address_draft_ai', sa.Column('applies_to', sa.String(200), nullable=False))
    op.add_column('tb_form_address_draft_ai', sa.Column('lockbox_name', sa.String(150), nullable=False))
    op.add_column('tb_form_address_draft_ai', sa.Column('usps_address', sa.String(1000), nullable=True))
    op.add_column(
        'tb_form_address_draft_ai', sa.Column('courier_address', sa.String(1000), nullable=True)
    )
    op.add_column('tb_form_address_draft_ai', sa.Column('address_details', sa.JSON(), nullable=False))

    op.create_unique_constraint(
        'uq_tb_form_address_draft_ai_form_scenario_lockbox',
        'tb_form_address_draft_ai',
        ['form_number', 'filing_scenario', 'lockbox_name'],
    )
    op.create_index(
        'ix_tb_form_address_draft_ai_form_number',
        'tb_form_address_draft_ai',
        ['form_number'],
        unique=False,
    )
