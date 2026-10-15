import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from edit_config import save_config
from utils import PREFIX, decrypt_config, encrypt_config

ROOT = Path(__file__).resolve().parents[1]


class EditConfigTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.config = {"decKey": "qwer", "useProxy": False,
                      "gpu": {"power_limit": 0}, "worker": "old"}
        cls.encrypted = json.dumps(encrypt_config(cls.config))

    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.path = Path(self.directory.name) / "config.json"
        self.path.write_text(self.encrypted, encoding="utf-8")

    def run_cli(self, *changes):
        return subprocess.run(
            [sys.executable, str(ROOT / "edit_config.py"), "-c", str(self.path), *changes],
            cwd=self.directory.name, capture_output=True, text=True)

    def test_show_decrypts_without_changing_file(self):
        result = self.run_cli()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout), self.config)
        self.assertEqual(self.path.read_text(), self.encrypted)

    def test_updates_types_nested_settings_strings_and_key(self):
        result = self.run_cli("useProxy=true", "gpu.power_limit=120", "worker=سلام=gpu",
                              "decKey=new-key", 'extra=[false, null, "text"]')
        self.assertEqual(result.returncode, 0, result.stderr)
        encrypted = json.loads(self.path.read_text())
        self.assertEqual(decrypt_config(encrypted), {
            "decKey": "new-key", "useProxy": True, "gpu": {"power_limit": 120},
            "worker": "سلام=gpu", "extra": [False, None, "text"],
        })
        for name, value in encrypted.items():
            if name != "decKey":
                self.assertTrue(name.startswith(PREFIX))
                self.assertTrue(value.startswith(PREFIX))
        self.assertEqual(self.path.stat().st_mode & 0o777, 0o600)
        self.assertEqual(list(self.path.parent.iterdir()), [self.path])

    def test_invalid_changes_leave_original_intact(self):
        for change in ("missing-equals", "gpu..power_limit=120", "worker.name=x",
                       "missing.child=1", "decKey=", "gpu.power_limit=NaN"):
            with self.subTest(change=change):
                result = self.run_cli("useProxy=true", change)
                self.assertNotEqual(result.returncode, 0)
                self.assertIn("Config edit failed:", result.stderr)
                self.assertEqual(self.path.read_text(), self.encrypted)

    def test_wrong_key_leaves_original_intact(self):
        config = json.loads(self.encrypted)
        config["decKey"] = "wrong"
        original = json.dumps(config)
        self.path.write_text(original)
        result = self.run_cli("useProxy=true")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("wrong decKey", result.stderr)
        self.assertEqual(self.path.read_text(), original)

    def test_share_link_is_saved_exactly_and_encrypted(self):
        link = "vless://example-id@proxy.example.invalid:443?security=tls&type=ws&path=%2Fws#My%20proxy"
        result = self.run_cli("proxyConfig=" + link, "useProxy=true")
        self.assertEqual(result.returncode, 0, result.stderr)
        content = self.path.read_text()
        self.assertNotIn(link, content)
        config = decrypt_config(json.loads(content))
        self.assertEqual(config["proxyConfig"], link)
        self.assertIs(config["useProxy"], True)

    def test_failed_replace_preserves_original_and_cleans_temporary_file(self):
        with patch("edit_config.os.replace", side_effect=OSError("cannot replace")):
            with self.assertRaisesRegex(OSError, "cannot replace"):
                save_config(self.path, self.config)
        self.assertEqual(self.path.read_text(), self.encrypted)
        self.assertEqual(list(self.path.parent.iterdir()), [self.path])


if __name__ == "__main__":
    unittest.main()
