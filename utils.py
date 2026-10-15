"""Encrypt JSON settings with a string key, preserving their original types.

The config file keeps decKey in plaintext by design. Anyone who can read that
file can decrypt it; this format only conceals the settings' plaintext on disk.
"""
import argparse
import base64
import binascii
import json
import os
from pathlib import Path

from cryptography.fernet import Fernet, InvalidToken
from cryptography.hazmat.primitives.kdf.scrypt import Scrypt

PREFIX = "enc:v1:"


def _validate_key(key):
    if not isinstance(key, str) or not key:
        raise ValueError("decKey must be a nonempty string")


def _cipher(key, salt):
    _validate_key(key)
    # Each token carries its own random salt. Keep these parameters stable for v1.
    kdf = Scrypt(salt=salt, length=32, n=2**15, r=8, p=1)
    return Fernet(base64.urlsafe_b64encode(kdf.derive(key.encode("utf-8"))))


def encrypt(value, key):
    """Encrypt a JSON-compatible value with a string key; return a text token."""
    _validate_key(key)
    payload = json.dumps(value, ensure_ascii=False, allow_nan=False,
                         separators=(",", ":")).encode("utf-8")
    salt = os.urandom(16)
    token = _cipher(key, salt).encrypt(payload)
    return PREFIX + base64.urlsafe_b64encode(salt + token).decode("ascii")


def decrypt(value, key):
    """Decrypt a token, restoring JSON types; reject wrong keys or damaged data."""
    _validate_key(key)
    if not isinstance(value, str) or not value.startswith(PREFIX):
        raise ValueError("Expected an encrypted enc:v1: token")
    try:
        packed = base64.b64decode(value[len(PREFIX):], altchars=b"-_", validate=True)
        if len(packed) <= 16:
            raise ValueError("Truncated encrypted token")
        payload = _cipher(key, packed[:16]).decrypt(packed[16:])
        return json.loads(payload.decode("utf-8"))
    except (InvalidToken, ValueError, binascii.Error) as exc:
        raise ValueError("Cannot decrypt setting: wrong decKey or damaged encrypted data") from exc


def encrypt_config(config, key=None):
    """Encrypt every setting name and full value, leaving only decKey readable.

    Nested objects and lists are encrypted inside their enclosing value, so no
    nested field names or scalar values remain visible in the output file.
    Pass key to set or change decKey on an already decrypted configuration.
    """
    if not isinstance(config, dict):
        raise ValueError("Configuration must be a JSON object")
    if key is None:
        key = config.get("decKey")
    _validate_key(key)
    result = {"decKey": key}
    for name, value in config.items():
        if name == "decKey":
            continue
        if not isinstance(name, str):
            raise ValueError("Configuration setting names must be strings")
        if name.startswith(PREFIX):
            raise ValueError("Configuration is already encrypted; decrypt it before editing")
        result[encrypt(name, key)] = encrypt(value, key)
    return result


def decrypt_config(config):
    """Read the plaintext decKey and decrypt all other config keys and values."""
    if not isinstance(config, dict):
        raise ValueError("Configuration must be a JSON object")
    key = config.get("decKey")
    _validate_key(key)
    result = {"decKey": key}
    for encrypted_name, encrypted_value in config.items():
        if encrypted_name == "decKey":
            continue
        name = decrypt(encrypted_name, key)
        if not isinstance(name, str) or name in result:
            raise ValueError("Invalid or duplicate decrypted setting name")
        result[name] = decrypt(encrypted_value, key)
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("operation", choices=("encrypt", "decrypt"))
    parser.add_argument("input", type=Path)
    parser.add_argument("-o", "--output", type=Path,
                        help="write a private JSON file instead of printing to stdout")
    args = parser.parse_args()
    try:
        config = json.loads(args.input.read_text(encoding="utf-8"))
        transform = encrypt_config if args.operation == "encrypt" else decrypt_config
        output = json.dumps(transform(config), ensure_ascii=False, indent=2) + "\n"
        if args.output is None:
            print(output, end="")
        else:
            fd = os.open(args.output, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
            with os.fdopen(fd, "w", encoding="utf-8") as stream:
                os.fchmod(stream.fileno(), 0o600)
                stream.write(output)
    except (OSError, ValueError, TypeError) as exc:
        parser.exit(1, f"Config {args.operation} failed: {exc}\n")


if __name__ == "__main__":
    main()
