"""initial schema

Revision ID: 0001
Revises: 
Create Date: 2026-09-23 19:18:26.475397
"""
from collections.abc import Sequence

import pgvector.sqlalchemy
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = '0001'
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute('CREATE EXTENSION IF NOT EXISTS vector')
    op.execute('CREATE EXTENSION IF NOT EXISTS citext')
    op.create_table('connectors',
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('service', sa.String(length=40), nullable=False),
    sa.Column('auth_type', sa.String(length=20), nullable=False),
    sa.Column('base_url', sa.String(length=200), nullable=False),
    sa.Column('op_schema', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
    sa.Column('enabled', sa.Boolean(), nullable=False),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('service')
    )
    op.create_table('users',
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('email', postgresql.CITEXT(), nullable=False),
    sa.Column('password_hash', sa.String(length=100), nullable=False),
    sa.Column('full_name', sa.String(length=200), nullable=False),
    sa.Column('profile_json', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
    sa.Column('plan_tier', sa.String(length=20), nullable=False),
    sa.Column('is_active', sa.Boolean(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('email')
    )
    op.create_table('credentials',
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('user_id', sa.UUID(), nullable=False),
    sa.Column('service', sa.String(length=40), nullable=False),
    sa.Column('name', sa.String(length=40), nullable=False),
    sa.Column('kind', sa.Enum('api_key', 'oauth_token', 'cookies', 'password', name='credential_kind', native_enum=False, length=32), nullable=False),
    sa.Column('ciphertext', sa.LargeBinary(), nullable=False),
    sa.Column('nonce', sa.LargeBinary(), nullable=False),
    sa.Column('tag', sa.LargeBinary(), nullable=False),
    sa.Column('wrapped_dek', sa.LargeBinary(), nullable=False),
    sa.Column('dek_nonce', sa.LargeBinary(), nullable=False),
    sa.Column('key_version', sa.Integer(), nullable=False),
    sa.Column('hint', sa.String(length=32), nullable=False),
    sa.Column('expires_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('user_id', 'service', 'name')
    )
    op.create_index(op.f('ix_credentials_user_id'), 'credentials', ['user_id'], unique=False)
    op.create_table('documents',
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('user_id', sa.UUID(), nullable=False),
    sa.Column('doc_type', sa.String(length=20), nullable=False),
    sa.Column('filename', sa.String(length=255), nullable=False),
    sa.Column('storage_path', sa.String(length=500), nullable=False),
    sa.Column('sha256', sa.String(length=64), nullable=False),
    sa.Column('raw_text', sa.Text(), nullable=False),
    sa.Column('parse_status', sa.Enum('PENDING', 'PARSED', 'EMBEDDED', 'FAILED', name='parse_status', native_enum=False, length=32), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_documents_user_id'), 'documents', ['user_id'], unique=False)
    op.create_table('workflow_templates',
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('user_id', sa.UUID(), nullable=False),
    sa.Column('name', sa.String(length=120), nullable=False),
    sa.Column('prompt', sa.Text(), nullable=False),
    sa.Column('plan_json', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    sa.Column('schedule_cron', sa.String(length=64), nullable=True),
    sa.Column('enabled', sa.Boolean(), nullable=False),
    sa.Column('last_run_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_workflow_templates_user_id'), 'workflow_templates', ['user_id'], unique=False)
    op.create_table('doc_chunks',
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('document_id', sa.UUID(), nullable=False),
    sa.Column('chunk_index', sa.Integer(), nullable=False),
    sa.Column('content', sa.Text(), nullable=False),
    sa.Column('embedding', pgvector.sqlalchemy.vector.VECTOR(dim=768), nullable=False),
    sa.ForeignKeyConstraint(['document_id'], ['documents.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_doc_chunks_document_id'), 'doc_chunks', ['document_id'], unique=False)
    op.create_index('ix_doc_chunks_embedding_hnsw', 'doc_chunks', ['embedding'], unique=False, postgresql_using='hnsw', postgresql_ops={'embedding': 'vector_cosine_ops'})
    op.create_table('tasks',
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('user_id', sa.UUID(), nullable=False),
    sa.Column('prompt', sa.Text(), nullable=False),
    sa.Column('status', sa.Enum('QUEUED', 'PLANNING', 'RUNNING', 'AWAITING_APPROVAL', 'COMPLETED', 'FAILED', 'CANCELLED', name='task_status', native_enum=False, length=32), nullable=False),
    sa.Column('plan_json', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    sa.Column('thread_id', sa.String(length=64), nullable=False),
    sa.Column('config_json', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
    sa.Column('summary', sa.Text(), nullable=False),
    sa.Column('error_label', sa.String(length=32), nullable=True),
    sa.Column('error_message', sa.Text(), nullable=True),
    sa.Column('cancel_requested', sa.Boolean(), nullable=False),
    sa.Column('llm_calls', sa.Integer(), nullable=False),
    sa.Column('tokens_used', sa.Integer(), nullable=False),
    sa.Column('template_id', sa.UUID(), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('started_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('ended_at', sa.DateTime(timezone=True), nullable=True),
    sa.ForeignKeyConstraint(['template_id'], ['workflow_templates.id'], ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('thread_id')
    )
    op.create_index(op.f('ix_tasks_created_at'), 'tasks', ['created_at'], unique=False)
    op.create_index(op.f('ix_tasks_status'), 'tasks', ['status'], unique=False)
    op.create_index(op.f('ix_tasks_user_id'), 'tasks', ['user_id'], unique=False)
    op.create_table('approvals',
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('task_id', sa.UUID(), nullable=False),
    sa.Column('step_key', sa.String(length=32), nullable=False),
    sa.Column('payload_json', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
    sa.Column('screenshot_uri', sa.String(length=500), nullable=True),
    sa.Column('decision', sa.Enum('pending', 'approved', 'rejected', name='decision', native_enum=False, length=32), nullable=False),
    sa.Column('edited_fields_json', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('decided_at', sa.DateTime(timezone=True), nullable=True),
    sa.ForeignKeyConstraint(['task_id'], ['tasks.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_approvals_task_id'), 'approvals', ['task_id'], unique=False)
    op.create_index('uq_approvals_one_pending_per_task', 'approvals', ['task_id'], unique=True, postgresql_where=sa.text("decision = 'pending'"))
    op.create_table('artifacts',
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('task_id', sa.UUID(), nullable=False),
    sa.Column('step_key', sa.String(length=32), nullable=True),
    sa.Column('kind', sa.String(length=16), nullable=False),
    sa.Column('uri', sa.String(length=500), nullable=False),
    sa.Column('bytes', sa.Integer(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.ForeignKeyConstraint(['task_id'], ['tasks.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('uri')
    )
    op.create_index(op.f('ix_artifacts_task_id'), 'artifacts', ['task_id'], unique=False)
    op.create_table('audit_log',
    sa.Column('id', sa.BigInteger(), autoincrement=True, nullable=False),
    sa.Column('user_id', sa.UUID(), nullable=True),
    sa.Column('task_id', sa.UUID(), nullable=True),
    sa.Column('action', sa.String(length=64), nullable=False),
    sa.Column('resource', sa.String(length=200), nullable=False),
    sa.Column('meta_json', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
    sa.Column('at', sa.DateTime(timezone=True), nullable=False),
    sa.ForeignKeyConstraint(['task_id'], ['tasks.id'], ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='SET NULL'),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_audit_log_at'), 'audit_log', ['at'], unique=False)
    op.create_index(op.f('ix_audit_log_user_id'), 'audit_log', ['user_id'], unique=False)
    op.create_table('eval_runs',
    sa.Column('id', sa.BigInteger(), autoincrement=True, nullable=False),
    sa.Column('suite', sa.String(length=40), nullable=False),
    sa.Column('case_id', sa.String(length=64), nullable=False),
    sa.Column('condition', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
    sa.Column('repeat_index', sa.Integer(), nullable=False),
    sa.Column('task_id', sa.UUID(), nullable=True),
    sa.Column('success', sa.Boolean(), nullable=False),
    sa.Column('failure_label', sa.String(length=32), nullable=True),
    sa.Column('steps', sa.Integer(), nullable=False),
    sa.Column('hitl_count', sa.Integer(), nullable=False),
    sa.Column('llm_calls', sa.Integer(), nullable=False),
    sa.Column('tokens_used', sa.Integer(), nullable=False),
    sa.Column('wall_ms', sa.Integer(), nullable=False),
    sa.Column('notes', sa.Text(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.ForeignKeyConstraint(['task_id'], ['tasks.id'], ondelete='SET NULL'),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_table('task_events',
    sa.Column('id', sa.BigInteger(), autoincrement=True, nullable=False),
    sa.Column('task_id', sa.UUID(), nullable=False),
    sa.Column('seq', sa.Integer(), nullable=False),
    sa.Column('event', sa.String(length=32), nullable=False),
    sa.Column('data', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
    sa.Column('ts', sa.DateTime(timezone=True), nullable=False),
    sa.ForeignKeyConstraint(['task_id'], ['tasks.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('task_id', 'seq')
    )
    op.create_index(op.f('ix_task_events_task_id'), 'task_events', ['task_id'], unique=False)
    op.create_table('task_steps',
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('task_id', sa.UUID(), nullable=False),
    sa.Column('step_key', sa.String(length=32), nullable=False),
    sa.Column('description', sa.Text(), nullable=False),
    sa.Column('agent_kind', sa.String(length=16), nullable=False),
    sa.Column('tool', sa.String(length=64), nullable=False),
    sa.Column('depends_on', sa.ARRAY(sa.String(length=32)), nullable=False),
    sa.Column('risk_level', sa.String(length=8), nullable=False),
    sa.Column('status', sa.Enum('PENDING', 'RUNNING', 'PAUSED', 'DONE', 'FAILED', 'SKIPPED', name='step_state', native_enum=False, length=32), nullable=False),
    sa.Column('output_json', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    sa.Column('error_json', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    sa.Column('attempts', sa.Integer(), nullable=False),
    sa.Column('latency_ms', sa.Integer(), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.ForeignKeyConstraint(['task_id'], ['tasks.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('task_id', 'step_key')
    )
    op.create_index(op.f('ix_task_steps_task_id'), 'task_steps', ['task_id'], unique=False)


def downgrade() -> None:
    op.drop_index(op.f('ix_task_steps_task_id'), table_name='task_steps')
    op.drop_table('task_steps')
    op.drop_index(op.f('ix_task_events_task_id'), table_name='task_events')
    op.drop_table('task_events')
    op.drop_table('eval_runs')
    op.drop_index(op.f('ix_audit_log_user_id'), table_name='audit_log')
    op.drop_index(op.f('ix_audit_log_at'), table_name='audit_log')
    op.drop_table('audit_log')
    op.drop_index(op.f('ix_artifacts_task_id'), table_name='artifacts')
    op.drop_table('artifacts')
    op.drop_index('uq_approvals_one_pending_per_task', table_name='approvals', postgresql_where=sa.text("decision = 'pending'"))
    op.drop_index(op.f('ix_approvals_task_id'), table_name='approvals')
    op.drop_table('approvals')
    op.drop_index(op.f('ix_tasks_user_id'), table_name='tasks')
    op.drop_index(op.f('ix_tasks_status'), table_name='tasks')
    op.drop_index(op.f('ix_tasks_created_at'), table_name='tasks')
    op.drop_table('tasks')
    op.drop_index('ix_doc_chunks_embedding_hnsw', table_name='doc_chunks', postgresql_using='hnsw', postgresql_ops={'embedding': 'vector_cosine_ops'})
    op.drop_index(op.f('ix_doc_chunks_document_id'), table_name='doc_chunks')
    op.drop_table('doc_chunks')
    op.drop_index(op.f('ix_workflow_templates_user_id'), table_name='workflow_templates')
    op.drop_table('workflow_templates')
    op.drop_index(op.f('ix_documents_user_id'), table_name='documents')
    op.drop_table('documents')
    op.drop_index(op.f('ix_credentials_user_id'), table_name='credentials')
    op.drop_table('credentials')
    op.drop_table('users')
    op.drop_table('connectors')
