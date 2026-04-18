import sys
from logging.config import fileConfig
from pathlib import Path

from alembic import context
from sqlalchemy import engine_from_config, pool

# Ajoute src/ au path pour que les imports optimizer.* fonctionnent
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from optimizer.config import settings  # noqa: E402
from optimizer.db.models import Base  # noqa: E402

config = context.config

# Surcharge l'URL depuis Settings (lit .env)
config.set_main_option("sqlalchemy.url", settings.database_url)

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata

# Table version distincte — lue depuis alembic.ini, mais on la ré-applique ici
# pour que `context.configure` l'utilise bien en mode online.
VERSION_TABLE = config.get_main_option("version_table") or "alembic_version_optimizer"


def run_migrations_offline() -> None:
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        version_table=VERSION_TABLE,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            version_table=VERSION_TABLE,
        )
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
