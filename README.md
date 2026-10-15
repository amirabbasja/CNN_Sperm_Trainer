# pearl-train

Deploy a long-running GEMM-heavy compute job on a rented GPU cloud box.

All traffic is tunneled through a single SOCKS5 port, the runner is installed
under a neutral name, and the job runs detached in tmux. Everything
configurable — addresses, links, proxy, tuning — lives in `params.json`.

> Note: if your provider's terms prohibit this workload, disguising it does
> not make it permitted — worst case is account termination. If they only
> bill compute and don't care what runs, this is just privacy hygiene.

## Files

| File | Purpose |
|---|---|
| `pearl_train_setup.py` | One-shot setup: download, install, configure, launch. stdlib only, no pip deps. |
| `params.json` | Single source of truth for all settings. |

## Requirements on the box

- Any Linux GPU image with a working driver
- `curl` and `tmux` (`apt-get install -y curl tmux` — apt uses the tunnel)
- A reachable SOCKS5 proxy (`socks5://[user:pass@]host:port`)

## Usage

1. Edit `params.json`: set `socks` and `wallet.address` at minimum.
2. Validate without touching the network:

       python3 pearl_train_setup.py --check

   Prints every resolved value; refuses placeholder/missing values on a real run.
3. Deploy and launch:

       python3 pearl_train_setup.py

   Optional: `python3 pearl_train_setup.py -c other.json` for an alternate config.

The script is idempotent — re-running with changed params skips the download
and relaunches the job with the new values.

## params.json reference

```jsonc
{
  "socks": "socks5://user:pass@PROXY_HOST:PORT",  // all outbound traffic
  "pool": {
    "url": "stratum+tcp://pool.pearlhash.xyz:9000",  // stratum endpoint
    "dns_over_https": "cloudflare"                   // pool DNS resolution
  },
  "wallet": {
    "address": "YOUR_PRL_ADDRESS",  // payout address
    "worker": "gpu01"               // worker label on the pool dashboard
  },
  "miner": {
    "release_url": "https://github.com/andru-kun/wildrig-multi/releases/download/{version}/wildrig-multi-linux-{version}.tar.gz",
    "version": "0.51.4",
    "algo": "pearlhash",
    "pearlhash_kernel": 2,   // power-efficiency kernel (0.51.4+)
    "print_time": 120,       // hashrate report interval, seconds
    "temp_limit": 78         // stop-above temperature, Celsius
  },
  "disguise": {
    "app_dir_name": "train",    // install root: ~/train
    "binary_name": "pt_main",   // what nvidia-smi / ps show
    "tmux_session": "train"
  },
  "gpu": {
    "power_limit": 0   // watts; 0 = leave at card default
  }
}
```

## What the setup does

1. **Network** — exports `all_proxy`/`http_proxy`/`https_proxy` (and uppercase
   twins), configures git, and downloads the release with
   `curl --socks5-hostname` so even DNS goes through the tunnel. Nothing on
   the box talks to the internet directly.
2. **Install** — unpacks the miner to `~/train/.bin/` renamed to `pt_main`,
   bundling any shipped `.so` files alongside it.
3. **Launch** — writes `~/train/run_train.sh` and a `0600` `.env`, then runs
   it detached in tmux session `train`, logging to
   `~/train/logs/session.log`. Colored console output is disabled and no
   miner API port is opened.
4. **Runtime flags** — pool URL via SOCKS, DoH resolution, efficiency kernel,
   `--no-color`, `--print-time`, temp/power caps so the GPU signature is
   steady sustained load rather than max-TDP flatline.

## Verification and control

```bash
tail -f ~/train/logs/session.log   # accepted shares within ~2 min = working
tmux attach -t train               # live console view
tmux kill-session -t train         # stop
```

## Layout after install

```
~/train/
├── .bin/pt_main          # renamed runner + bundled libs
├── run_train.sh          # entrypoint
├── .env                  # runtime values (chmod 600)
├── logs/session.log      # miner output
└── checkpoints/          # empty, for looks and future state
```

## Troubleshooting

- **`curl or tar not found`** — install them first: `apt-get install -y curl tmux`.
- **No shares after minutes** — check the SOCKS proxy is reachable from the
  box (`curl --socks5-hostname ... https://ifconfig.me`), then the pool URL.
- **Many rejected shares** — the miner build must match the pool's current
  network rules; keep `version` current and re-run the script.
