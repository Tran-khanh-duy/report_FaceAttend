"""init core schema

Revision ID: 001
Revises: 
Create Date: 2026-05-07 00:00:00.000000

"""
from alembic import op
import sqlalchemy as sa

revision = '001'
down_revision = None
branch_labels = None
depends_on = None

def upgrade() -> None:
    # 1. Bảng Tòa Nhà
    op.create_table(
        'ToaNha',
        sa.Column('MaToa', sa.String(50), primary_key=True),
        sa.Column('TenToa', sa.String(255), nullable=False)
    )

    # 2. Bảng Lớp
    op.create_table(
        'lop',
        sa.Column('IDLop', sa.String(50), primary_key=True),
        sa.Column('TenLop', sa.String(255), nullable=False)
    )

    # 3. Bảng Phòng
    op.create_table(
        'Phong',
        sa.Column('MaPhong', sa.String(50), primary_key=True),
        sa.Column('Tang', sa.Integer(), nullable=False),
        sa.Column('MaToa', sa.String(50), sa.ForeignKey('ToaNha.MaToa')),
        sa.Column('TenPhong', sa.String(255), nullable=False)
    )

    # 4. Bảng Học Viên
    op.create_table(
        'hocvien',
        sa.Column('id', sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column('MaHV', sa.String(50), unique=True, nullable=False),
        sa.Column('HoTen', sa.String(255), nullable=False),
        sa.Column('GioiTinh', sa.String(10)),
        sa.Column('date_of_birth', sa.Date()),
        sa.Column('SoDienThoai', sa.String(20)),
        sa.Column('email', sa.String(255)),
        sa.Column('IDLop', sa.String(50), sa.ForeignKey('lop.IDLop')),
        sa.Column('building', sa.String(50)),
        sa.Column('floor', sa.String(50)),
        sa.Column('room', sa.String(50)),
        sa.Column('face_enrolled', sa.Boolean(), server_default='0'),
        sa.Column('created_at', sa.DateTime(), server_default=sa.func.now())
    )

    # 5. Bảng Camera
    op.create_table(
        'Cameras',
        sa.Column('camera_id', sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column('camera_name', sa.String(255), nullable=False),
        sa.Column('location_desc', sa.String(255)),
        sa.Column('rtsp_url', sa.String(500)),
        sa.Column('ip_address', sa.String(50)),
        sa.Column('resolution', sa.String(50), server_default='1280x720'),
        sa.Column('area_id', sa.String(100)),
        sa.Column('is_active', sa.Boolean(), server_default='1')
    )

def downgrade() -> None:
    op.drop_table('Cameras')
    op.drop_table('hocvien')
    op.drop_table('Phong')
    op.drop_table('lop')
    op.drop_table('ToaNha')
