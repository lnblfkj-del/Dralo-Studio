"""Standalone SQLite migration chain; never import evolving ORM metadata."""

from alembic import context
from sqlalchemy import create_engine
from sqlalchemy.engine import make_url

from app.core.config import settings

url = make_url(settings.database_url)
if settings.runtime_execution_location != "local" or url.get_backend_name() != "sqlite":
    raise RuntimeError("Standalone migrations require local SQLite execution")
url = url.set(drivername="sqlite")

if context.is_offline_mode():
    context.configure(url=url, literal_binds=True, version_table="standalone_alembic_version")
    with context.begin_transaction():
        context.run_migrations()
else:
    engine = create_engine(url)
    try:
        with engine.connect() as connection:
            context.configure(connection=connection, version_table="standalone_alembic_version")
            with context.begin_transaction():
                context.run_migrations()
    finally:
        engine.dispose()
