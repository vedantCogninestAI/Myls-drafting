"""add fields_extracted to ingestion_files

Revision ID: f4a9c1d83b6e
Revises: e7b2d4a91c35
Create Date: 2026-07-08 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = 'f4a9c1d83b6e'
down_revision: Union[str, None] = 'e7b2d4a91c35'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        'ingestion_files',
        sa.Column('fields_extracted', sa.Boolean(), nullable=False, server_default=sa.false()),
    )


def downgrade() -> None:
    op.drop_column('ingestion_files', 'fields_extracted')
