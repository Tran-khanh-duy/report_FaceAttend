"""embedding migration

Revision ID: 002
Revises: 001
Create Date: 2026-05-07 00:00:01.000000

"""
from alembic import op
import sqlalchemy as sa

revision = '002'
down_revision = '001'
branch_labels = None
depends_on = None

def upgrade() -> None:
    op.create_table(
        'FaceEmbeddings',
        sa.Column('embedding_id', sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column('student_id', sa.Integer(), sa.ForeignKey('hocvien.id', ondelete='CASCADE'), nullable=False),
        sa.Column('embedding_vector', sa.LargeBinary(length=8192), nullable=False),
        sa.Column('model_version', sa.String(50), server_default="buffalo_l"),
        sa.Column('created_at', sa.DateTime(), server_default=sa.func.now()),
        sa.Column('is_active', sa.Boolean(), server_default='1')
    )
    # Add index for fast student lookup
    op.create_index('ix_faceembeddings_student', 'FaceEmbeddings', ['student_id', 'is_active'])

def downgrade() -> None:
    op.drop_index('ix_faceembeddings_student', table_name='FaceEmbeddings')
    op.drop_table('FaceEmbeddings')
