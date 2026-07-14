"""rename form_fees_address to form_fees, form_filing_addresses to form_address

Revision ID: c3f8a2b6e910
Revises: a1e6c9d2f4b7
Create Date: 2026-07-08 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op

# revision identifiers, used by Alembic.
revision: str = 'c3f8a2b6e910'
down_revision: Union[str, None] = 'a1e6c9d2f4b7'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.rename_table('form_fees_address', 'form_fees')
    op.execute('ALTER INDEX ix_form_fees_address_form_number RENAME TO ix_form_fees_form_number')
    op.execute(
        'ALTER TABLE form_fees RENAME CONSTRAINT uq_form_fees_address_form_category '
        'TO uq_form_fees_form_category'
    )

    op.rename_table('form_filing_addresses', 'form_address')
    op.execute('ALTER INDEX ix_form_filing_addresses_form_number RENAME TO ix_form_address_form_number')
    op.execute(
        'ALTER TABLE form_address RENAME CONSTRAINT uq_form_filing_addresses_form_scenario_lockbox '
        'TO uq_form_address_form_scenario_lockbox'
    )


def downgrade() -> None:
    op.execute(
        'ALTER TABLE form_address RENAME CONSTRAINT uq_form_address_form_scenario_lockbox '
        'TO uq_form_filing_addresses_form_scenario_lockbox'
    )
    op.execute('ALTER INDEX ix_form_address_form_number RENAME TO ix_form_filing_addresses_form_number')
    op.rename_table('form_address', 'form_filing_addresses')

    op.execute(
        'ALTER TABLE form_fees RENAME CONSTRAINT uq_form_fees_form_category '
        'TO uq_form_fees_address_form_category'
    )
    op.execute('ALTER INDEX ix_form_fees_form_number RENAME TO ix_form_fees_address_form_number')
    op.rename_table('form_fees', 'form_fees_address')
