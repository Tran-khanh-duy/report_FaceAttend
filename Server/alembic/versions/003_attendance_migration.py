"""attendance migration

Revision ID: 003
Revises: 002
Create Date: 2026-05-07 00:00:02.000000

"""
from alembic import op
import sqlalchemy as sa

revision = '003'
down_revision = '002'
branch_labels = None
depends_on = None

def upgrade() -> None:
    op.create_table(
        'AttendanceSessions',
        sa.Column('session_id', sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column('session_code', sa.String(100), unique=True, nullable=False),
        sa.Column('class_id', sa.String(50), sa.ForeignKey('lop.IDLop', ondelete='CASCADE')),
        sa.Column('subject_name', sa.String(255), nullable=False),
        sa.Column('session_date', sa.Date()),
        sa.Column('start_time', sa.DateTime()),
        sa.Column('end_time', sa.DateTime()),
        sa.Column('status', sa.String(50), server_default='PENDING'),
        sa.Column('present_count', sa.Integer(), server_default='0'),
        sa.Column('absent_count', sa.Integer(), server_default='0'),
        sa.Column('created_at', sa.DateTime(), server_default=sa.func.now())
    )
    
    op.create_table(
        'AttendanceRecords',
        sa.Column('record_id', sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column('session_id', sa.Integer(), sa.ForeignKey('AttendanceSessions.session_id', ondelete='CASCADE'), nullable=False),
        sa.Column('student_id', sa.Integer(), sa.ForeignKey('hocvien.id', ondelete='CASCADE'), nullable=False),
        sa.Column('check_in_time', sa.DateTime()),
        sa.Column('status', sa.String(50), server_default='ABSENT'),
        sa.Column('recognition_score', sa.Float()),
        sa.Column('snapshot_path', sa.String(500)),
        sa.Column('camera_id', sa.Integer(), sa.ForeignKey('Cameras.camera_id', ondelete='SET NULL')),
        sa.Column('created_at', sa.DateTime(), server_default=sa.func.now())
    )
    # Ràng buộc mỗi học viên chỉ được điểm danh 1 lần trong 1 session (Dùng cho lệnh UPSERT ON DUPLICATE KEY)
    op.create_unique_constraint('uq_session_student', 'AttendanceRecords', ['session_id', 'student_id'])

def downgrade() -> None:
    op.drop_constraint('uq_session_student', 'AttendanceRecords', type_='unique')
    op.drop_table('AttendanceRecords')
    op.drop_table('AttendanceSessions')
