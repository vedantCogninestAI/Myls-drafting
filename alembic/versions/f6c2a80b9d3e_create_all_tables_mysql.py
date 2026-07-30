"""create all tables (MySQL) - squashed initial migration

Revision ID: f6c2a80b9d3e
Revises:
Create Date: 2026-07-31 00:00:00.000000

Squashes the prior Postgres-dialect migration history (see git log for
that history) into a single fresh migration for the MySQL target this
project now runs against. Creates all 6 tables directly under their
final tb_<name>_draft_ai names (see docs/database.md), with MySQL-native
column types from the start — no PostgreSQL-only constructs (JSONB,
native UUID, ON CONFLICT, etc.).
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import mysql

# revision identifiers, used by Alembic.
revision: str = 'f6c2a80b9d3e'
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'tb_cases_draft_ai',
        sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
        sa.Column('case_name', sa.String(255), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('case_name', name='uq_tb_cases_draft_ai_case_name'),
    )

    op.create_table(
        'tb_ingestion_files_draft_ai',
        sa.Column('id', sa.Uuid(as_uuid=True), nullable=False),
        sa.Column('case_id', sa.Integer(), nullable=False),
        sa.Column('filename', sa.String(255), nullable=False),
        sa.Column('doc_type', sa.String(50), nullable=True),
        sa.Column('ocr_text', mysql.LONGTEXT(), nullable=True),
        sa.Column('s3_url', sa.String(2048), nullable=True),
        sa.Column('error', sa.Text(), nullable=True),
        sa.Column('fields_extracted', sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.ForeignKeyConstraint(['case_id'], ['tb_cases_draft_ai.id']),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index(
        'ix_tb_ingestion_files_draft_ai_case_id', 'tb_ingestion_files_draft_ai', ['case_id'], unique=False
    )

    op.create_table(
        'tb_form_fields_draft_ai',
        sa.Column('id', sa.Uuid(as_uuid=True), nullable=False),
        sa.Column('ingestion_file_id', sa.Uuid(as_uuid=True), nullable=False),
        sa.Column('case_id', sa.Integer(), nullable=False),
        sa.Column('fields', sa.JSON(), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.ForeignKeyConstraint(['case_id'], ['tb_cases_draft_ai.id']),
        sa.ForeignKeyConstraint(['ingestion_file_id'], ['tb_ingestion_files_draft_ai.id']),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index(
        'ix_tb_form_fields_draft_ai_case_id', 'tb_form_fields_draft_ai', ['case_id'], unique=False
    )
    op.create_index(
        'ix_tb_form_fields_draft_ai_ingestion_file_id',
        'tb_form_fields_draft_ai',
        ['ingestion_file_id'],
        unique=False,
    )

    op.create_table(
        'tb_templates_draft_ai',
        sa.Column('id', sa.Uuid(as_uuid=True), nullable=False),
        sa.Column('process_type', sa.String(255), nullable=False),
        sa.Column('filename', sa.String(255), nullable=False),
        sa.Column('ocr_text', mysql.LONGTEXT(), nullable=False),
        sa.Column('is_active', sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index(
        'ix_tb_templates_draft_ai_process_type', 'tb_templates_draft_ai', ['process_type'], unique=False
    )

    op.create_table(
        'tb_form_fees_draft_ai',
        sa.Column('id', sa.Uuid(as_uuid=True), nullable=False),
        sa.Column('form_number', sa.String(50), nullable=False),
        sa.Column('form_title', sa.String(500), nullable=False),
        sa.Column('form_url', sa.String(2048), nullable=True),
        sa.Column('filing_category', sa.String(100), nullable=False),
        sa.Column('paper_fee', sa.Float(), nullable=True),
        sa.Column('online_fee', sa.Float(), nullable=True),
        sa.Column('fee_details', sa.JSON(), nullable=False),
        sa.Column('scraped_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint(
            'form_number', 'filing_category', name='uq_tb_form_fees_draft_ai_form_category'
        ),
    )
    op.create_index(
        'ix_tb_form_fees_draft_ai_form_number', 'tb_form_fees_draft_ai', ['form_number'], unique=False
    )

    op.create_table(
        'tb_form_address_draft_ai',
        sa.Column('id', sa.Uuid(as_uuid=True), nullable=False),
        sa.Column('form_number', sa.String(50), nullable=False),
        sa.Column('form_title', sa.String(500), nullable=False),
        sa.Column('form_url', sa.String(2048), nullable=True),
        sa.Column('filing_scenario', sa.String(150), nullable=False),
        sa.Column('applies_to', sa.String(200), nullable=False),
        sa.Column('lockbox_name', sa.String(150), nullable=False),
        sa.Column('usps_address', sa.String(1000), nullable=True),
        sa.Column('courier_address', sa.String(1000), nullable=True),
        sa.Column('address_details', sa.JSON(), nullable=False),
        sa.Column('scraped_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint(
            'form_number',
            'filing_scenario',
            'lockbox_name',
            name='uq_tb_form_address_draft_ai_form_scenario_lockbox',
        ),
    )
    op.create_index(
        'ix_tb_form_address_draft_ai_form_number', 'tb_form_address_draft_ai', ['form_number'], unique=False
    )


def downgrade() -> None:
    op.drop_table('tb_form_address_draft_ai')
    op.drop_table('tb_form_fees_draft_ai')
    op.drop_table('tb_templates_draft_ai')
    op.drop_table('tb_form_fields_draft_ai')
    op.drop_table('tb_ingestion_files_draft_ai')
    op.drop_table('tb_cases_draft_ai')
