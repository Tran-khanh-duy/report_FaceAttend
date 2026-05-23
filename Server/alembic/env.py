import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

from logging.config import fileConfig
from sqlalchemy import create_engine, pool
from alembic import context
from config import db_config

# config của alembic
config = context.config

# Đọc log config
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = None

def _make_engine():
    """
    Tạo engine trực tiếp từ db_config (tránh vấn đề URL-encoding với
    ký tự đặc biệt trong password khi dùng set_main_option).
    """
    return create_engine(
        "mysql+mysqlconnector://",
        creator=lambda: __import__('mysql.connector', fromlist=['connector']).connect(
            host=db_config.host,
            port=db_config.port,
            database=db_config.database,
            user=db_config.username,
            password=db_config.password,
        ),
        poolclass=pool.NullPool,
    )

def run_migrations_offline() -> None:
    """Run migrations in 'offline' mode (dùng URL string thô)."""
    # Offline mode: encode % thành %% để configparser không lỗi
    from urllib.parse import quote_plus
    _u = quote_plus(db_config.username)
    _p = quote_plus(db_config.password).replace('%', '%%')
    url = (
        f"mysql+mysqlconnector://{_u}:{_p}"
        f"@{db_config.host}:{db_config.port}/{db_config.database}"
    )
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()

def run_migrations_online() -> None:
    """Run migrations in 'online' mode dùng create_engine trực tiếp."""
    connectable = _make_engine()
    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
        )
        with context.begin_transaction():
            context.run_migrations()

if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
