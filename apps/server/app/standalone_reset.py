"""Prepare an explicitly authorized fresh local database, never replace the old one."""

import argparse
import hashlib
import json
import os
import secrets
import sqlite3
import subprocess
import sys
from contextlib import closing
from datetime import UTC, datetime, timedelta
from pathlib import Path

from cryptography.fernet import Fernet
from dotenv import dotenv_values
from sqlalchemy.engine import make_url

ROOT = Path(__file__).resolve().parents[3]
CONFIG_TABLES = (
    "users", "workspaces", "workspace_memberships", "providers", "provider_models",
    "execution_policies", "agent_skills", "agent_skill_versions", "ai_settings",
    "business_executor_settings", "style_categories", "style_presets",
)
EMPTY_TABLES = ("projects", "jobs", "media_files", "billing_calls", "billing_receipts",
                "worker_runtimes", "browser_sessions", "episode_planning_records", "planning_response_records")


def regular(path):
    path = Path(path).absolute()
    if any(item.is_symlink() or getattr(item, "is_junction", lambda: False)()
           for item in (path, *path.parents)):
        raise RuntimeError("Symlink or junction paths are not allowed")
    return path.resolve()


def source_configuration(env_file):
    env_file = regular(env_file)
    if not env_file.is_file():
        raise RuntimeError("A private source .env file is required")
    values = dict(dotenv_values(env_file, interpolate=False))
    if values.get("RUNTIME_EXECUTION_LOCATION", "local") != "local":
        raise RuntimeError("This tool only prepares local SQLite resets, not cloud resets")
    if any(value is None or "\0" in value or "${" in value for value in values.values()):
        raise RuntimeError("Resolve empty or interpolated dotenv entries before resetting")
    try:
        url = make_url(values["DATABASE_URL"])
        if url.drivername not in {"sqlite", "sqlite+aiosqlite"} or not url.database or url.query:
            raise ValueError()
        Fernet(values["PROVIDER_ENCRYPTION_KEY"].encode("ascii"))
    except (KeyError, ValueError, UnicodeError):
        raise RuntimeError("Explicit SQLite URL and valid provider encryption key are required") from None
    database = Path(url.database)
    # Runtime relative SQLite URLs are resolved against the deployment root.
    database = regular(database if database.is_absolute() else env_file.parent / database)
    if not database.is_file():
        raise RuntimeError("Source database does not exist")
    return env_file, values, database


