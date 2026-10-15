"""initial schema

Revision ID: 0001
Revises: 
Create Date: 2026-10-09 04:42:00.761490
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = '0001'
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table('api_key_configs',
    sa.Column('service', sa.String(length=50), nullable=False, comment="'qwen' or 'github'"),
    sa.Column('is_configured', sa.Boolean(), nullable=True),
    sa.Column('last_validated', sa.DateTime(timezone=True), nullable=True),
    sa.Column('is_valid', sa.Boolean(), nullable=True),
    sa.Column('validation_error', sa.String(length=500), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=True),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=True),
    sa.PrimaryKeyConstraint('service')
    )
    op.create_table('encrypted_secrets',
    sa.Column('name', sa.String(length=100), nullable=False),
    sa.Column('value', sa.Text(), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=True),
    sa.PrimaryKeyConstraint('name')
    )
    op.create_table('issue_records',
    sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
    sa.Column('issue_number', sa.Integer(), nullable=False),
    sa.Column('repository', sa.String(length=200), nullable=False),
    sa.Column('title', sa.String(length=500), nullable=False),
    sa.Column('body', sa.Text(), nullable=True),
    sa.Column('author', sa.String(length=100), nullable=True),
    sa.Column('labels', sa.JSON(), nullable=True),
    sa.Column('status', sa.Enum('RECEIVED', 'ANALYZING', 'CLASSIFIED', 'NEEDS_CLARIFICATION', 'GENERATING', 'LOW_CONFIDENCE', 'PR_CREATED', 'MERGE_CONFLICT', 'APPROVED', 'MERGED', 'CLOSED', 'FAILED', 'OUT_OF_SCOPE', name='issuestatus'), nullable=True),
    sa.Column('classification', sa.String(length=50), nullable=True),
    sa.Column('classification_confidence', sa.Float(), nullable=True),
    sa.Column('summary', sa.Text(), nullable=True),
    sa.Column('solution_plan', sa.Text(), nullable=True),
    sa.Column('generated_diff', sa.Text(), nullable=True),
    sa.Column('language', sa.String(length=50), nullable=True),
    sa.Column('modified_files', sa.JSON(), nullable=True),
    sa.Column('pr_number', sa.Integer(), nullable=True),
    sa.Column('pr_url', sa.String(length=500), nullable=True),
    sa.Column('branch_name', sa.String(length=200), nullable=True),
    sa.Column('approved_by', sa.String(length=100), nullable=True),
    sa.Column('approved_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=True),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=True),
    sa.Column('processed_at', sa.DateTime(timezone=True), nullable=True),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_table('activity_logs',
    sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
    sa.Column('issue_id', sa.Integer(), nullable=True),
    sa.Column('action_type', sa.String(length=50), nullable=False, comment='webhook_received | classification | code_generation | pr_created | approval_granted | error | clarification_requested | issue_closed'),
    sa.Column('description', sa.Text(), nullable=False),
    sa.Column('log_metadata', sa.JSON(), nullable=True),
    sa.Column('is_success', sa.Boolean(), nullable=True),
    sa.Column('level', sa.String(length=20), nullable=True),
    sa.Column('latency_ms', sa.Integer(), nullable=True),
    sa.Column('tokens_used', sa.Integer(), nullable=True),
    sa.Column('timestamp', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=True),
    sa.ForeignKeyConstraint(['issue_id'], ['issue_records.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id')
    )
    with op.batch_alter_table('activity_logs', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_activity_logs_timestamp'), ['timestamp'], unique=False)



def downgrade() -> None:
    with op.batch_alter_table('activity_logs', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_activity_logs_timestamp'))

    op.drop_table('activity_logs')
    op.drop_table('issue_records')
    op.drop_table('encrypted_secrets')
    op.drop_table('api_key_configs')
    # Postgres keeps enum types after DROP TABLE; remove it so re-upgrade works.
    sa.Enum(name='issuestatus').drop(op.get_bind(), checkfirst=True)
