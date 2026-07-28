"""drop process_type, add unique case_name to cases

Revision ID: b7a3d9f0e412
Revises: 818195be7ff9
Create Date: 2026-07-28 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = 'b7a3d9f0e412'
down_revision: Union[str, None] = '818195be7ff9'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('cases', sa.Column('case_name', sa.String(), nullable=False))
    op.create_unique_constraint('uq_cases_case_name', 'cases', ['case_name'])
    op.drop_column('cases', 'process_type')


def downgrade() -> None:
    op.add_column('cases', sa.Column('process_type', sa.String(), nullable=True))
    op.drop_constraint('uq_cases_case_name', 'cases', type_='unique')
    op.drop_column('cases', 'case_name')
