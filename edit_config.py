"""Show decrypted settings or apply KEY=VALUE changes and save them encrypted."""
import argparse
import json
import os
from pathlib import Path
import tempfile

from utils import decrypt_config, encrypt_config

DEFAULT_CONFIG = Path(__file__).resolve().with_name("config.json")


def set_value(config, assignment):
    """Set a dotted object path using a JSON value or an unquoted string."""
    name, separator, raw = assignment.partition("=")
    parts = name.split(".")
    if not separator or any(not part for part in parts):
        raise ValueError(f"Expected KEY=VALUE, got {assignment!r}")
    try:
        value = json.loads(raw)
    except json.JSONDecodeError:
        value = raw
    target = config
    for part in parts[:-1]:
        target = target.get(part)
        if not isinstance(target, dict):
            raise ValueError(f"Parent of {name!r} must be an existing JSON object")
    target[parts[-1]] = value


def save_config(path, config):
    """Encrypt before atomically replacing the file with a private copy."""
    output = json.dumps(encrypt_config(config), ensure_ascii=False, indent=2) + "\n"
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w", encoding="utf-8", dir=path.parent,
            prefix=f".{path.name}.", suffix=".tmp", delete=False,
        ) as stream:
            temporary = Path(stream.name)
            stream.write(output)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("-c", "--config", type=Path, default=DEFAULT_CONFIG,
                        help="config file (default: config.json beside this script)")
    parser.add_argument("changes", metavar="KEY=VALUE", nargs="*",
                        help="e.g. useProxy=true gpu.power_limit=120 worker=my-gpu")
    args = parser.parse_args()
    try:
        path = args.config.expanduser().resolve()
        config = decrypt_config(json.loads(path.read_text(encoding="utf-8")))
        if not args.changes:
            print(json.dumps(config, ensure_ascii=False, indent=2))
            return
        for assignment in args.changes:
            set_value(config, assignment)
        save_config(path, config)
        print(f"Updated and encrypted {path}")
    except (OSError, ValueError, TypeError) as exc:
        parser.exit(1, f"Config edit failed: {exc}\n")


if __name__ == "__main__":
    main()