def inspect(connection):
    tables = {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")}
    if "standalone_alembic_version" not in tables:
        raise RuntimeError("Unrecognized database: no published standalone revision")
    versions = [row[0] for row in connection.execute("SELECT version_num FROM standalone_alembic_version")]
    known = json.loads((ROOT / "apps/server/migrations/reset-source-tables.json").read_text(encoding="utf-8"))
    if len(versions) != 1 or versions[0] not in known:
        raise RuntimeError("Unrecognized standalone revision; source remains untouched")
    expected = known[versions[0]]
    if tables != set(expected) | {"standalone_alembic_version"}:
        raise RuntimeError("Source table set does not match its standalone revision")
    for table, columns in expected.items():
        if [row[1] for row in connection.execute(f'PRAGMA table_info("{table}")')] != columns:
            raise RuntimeError("Source columns do not match its standalone revision")
    if connection.execute("PRAGMA quick_check").fetchone()[0] != "ok":
        raise RuntimeError("Source database integrity check failed")
    if connection.execute("PRAGMA foreign_key_check").fetchone():
        raise RuntimeError("Source database contains foreign key errors")
    now = datetime.now(UTC)
    for status, heartbeat in connection.execute("SELECT status, heartbeat_at FROM worker_runtimes"):
        parsed = datetime.fromisoformat(heartbeat)
        parsed = parsed.replace(tzinfo=UTC) if parsed.tzinfo is None else parsed.astimezone(UTC)
        if status == "online" and parsed >= now - timedelta(minutes=5):
            raise RuntimeError("Recent worker heartbeat found; stop services before preparing reset")
    counts = {name: connection.execute(f'SELECT count(*) FROM "{name}"').fetchone()[0] for name in tables}
    active = connection.execute("SELECT count(*) FROM jobs WHERE status NOT IN ('succeeded','failed','cancelled')").fetchone()[0]
    return {"source_revision": versions[0], "counts": counts, "unfinished_jobs": active}


def transformed_rows(connection, values):
    cipher = Fernet(values["PROVIDER_ENCRYPTION_KEY"].encode("ascii"))
    rows = {table: [dict(row) for row in connection.execute(f'SELECT * FROM "{table}"')] for table in CONFIG_TABLES}
    # Verify the retained key before preparing a new database, never print plaintext.
    try:
        for row in rows["providers"]:
            cipher.decrypt(row["api_key_ciphertext"].encode("ascii"))
        for row in rows["ai_settings"]:
            if row["market_search_api_key_ciphertext"]:
                cipher.decrypt(row["market_search_api_key_ciphertext"].encode("ascii"))
    except Exception:
        raise RuntimeError("Retained encryption key cannot decrypt source credentials") from None
    for row in rows["users"]:
        row.update(avatar_media_id=None, session_version=row["session_version"] + 1, session_reason="r3_reset")
    for row in rows["provider_models"]:
        params = json.loads(row["default_params"])
        for key in ("_audio_verification", "speech_verified", "music_verified", "episode_planning_capability"):
            params.pop(key, None)
        row.update(default_params=json.dumps(params), video_prompt_certifications="{}",
                   effective_concurrency=row["max_concurrency"], rate_limit_hits=0, success_streak=0,
                   rate_limit_until=None, last_rate_limited_at=None)
    # Old built-in Skill snapshots are not carried into the new workflow.
    rows["agent_skills"] = [row for row in rows["agent_skills"] if not row["is_builtin"]]
    custom = {row["id"] for row in rows["agent_skills"]}
    rows["agent_skill_versions"] = [row for row in rows["agent_skill_versions"] if row["skill_id"] in custom]
    for row in rows["ai_settings"]:
        bindings = json.loads(row["agent_skill_bindings"])
        row["agent_skill_bindings"] = json.dumps({key: [ident for ident in ids if ident in custom] for key, ids in bindings.items()})
        for key in ("script_agent_skill_id", "outline_agent_skill_id"):
            if row[key] not in custom:
                row[key] = None
    for row in rows["business_executor_settings"]:
        row["skill_versions"] = "[]"
        row["parameters"] = "{}"
        row["revision"] += 1
    for row in rows["style_presets"]:
        row.update(preview_media_id=None, reference_media_id=None)
    return rows


def quote(value):
    return '"' + value.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n").replace("\r", "\\r") + '"'


def prepare(env_file, output, *, apply=False, confirmed=False, stopped=False, acknowledged=False):
    env_file, values, database = source_configuration(env_file)
    output = regular(output)
    if output.exists() or output == database or output in database.parents or output == env_file or output in env_file.parents:
        raise RuntimeError("Output must be a new directory separate from source configuration and database")
    with closing(sqlite3.connect(database.as_uri() + "?mode=ro", uri=True, timeout=5)) as source:
        source.row_factory = sqlite3.Row
        source.execute("BEGIN")
        summary = inspect(source)
        rows = transformed_rows(source, values)
        summary["retained_counts"] = {name: len(items) for name, items in rows.items()}
        summary["paid_calls"] = 0
        summary["source_modified"] = False
        summary["activated"] = False
        summary["object_storage"] = "archived only; configure a new prefix before enabling sync"
        if not apply:
            return summary
        if not confirmed or not stopped:
            raise RuntimeError("Apply requires explicit test-project reset confirmation and services-stopped attestation")
        if summary["unfinished_jobs"] and not acknowledged:
            raise RuntimeError("Unfinished jobs exist; reconcile upstream charges and explicitly acknowledge them")
        epoch = "r3-" + secrets.token_hex(16)
        output.mkdir(parents=True, mode=0o700)
        archive = output / "archive"
        archive.mkdir(mode=0o700)
        snapshot = archive / "source.sqlite3"
        # sqlite backup includes committed WAL data; copying only the main file does not.
        with closing(sqlite3.connect(snapshot)) as backup:
            source.backup(backup)
        (archive / "source.env").write_bytes(env_file.read_bytes())
        storage_config = Path(values.get("STORAGE_CONFIG_PATH", "data/storage-config.json"))
        storage_config = regular(storage_config if storage_config.is_absolute() else env_file.parent / storage_config)
        if storage_config.exists():
            (archive / "storage-config.json").write_bytes(storage_config.read_bytes())
            (archive / "storage-config.json").chmod(0o600)
        (output / "storage").mkdir()
        (output / "logs").mkdir()
        target = output / "fresh.sqlite3"
        configured = {**values, "RUNTIME_EXECUTION_LOCATION": "local", "SERVER_HOST": "127.0.0.1",
            "DATABASE_URL": "sqlite+aiosqlite:///" + target.as_posix(), "STORAGE_PATH": str(output / "storage"),
            "STORAGE_CONFIG_PATH": str(output / "storage-config.json"), "LOG_PATH": str(output / "logs"),
            "JWT_SECRET": secrets.token_urlsafe(48), "EPISODE_PLANNING_EPOCH": epoch}
        for key in ("FFMPEG_PATH", "FFPROBE_PATH", "ASR_RUNTIME_PATH", "ASR_MODEL_PATH", "EDIT_RENDER_RUNTIME_PATH"):
            value = configured.get(key)
            if value and ("/" in value or "\\" in value) and not Path(value).is_absolute():
                configured[key] = str((env_file.parent / value).resolve())
        (output / "deployment.env").write_text("\n".join(f"{key}={quote(value)}" for key, value in configured.items()) + "\n", encoding="utf-8")
        code = "import sys; sys.path.insert(0,sys.argv[1]); from app.standalone_initialize import initialize; initialize()"
        result = subprocess.run([sys.executable, "-I", "-B", "-c", code, str(ROOT / "apps/server")],
            cwd=ROOT, env={**os.environ, **configured}, capture_output=True, timeout=60)
        if result.returncode:
            raise RuntimeError("Fresh initialization failed; partial bundle is quarantined and never activated")
        with closing(sqlite3.connect(target)) as fresh:
            fresh.execute("PRAGMA foreign_keys=ON")
            fresh.execute("BEGIN")
            fresh.execute("PRAGMA defer_foreign_keys=ON")
            for table, items in rows.items():
                for row in items:
                    columns = ','.join('"' + key + '"' for key in row)
                    placeholders = ','.join('?' for _ in row)
                    fresh.execute(f'INSERT INTO "{table}" ({columns}) VALUES ({placeholders})', tuple(row.values()))
            if fresh.execute("PRAGMA foreign_key_check").fetchone():
                raise RuntimeError("Retained configuration failed foreign key validation")
            tables = json.loads((ROOT / "apps/server/migrations/schema-tables.json").read_text(encoding="utf-8"))
            for table in set(tables) - set(CONFIG_TABLES):
                if fresh.execute(f'SELECT count(*) FROM "{table}"').fetchone()[0]:
                    raise RuntimeError("Fresh database contains unexpected business rows")
            fresh.commit()
        summary.update(ready=True, execution_epoch=epoch, archive_sha256=hashlib.sha256(snapshot.read_bytes()).hexdigest())
        (output / "reset-report.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
        for file in (snapshot, archive / "source.env", output / "deployment.env", target):
            file.chmod(0o600)
        return summary


def main():
    parser = argparse.ArgumentParser(description="Prepare a private R3 reset bundle; never activate or delete source data")
    parser.add_argument("--source-env", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--confirm-reset-test-projects", action="store_true")
    parser.add_argument("--services-stopped", action="store_true")
    parser.add_argument("--acknowledge-inflight", action="store_true")
    args = parser.parse_args()
    try:
        report = prepare(args.source_env, args.output, apply=args.apply, confirmed=args.confirm_reset_test_projects,
                         stopped=args.services_stopped, acknowledged=args.acknowledge_inflight)
    except RuntimeError as error:
        raise SystemExit(str(error)) from None
    except Exception as error:
        raise SystemExit("Reset preparation failed (" + type(error).__name__ + "); private diagnostics withheld") from None
    print(json.dumps(report))


if __name__ == "__main__":
    main()
