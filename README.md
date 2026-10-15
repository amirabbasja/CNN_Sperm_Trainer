# GPU Neural Network Training

Install and run the GPU training runtime bundled in `src/trainer`. Setup copies
the local binary and its accompanying files; it requires no runtime download.

## Requirements

- Linux with a compatible GPU and working driver
- Python 3.9 or newer, Bash, and tmux
- The complete `src/trainer` directory, including the bundled executable
- When `useProxy` is `true`: a reachable SOCKS5 proxy (setup included in `proxy/`)

## Net-client: local SOCKS proxy

The self-contained `proxy/` directory includes the Xray runtime for Linux AMD64
and ARM64, geodata, profile checker, and setup/control scripts. It requires Bash,
Python 3.9+ with SSL, and trusted OS CA certificates; setup needs no sudo or downloads.
The bundled executable is named `net-client`; use the scripts below to configure
and manage it. Skip this section if you want direct pool connections.

Add your `ss://` or `vless://` share links to the private
`proxy/assets/links.txt`, one per line. If that file is missing in a fresh clone,
create it from the template first:

```bash
cp -n proxy/assets/links.example.txt proxy/assets/links.txt
chmod 600 proxy/assets/links.txt
```

After filling in the links, run from the project root:

```bash
bash proxy/connection_setup.sh
bash proxy/connection_setup.sh status
```

Setup tests the profiles and starts the fastest successful one on
`127.0.0.1:10808`, accepting SOCKS5 and HTTP connections. The `socks` value in
`params.json` already matches this port. If you select a different port with
`--port`, update `params.json` to match before launching the training runtime.
Proxy setup and training setup are separate steps. To use this client for pool
connections, set `"useProxy": true` in `params.json` and run training setup after
the proxy is running.

### Profiles and ports

Setup accepts share links or a full Xray JSON client export. For example:

```bash
bash proxy/connection_setup.sh setup --links /path/to/profile.json
bash proxy/connection_setup.sh setup --links /path/to/links.txt --port 10809
```

Custom input paths are relative to your current working directory. The default
input is always `proxy/assets/links.txt`, located relative to the scripts.
Setup tests each profile twice and selects the lowest median HTTPS response time
among profiles that pass both attempts. It saves the selected configuration in
`proxy/.connection/`. No automatic failover or boot service is installed.

### Start, stop, and troubleshoot

```bash
bash proxy/connection_setup.sh status   # local process status
bash proxy/connection_setup.sh start    # reuse the saved profile after a restart
bash proxy/connection_setup.sh stop
bash proxy/connection_setup.sh restart
bash proxy/connection_setup.sh logs     # recent runtime log lines
bash proxy/connection_setup.sh check    # validate the saved configuration
bash proxy/connection_setup.sh test     # test profiles without switching
bash proxy/connection_setup.sh setup    # retest, select, and activate a profile
bash proxy/connection_setup.sh verify   # check bundled asset integrity
```

`status` checks the local process; `test` checks upstream connectivity. If all
profiles fail during setup, the existing saved/running client is kept. If the
port is occupied, stop its owner or select another port and update `socks` in
`params.json` accordingly. After restarting the machine/workspace, run `start`.

### Use net-client with other commands

```bash
bash proxy/proxy_connect.sh                       # interactive proxy-aware shell
bash proxy/proxy_connect.sh -- curl https://example.com  # curl is optional
eval "$(bash proxy/proxy_connect.sh --print-env)"   # set proxies in this shell
```

The launcher starts the saved client if necessary. Exit the child shell with
`exit`; the client keeps running until stopped. To remove exported proxy settings
from the current shell:

```bash
unset http_proxy https_proxy all_proxy HTTP_PROXY HTTPS_PROXY ALL_PROXY no_proxy NO_PROXY
```

The command launcher applies proxy environment variables to a command or shell;
only applications that honor those settings use it. Private links, runtime state,
and reports are ignored by Git. See [proxy/README.md](proxy/README.md) for JSON
profiles, testing, lifecycle commands, and troubleshooting.

