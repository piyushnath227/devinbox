"""Durable inbox/outbox and issue uniqueness.

Existing duplicate issues deliberately block migration; operators must reconcile
PRs and reassign activity logs before removing duplicates. No data is deleted.
"""
from alembic import op
import sqlalchemy as sa

revision = '0002'
down_revision = '0001'
branch_labels = None
depends_on = None


def upgrade():
    bind = op.get_bind()
    duplicate = bind.execute(sa.text('SELECT repository, issue_number FROM issue_records GROUP BY repository, issue_number HAVING count(*) > 1 LIMIT 1')).first()
    if duplicate:
        raise RuntimeError('Duplicate issue records must be reconciled before migration 0002')
    with op.batch_alter_table('issue_records') as batch:
        batch.add_column(sa.Column('checkpoint', sa.JSON(), nullable=True))
        batch.create_unique_constraint('uq_issue_repository_number', ['repository', 'issue_number'])
        batch.create_index('ix_issue_repository_status', ['repository', 'status'])
    op.create_table('jobs',
        sa.Column('id', sa.String(36), primary_key=True),
        sa.Column('kind', sa.String(20), nullable=False),
        sa.Column('repository', sa.String(200), nullable=False),
        sa.Column('payload', sa.JSON(), nullable=False),
        sa.Column('active_key', sa.String(240), unique=True),
        sa.Column('status', sa.String(20), nullable=False),
        sa.Column('attempts', sa.Integer(), nullable=False),
        sa.Column('dispatch_token', sa.String(36)),
        sa.Column('available_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('lease_until', sa.DateTime(timezone=True)),
        sa.Column('result', sa.JSON()),
        sa.Column('error', sa.String(500)),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index('ix_jobs_dispatch', 'jobs', ['status', 'available_at'])
    op.create_table('webhook_deliveries',
        sa.Column('id', sa.String(128), primary_key=True),
        sa.Column('job_id', sa.String(36), sa.ForeignKey('jobs.id'), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_table('admission_windows', sa.Column('id', sa.String(100), primary_key=True), sa.Column('count', sa.Integer(), nullable=False))


def downgrade():
    op.drop_table('webhook_deliveries')
    op.drop_table('jobs')
    op.drop_table('admission_windows')
    with op.batch_alter_table('issue_records') as batch:
        batch.drop_column('checkpoint')
        batch.drop_index('ix_issue_repository_status')
        batch.drop_constraint('uq_issue_repository_number', type_='unique')
