from logging.config import fileConfig

from alembic import context
from app.config import get_settings
from app.models import API_TABLES, Base
from sqlalchemy import engine_from_config, pool

config = context.config
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata

settings = get_settings()
_db_url = settings.database_migration_url or settings.database_url
# ConfigParser treats "%" as interpolation, so URL-encoded secrets (e.g. "%26") must be escaped.
config.set_main_option("sqlalchemy.url", _db_url.replace("%", "%%"))


def _include_object(object, name, type_, reflected, compare_to):  # type: ignore[no-untyped-def]
    """Autogenerate only ever looks at the API's own tables (SPEC §6.1).

    The agent's tables and views (created by the baseline migration) and the externally synced v_* tables are
    invisible to autogenerate, so it can never propose dropping or altering them. Changes to agent tables are
    written by hand, additive, and reviewed against the agent's write paths (SPEC §0.5).
    """
    if type_ == "table":
        return name in API_TABLES
    return True


def run_migrations_offline() -> None:
    context.configure(
        url=config.get_main_option("sqlalchemy.url"),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        include_object=_include_object,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}), prefix="sqlalchemy.", poolclass=pool.NullPool
    )
    with connectable.connect() as connection:
        context.configure(
            connection=connection, target_metadata=target_metadata, include_object=_include_object
        )
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
