"""Create fresh local credentials without replacing existing configuration."""

from getpass import getpass
from pathlib import Path
import secrets

from cryptography.fernet import Fernet


def main() -> None:
    root = Path(__file__).resolve().parents[1]
    target = root / ".env"
    if target.exists():
        raise SystemExit("Existing .env preserved; edit it yourself if needed.")
    password = getpass("Local admin password (at least 12 characters): ")
    if len(password) < 12 or any(value in password for value in ("\n", "\r", "\0")):
        raise SystemExit("Use a password of at least 12 characters without control characters.")
    if getpass("Confirm password: ") != password:
        raise SystemExit("Passwords do not match; no configuration written.")
    # Dotenv double-quoted values need escaping; never print generated secrets.
    quoted = password.replace("\\", "\\\\").replace('"', '\\"')
    contents = "\n".join([
        "APP_ENV=development", "RUNTIME_EXECUTION_LOCATION=local",
        "SERVER_HOST=127.0.0.1", "SERVER_PORT=8000",
        "DATABASE_URL=sqlite+aiosqlite:///./data/app.db",
        "STORAGE_PATH=./storage", "LOG_PATH=./logs",
        "JOB_CONCURRENCY_PRESET=low", "BOOTSTRAP_ADMIN_USERNAME=admin",
        f'BOOTSTRAP_ADMIN_PASSWORD="{quoted}"',
        f"JWT_SECRET={secrets.token_urlsafe(48)}",
        f"PROVIDER_ENCRYPTION_KEY={Fernet.generate_key().decode('ascii')}", "",
        f"EPISODE_PLANNING_EPOCH=r3-{secrets.token_hex(16)}", "",
    ])
    with target.open("x", encoding="utf-8", newline="\n") as handle:
        handle.write(contents)
    try:
        target.chmod(0o600)
    except OSError:
        pass
    print("Created local .env. Keep it private and backed up; username: admin.")


if __name__ == "__main__":
    main()
