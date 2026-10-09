"""Public contract tests use temporary paths, never a configured user database."""

import os
import tempfile
from pathlib import Path

_root = Path(tempfile.mkdtemp(prefix='dralo-public-contract-'))
os.environ.update({
    'APP_ENV': 'test', 'RUNTIME_EXECUTION_LOCATION': 'local',
    'WORKSPACE_ISOLATION_ENABLED': 'false',
    'DATABASE_URL': 'sqlite+aiosqlite:///' + (_root / 'test.db').as_posix(),
    'STORAGE_PATH': str(_root / 'storage'), 'STORAGE_CONFIG_PATH': str(_root / 'storage.json'),
    'LOG_PATH': str(_root / 'logs'), 'JWT_SECRET': 'public-test-only-not-production',
    'PROVIDER_ENCRYPTION_KEY': 'MDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDA=',
})
