from alembic import context
from sqlalchemy import create_engine

from app.config import get_settings
from app.models import Base

target_metadata = Base.metadata


def include_object(obj, name, type_, reflected, compare_to):
    """Leave tables the app doesn't define itself out of autogenerate.

    The job queue (procrastinate_*) creates its own tables, and spreadsheet data
    lives in the separate "sheets" schema; without this, autogenerate would
    propose dropping them.
    """
    if type_ == "table" and reflected and compare_to is None:
        return False
    if type_ == "index" and reflected and compare_to is None and (name or "").startswith(("procrastinate", "idx_procrastinate")):
        return False
    return True


def run_migrations_online() -> None:
    engine = create_engine(get_settings().database_url)
    with engine.connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata, include_object=include_object)
        with context.begin_transaction():
            context.run_migrations()


run_migrations_online()
