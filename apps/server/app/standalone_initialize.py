"""Initialize a new standalone SQLite database without adopting existing schemas."""

import json
import sqlite3
from contextlib import closing

from alembic import command
from alembic.config import Config

from app.core.config import PROJECT_ROOT, settings
from app.services.builtin_style_media_service import install_files


def initialize() -> None:
    if settings.runtime_execution_location != "local" or settings.sqlite_file is None:
        raise RuntimeError("Standalone initialization requires local SQLite execution")
    file = settings.sqlite_file
    if file.is_file():
        with closing(sqlite3.connect(file.resolve().as_uri() + "?mode=ro", uri=True)) as connection:
            tables = {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")}
            if tables:
                if "standalone_alembic_version" not in tables:
                    raise RuntimeError("Existing database has no standalone revision; refusing to change it")
                versions = [row[0] for row in connection.execute("SELECT version_num FROM standalone_alembic_version")]
                if versions != ["ds_r3_0001"]:
                    raise RuntimeError("Unsupported standalone database revision; refusing to change it. R3 requires explicit test-data reset; startup never resets an old database")
                expected = json.loads((PROJECT_ROOT / "apps/server/migrations/schema-tables.json").read_text(encoding="utf-8"))
                if tables != set(expected) | {"standalone_alembic_version"}:
                    raise RuntimeError("Standalone schema table set does not match its revision")
                for table, columns in expected.items():
                    quoted = table.replace('"', '""')
                    actual = [row[1] for row in connection.execute(f'PRAGMA table_info("{quoted}")')]
                    if actual != columns:
                        raise RuntimeError("Standalone schema columns do not match their revision")
                install_files(settings.storage_path)
                return
    file.parent.mkdir(parents=True, exist_ok=True)
    config = Config(str(PROJECT_ROOT / "apps/server/alembic.ini"))
    config.set_main_option("script_location", str(PROJECT_ROOT / "apps/server/migrations"))
    command.upgrade(config, "head")
    install_files(settings.storage_path)


if __name__ == "__main__":
    initialize()
    print("Standalone schema and builtin images initialized; configured models remain user-managed.")
