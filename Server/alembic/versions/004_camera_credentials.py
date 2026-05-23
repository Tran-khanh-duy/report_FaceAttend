"""add camera credentials and seed data

Revision ID: 004
Revises: 003
Create Date: 2026-05-07

Thêm các cột:
  - username    : tài khoản đăng nhập camera (mặc định 'admin')
  - password    : mật khẩu camera
  - floor       : tầng (INT) để hỗ trợ logic chia tầng
  - rtsp_port   : cổng RTSP (mặc định 554)
  - device_group: nhóm thiết bị / tên KTX (ví dụ 'KTX E4')

Sau đó seed 5 camera thực tế vào bảng.
"""
from alembic import op
import sqlalchemy as sa

revision = '004'
down_revision = '003'
branch_labels = None
depends_on = None


def upgrade() -> None:
    # 1. Thêm cột username
    op.add_column('Cameras',
        sa.Column('username', sa.String(100), nullable=True, server_default='admin')
    )
    # 2. Thêm cột password
    op.add_column('Cameras',
        sa.Column('password', sa.String(255), nullable=True)
    )
    # 3. Thêm cột floor (tầng)
    op.add_column('Cameras',
        sa.Column('floor', sa.Integer(), nullable=True)
    )
    # 4. Thêm cột rtsp_port
    op.add_column('Cameras',
        sa.Column('rtsp_port', sa.Integer(), nullable=True, server_default='554')
    )
    # 5. Thêm cột device_group (nhóm KTX / tên tòa nhà)
    op.add_column('Cameras',
        sa.Column('device_group', sa.String(100), nullable=True)
    )

    # 6. Seed 5 camera thực tế (chỉ INSERT nếu bảng đang rỗng)
    conn = op.get_bind()
    count = conn.execute(sa.text("SELECT COUNT(*) FROM Cameras")).scalar()
    if count == 0:
        conn.execute(sa.text("""
            INSERT INTO Cameras
                (camera_name, location_desc, ip_address, username, password,
                 rtsp_port, rtsp_url, resolution, area_id, floor, device_group, is_active)
            VALUES
                ('Tầng 1', 'KTX E4 - Tầng 1', '192.168.1.17', 'admin', 'a1234567',
                 554,
                 'rtsp://admin:a1234567@192.168.1.17:554/cam/realmonitor?channel=1&subtype=0',
                 '1280x720', 'KTX E4_1', 1, 'KTX E4', 1),

                ('Tầng 2', 'KTX E4 - Tầng 2', '192.168.1.23', 'admin', 'a1234567',
                 554,
                 'rtsp://admin:a1234567@192.168.1.23:554/cam/realmonitor?channel=1&subtype=0',
                 '1280x720', 'KTX E4_2', 2, 'KTX E4', 1),

                ('Tầng 3', 'KTX E4 - Tầng 3', '192.168.1.19', 'admin', 'a1234567',
                 554,
                 'rtsp://admin:a1234567@192.168.1.19:554/cam/realmonitor?channel=1&subtype=0',
                 '1280x720', 'KTX E4_3', 3, 'KTX E4', 1),

                ('Tầng 4', 'KTX E4 - Tầng 4', '192.168.1.20', 'admin', 'a1234567',
                 554,
                 'rtsp://admin:a1234567@192.168.1.20:554/cam/realmonitor?channel=1&subtype=0',
                 '1280x720', 'KTX E4_4', 4, 'KTX E4', 1),

                ('Tầng 5', 'KTX E4 - Tầng 5', '192.168.1.21', 'admin', 'a1234567',
                 554,
                 'rtsp://admin:a1234567@192.168.1.21:554/cam/realmonitor?channel=1&subtype=0',
                 '1280x720', 'KTX E4_5', 5, 'KTX E4', 1)
        """))


def downgrade() -> None:
    op.drop_column('Cameras', 'device_group')
    op.drop_column('Cameras', 'rtsp_port')
    op.drop_column('Cameras', 'floor')
    op.drop_column('Cameras', 'password')
    op.drop_column('Cameras', 'username')