## Choose direct or proxy pool connections

Set these top-level fields in `params.json` to route pool connections through
net-client (or another SOCKS5 proxy):

```json
{
  "useProxy": true,
  "socks": "socks5://127.0.0.1:10808"
}
```

This is a configuration excerpt; keep the other fields in your existing file.
`useProxy` must be a JSON boolean, without quotation marks.

- **`true`**: passes the configured SOCKS5 proxy to the runtime for pool
  connections. Start net-client first; training setup does not start it for you.
- **`false` or omitted**: connects directly to the pool. The `socks` field is
  ignored and may be omitted. This is the default in the supplied configuration.

The runner clears inherited proxy environment settings before applying the chosen
mode, including settings retained by tmux or supplied by `proxy_connect.sh`.
Rerun `python3 train_setup.py` after changing the configuration to regenerate the
runner and restart the training session. Changes to JSON do not affect a running
session until setup is rerun.

## Project layout

```text
params.json             training and SOCKS settings
train_setup.py / .sh     training setup entrypoints
src/trainer/            bundled GPU runtime
src/weights_*.pth       existing weight files
tests/                  training setup tests
proxy/                  self-contained SOCKS5/HTTP proxy kit
```

## Usage

1. Edit the existing fields in `params.json`: configure the training algorithm,
   coordinator endpoint, and account identifier, and adjust the proxy and
   training worker identifier for your environment. Choose an algorithm
   supported by the bundled runtime; see `src/trainer/help.txt`.
2. Validate the configuration and local binary without installing or launching:

   ```bash
   python3 train_setup.py --check
   ```

3. Install and launch:

   ```bash
   python3 train_setup.py
   ```

Use `python3 train_setup.py -c other.json` for an alternate configuration.
`bash train_setup.sh` accepts the same arguments and uses the same configuration.
Both entrypoints locate `src/trainer` relative to the setup script, so they work
from any working directory. The shell entrypoint reads settings from the JSON
file, including when invoked with environment variables set.

## Settings

| Setting | Purpose |
|---|---|
| `useProxy` | Boolean: `true` routes pool connections through SOCKS5; `false` or omitted connects directly. |
| `socks` | Required only when `useProxy` is `true`; bundled proxy URL: `socks5://127.0.0.1:10808`. |
| Training coordinator | Coordinator endpoint for your chosen training algorithm. Required. |
| Training account | Your training account identifier. Required. |
| Training worker | Worker suffix appended to the account identifier with a dot. |
| Training algorithm | Algorithm supported by the bundled training runtime. Required. |
| Progress reporting | Training throughput reporting interval in seconds. |
| Temperature limit | Temperature at which training pauses, in Celsius. |
| `disguise.app_dir_name` | Install directory relative to your home directory; default `train`. |
| `disguise.binary_name` | Installed executable filename; default `pt_main`. |
| `disguise.tmux_session` | tmux session name; default `train`. |
| `gpu.power_limit` | Power limit in watts; `0` leaves the current setting unchanged. |

## Installation and control

Setup copies the local bundle into `~/train/.bin/`, names the installed executable
`pt_main`, writes a private `.env` file and `run_train.sh`, and starts tmux.
Libraries and runtime assets alongside the binary are copied too. The runner
works from `.bin` and includes `.bin` and `.bin/lib` in its library search path.

Re-running setup refreshes the installed files from `src/trainer` and replaces
the configured tmux session. To update the training runtime, replace the local bundle and
run setup again. Installation does not change global Git proxy settings.
The training process uses the connection mode selected by `useProxy`; setup itself needs no network.

With the default directory and session names:

```bash
tail -f ~/train/logs/session.log
tmux attach -t train
tmux kill-session -t train
```

If validation fails, fill in the missing settings or restore the local binary.
If launching fails, check that tmux is installed, your GPU driver works, and the
training coordinator is reachable, along with the proxy when enabled. The setup
does not install system dependencies.
