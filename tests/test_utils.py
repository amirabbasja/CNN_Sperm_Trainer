import base64
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from utils import PREFIX, decrypt, decrypt_config, encrypt, encrypt_config

ROOT = Path(__file__).resolve().parents[1]


class EncryptionTests(unittest.TestCase):
    def test_round_trip_preserves_json_types_and_unicode(self):
        for value in ("", "سلام 🔑", False, True, 0, 120, 1.25, None,
                      [1, False, None, {"decKey": "nested value"}],
                      {"miner": {"algo": "pearlhash"}, "empty": {}}):
            with self.subTest(value=value):
                token = encrypt(value, "string key 🔑")
                result = decrypt(token, "string key 🔑")
                self.assertEqual(result, value)
                self.assertIs(type(result), type(value))

    def test_tokens_are_randomized(self):
        self.assertNotEqual(encrypt("same", "qwer"), encrypt("same", "qwer"))

    def test_wrong_key_and_tampered_data_fail(self):
        token = encrypt("secret", "qwer")
        with self.assertRaisesRegex(ValueError, "wrong decKey"):
            decrypt(token, "wrong")
        packed = bytearray(base64.urlsafe_b64decode(token[len(PREFIX):]))
        packed[-5] = ord("A") if packed[-5] != ord("A") else ord("B")
        damaged = PREFIX + base64.urlsafe_b64encode(packed).decode("ascii")
        with self.assertRaisesRegex(ValueError, "damaged"):
            decrypt(damaged, "qwer")

    def test_bad_keys_and_tokens_fail(self):
        for key in (None, "", 0, [], False):
            with self.subTest(key=key), self.assertRaisesRegex(ValueError, "nonempty string"):
                encrypt("value", key)
        for token in (None, 123, "plain text", "enc:v2:abc", PREFIX, PREFIX + "!!!"):
            with self.subTest(token=token), self.assertRaises(ValueError):
                decrypt(token, "qwer")

    def test_config_encrypts_every_name_and_value_except_root_dec_key(self):
        config = {"decKey": "qwer", "useProxy": False, "nested": {
            "decKey": "also encrypted", "list": [42, None, "value"]}}
        encrypted = encrypt_config(config)
        self.assertEqual(encrypted["decKey"], "qwer")
        for name, value in encrypted.items():
            if name != "decKey":
                self.assertTrue(name.startswith(PREFIX))
                self.assertTrue(value.startswith(PREFIX))
        self.assertNotIn("nested", json.dumps(encrypted))
        self.assertEqual(decrypt_config(encrypted), config)
        with self.assertRaisesRegex(ValueError, "already encrypted"):
            encrypt_config(encrypted)

    def test_decrypted_config_can_be_reencrypted_with_a_new_key(self):
        original = {"decKey": "old key", "setting": {"enabled": True}}
        config = encrypt_config(original, key="new key")
        self.assertEqual(config["decKey"], "new key")
        self.assertEqual(decrypt_config(config), {**original, "decKey": "new key"})
        self.assertEqual(original["decKey"], "old key")

    def test_invalid_configs_and_plaintext_settings_fail(self):
        for config in ([], None, {}, {"decKey": ""}, {"decKey": 123},
                       {"decKey": "qwer", "useProxy": False}):
            with self.subTest(config=config), self.assertRaises(ValueError):
                decrypt_config(config)

    def test_duplicate_or_reserved_decoded_names_fail(self):
        value = encrypt(1, "qwer")
        duplicate = {"decKey": "qwer", encrypt("name", "qwer"): value,
                     encrypt("name", "qwer"): value}
        reserved = {"decKey": "qwer", encrypt("decKey", "qwer"): value}
        non_string = {"decKey": "qwer", encrypt(42, "qwer"): value}
        for config in (duplicate, reserved, non_string):
            with self.subTest(config=config), self.assertRaisesRegex(ValueError, "setting name"):
                decrypt_config(config)

    def test_cli_round_trip_and_private_output(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "settings.json"
            original = {"decKey": "qwer", "setting": {"port": 9000, "enabled": False}}
            path.write_text(json.dumps(original))
            for operation in ("encrypt", "decrypt"):
                result = subprocess.run(
                    [sys.executable, str(ROOT / "utils.py"), operation, str(path), "-o", str(path)],
                    cwd=directory, capture_output=True, text=True)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(path.stat().st_mode & 0o777, 0o600)
                if operation == "encrypt":
                    self.assertNotIn("setting", json.loads(path.read_text()))
            self.assertEqual(json.loads(path.read_text()), original)


if __name__ == "__main__":
    unittest.main()
