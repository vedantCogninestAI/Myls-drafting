"""create draft sessions table

Revision ID: e7c4a92f5d31
Revises: b9ae99df1a01
Create Date: 2026-08-10 00:00:00.000000

Adds tb_draft_sessions_draft_ai to replace LangGraph's interrupt()/
checkpointer-based HITL for the draft feature with a plain sessions
table — one row per /generate call, id doubles as the session handle.
See docs/future_work.md for the full design.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import mysql

# revision identifiers, used by Alembic.
revision: str = 'e7c4a92f5d31'
down_revision: Union[str, None] = 'b9ae99df1a01'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'tb_draft_sessions_draft_ai',
        sa.Column('id', sa.Uuid(as_uuid=True), nullable=False),
        sa.Column('case_id', sa.Integer(), nullable=False),
        sa.Column('process_type', sa.String(100), nullable=False),
        sa.Column('initial_draft', mysql.LONGTEXT(), nullable=False),
        sa.Column('current_draft', mysql.LONGTEXT(), nullable=False),
        sa.Column('revision_count', sa.Integer(), nullable=False, server_default=sa.text('0')),
        sa.Column('filing_fee_data', mysql.LONGTEXT(), nullable=True),
        sa.Column('filing_address_data', mysql.LONGTEXT(), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column(
            'updated_at',
            sa.DateTime(timezone=True),
            server_default=sa.text('now()'),
            server_onupdate=sa.text('now()'),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(['case_id'], ['tb_cases_draft_ai.id']),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index(
        'ix_tb_draft_sessions_draft_ai_case_id', 'tb_draft_sessions_draft_ai', ['case_id'], unique=False
    )


def downgrade() -> None:
    op.drop_table('tb_draft_sessions_draft_ai')
