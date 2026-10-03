"""Configuration tests that never touch an existing user configuration."""

import importlib.util
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from cryptography.fernet import Fernet
from dotenv import dotenv_values


SOURCE = Path(__file__).resolve().parents[1] / "scripts" / "init_local_config.py"
spec = importlib.util.spec_from_file_location("local_config", SOURCE)
config = importlib.util.module_from_spec(spec)
spec.loader.exec_module(config)


class LocalConfigTests(unittest.TestCase):
    def test_fresh_config_round_trip(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            password = 'a-long-test-password-"\\quoted#value'
            with patch.object(config, "__file__", str(root / "scripts" / "init_local_config.py")), patch.object(config, "getpass", side_effect=[password, password]), patch("builtins.print"):
                config.main()
            values = dotenv_values(root / ".env")
            self.assertEqual(values["BOOTSTRAP_ADMIN_PASSWORD"], password)
            self.assertEqual(values["RUNTIME_EXECUTION_LOCATION"], "local")
            self.assertGreaterEqual(len(values["JWT_SECRET"]), 32)
            self.assertNotEqual(values["JWT_SECRET"], values["PROVIDER_ENCRYPTION_KEY"])
            Fernet(values["PROVIDER_ENCRYPTION_KEY"].encode("ascii"))

    def test_existing_config_is_preserved(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            target = root / ".env"
            target.write_text("preserve-me", encoding="utf-8")
            with patch.object(config, "__file__", str(root / "scripts" / "init_local_config.py")), self.assertRaises(SystemExit):
                config.main()
            self.assertEqual(target.read_text(encoding="utf-8"), "preserve-me")

    def test_invalid_password_creates_nothing(self):
        for answers in (["short"], ["long-password-123", "different-password"]):
            with tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                with patch.object(config, "__file__", str(root / "scripts" / "init_local_config.py")), patch.object(config, "getpass", side_effect=answers), self.assertRaises(SystemExit):
                    config.main()
                self.assertFalse((root / ".env").exists())


if __name__ == "__main__":
    unittest.main()
