# Rootless connection kit for Debian and Ubuntu

This package runs a local SOCKS5/HTTP proxy with no sudo, TUN device, systemd,
or network-administration capability. It works with your existing server profiles.
Setup tests your profiles and starts the successful one with the lowest median
HTTPS response time. No server installation or server changes are needed.

`proxy_connect.sh` launches a command or a Bash shell with proxy environment
variables. Only programs that
support those variables use the proxy. Other processes, existing terminals,
UDP/QUIC, and applications that ignore proxy settings are unaffected. This kit
does not configure transparent system-wide routing or a system-wide kill switch.

## Requirements and included dependencies

- Linux Debian 12+ or Ubuntu 22.04+, on x86_64/amd64 or aarch64/arm64.
- Bash and **Python 3.9+ with its standard `ssl` module** already installed.
  The OS must provide trusted CA certificates for HTTPS verification.
- A writable directory on a filesystem that permits executable files, `/proc`,
  and permission to open localhost TCP sockets and connect to your server.
- At least one valid `ss://` or `vless://` link, or a full Xray client JSON export.

The proxy executables for both architectures, standard geodata, link parser,
checker, and lifecycle manager are included under `assets`. There are **no pip
packages, curl dependency, apt commands, or runtime downloads**. Python, Bash,
the Linux kernel, and the OS certificate store are prerequisites, not bundled.
The Linux executables are Xray v26.3.27 renamed to `net-client`.

## First use

From the project root, enter the proxy directory. Commands in this guide run
from that directory:

```bash
cd proxy
python3 --version
cp -n assets/links.example.txt assets/links.txt
chmod 600 assets/links.txt
```

Put one complete share link on each line of `assets/links.txt`. To enter them
without putting credentials into shell command history, run these commands,
paste the links as input to `cat`, then press **Ctrl+D**:

```bash
umask 077
cat > assets/links.txt
```

Use raw links, not Markdown hyperlinks. Blank lines and lines starting with `#`
are ignored. The tracked `assets/links.example.txt` contains comments only;
`assets/links.txt` is your private, Git-ignored working copy.
Then run:

```bash
bash connection_setup.sh
bash connection_setup.sh status
```

The default setup makes two HTTPS test requests per profile, allowing up to 15
seconds per request. It selects the lowest median time among profiles that pass
**every attempt**, validates the selected config, and starts a background proxy
listening only on **127.0.0.1:10808**. This port accepts both SOCKS5 and HTTP
proxy connections. Nothing listens on the machine's public address.

If an earlier client already owns port 10808, stop it using its own manager or
use another port. This kit never stops an unrelated client:

```bash
bash connection_setup.sh --port 10809
```

All bundled paths are located relative to the scripts, so you can also invoke
them by absolute path from another directory. A custom `--links` path is relative
to your current directory. You do not need to pass `--binary`.

## Use the connection

Launch a shell whose proxy-aware child commands use the connection:

```bash
bash proxy_connect.sh
# Run your commands here. Type exit to return to your original shell.
```

Or launch a single command (curl here is an optional example, not a dependency):

```bash
bash proxy_connect.sh curl https://api.ipify.org
bash proxy_connect.sh git ls-remote https://github.com/XTLS/Xray-core.git HEAD
```

The launcher starts the saved client if it is stopped; you must complete setup
once first. The client remains running when the child command/shell exits. The
interactive shell uses `bash --noprofile --norc -i` to preserve the supplied
proxy settings rather than loading shell startup files.

For your **current** Bash shell, explicitly import the generated exports:

```bash
eval "$(bash proxy_connect.sh --print-env)"
```

This sets lowercase and uppercase `http_proxy`, `https_proxy`, and `all_proxy`.
HTTP/HTTPS use `http://127.0.0.1:PORT`; SOCKS-aware tools can use
`socks5h://127.0.0.1:PORT`. `NO_PROXY`/`no_proxy` are set to localhost addresses,
replacing any inherited bypass list in that shell. HTTP CONNECT and `socks5h`
let the proxy handle destination names; programs using `socks5://` may resolve
them locally instead. This is not a change to system DNS settings.

Applications such as a browser or a library that does not honor these variables
need their own proxy settings. Some Python libraries require their own optional
SOCKS support; the HTTP proxy may avoid that requirement. SSH, arbitrary UDP,
and already-running services do not become proxied by exporting variables.

To remove these settings from the current shell:

```bash
unset http_proxy https_proxy all_proxy HTTP_PROXY HTTPS_PROXY ALL_PROXY no_proxy NO_PROXY
```

This removes the variables; it does not restore any older proxy values. A child
shell leaves its parent shell's original environment intact when it exits.

## Control and retest

```bash
bash connection_setup.sh test                     # test without switching
bash connection_setup.sh setup --attempts 3       # test, choose, and activate
bash connection_setup.sh start                    # reuse saved configuration
bash connection_setup.sh stop
bash connection_setup.sh restart
bash connection_setup.sh status
bash connection_setup.sh check                    # syntax validation only
bash connection_setup.sh logs                     # last 60 runtime log lines
bash connection_setup.sh verify                   # bundled asset checksums
```

`status` confirms a matching local process, not that the upstream still works.
There is no automatic failover or periodic retesting. Rerun setup to select
another profile. The old client remains running while new candidates are tested.
If none passes, it is kept. On activation, the prior config is backed up as
`.connection/client.previous.json`; a failed start attempts to restore it.
There is a brief interruption when switching a running client.

Run just the Python checker, with or without an explicit input path:

```bash
python3 assets/check_profiles.py
python3 assets/check_profiles.py assets/links.txt --attempts 2 --timeout 10
python3 assets/check_profiles.py /path/to/profile.json --tag proxy
python3 assets/check_profiles.py /path/to/json-directory
```

The checker uses separate temporary localhost listeners. It stops those clients
after testing, including on Ctrl+C, and does not activate a profile. It writes
`best.json` when at least one profile passes all attempts. Setup performs the
extra activation step. To use a different input or HTTPS test site with setup:

```bash
bash connection_setup.sh --links /path/to/links.txt --url https://www.cloudflare.com/cdn-cgi/trace
```

Use a public HTTPS URL without tokens or credentials. HTTP 2xx/3xx responses
count as success; redirects are not followed. The measured time ends at the
HTTP response headers and includes connection setup, SOCKS negotiation, and
TLS. **This is an HTTPS connectivity test, not ICMP ping, bandwidth measurement,
or a guarantee of reliability.** A failure can come from the account, server,
DNS, filtering, unsupported options, certificate trust, or the test website.

## Files and privacy

```text
README.md
VALIDATION.md
connection_setup.sh
proxy_connect.sh
assets/
  links.example.txt              tracked input template
  links.txt                      your private input
  check_profiles.py              standalone checker
  share_links.py                 conservative link parser
  https_probe.py                 verified HTTPS through SOCKS5
  manage_connection.py           setup/lifecycle/environment helper
  bin/linux-amd64/net-client
  bin/linux-arm64/net-client
  geodata/geoip.dat
  geodata/geosite.dat
  licenses/Xray-LICENSE
  SHA256SUMS                     integrity checks, not a signature
  reports/                       created by tests
.connection/                     saved config, PID record and logs
```

Reports, imported JSON profiles, active configs, and logs may contain private
server information or credentials. New state directories have mode 700 and
files mode 600. `assets/links.txt` is shipped with mode 600; keep that mode when
replacing it. Do not publish the input, `.connection`, or `assets/reports`.
Checksums exclude editable input and generated state. If you intentionally edit
bundled Python code, the manifest must also be regenerated before setup/start.

The proxy is unauthenticated on localhost; other processes sharing your host
network namespace may be able to use it. This package is intended for your own
workspace. Stop it before moving or deleting the package; PID checks include
the executable path and kernel process start time. Concurrent control operations
are locked. Logs rotate on startup when larger than 5 MiB, not continuously.

The background executable's ordinary process name is `net-client`; the scripts
also appear as Bash/Python while running. **Renaming does not hide the program
from the host/provider**, its binary contents, startup banners, logs, endpoint
IP, port, or traffic patterns. The server's existing protocol is preserved.
A Shadowsocks profile cannot be upgraded to REALITY, TLS, port 443, or Cloudflare
camouflage by changing only this client. This package makes no invisibility
claim and does not erase system records.

## Supported profiles

- `ss://`: SIP002 base64/percent-encoded user info and legacy whole-payload
  base64. Shadowsocks plugins are rejected; they are not silently removed.
- `vless://`: TCP/RAW, WebSocket, ordinary gRPC; supported TLS and REALITY fields.
  Unknown options and unsupported transports such as XHTTP require a compatible
  full Xray JSON export. TLS certificate checks are not disabled by the importer.
- JSON: supported proxy outbounds, with DNS rules and outbound chains retained.
  Select an ambiguous outbound using `--tag`. A first routing rule forces traffic
  from the local listener through that outbound. Public listeners, control APIs,
  TUN settings, and background health monitors are not imported. Referenced
  custom files must exist; use absolute paths. Only use profiles you trust.

Share links typically omit DNS rules from the original app. They use the system
resolver to find the upstream server. Use a full JSON export if the provider's
DNS overrides are required. Bundled geodata supports JSON rules that reference
standard geoip/geosite files. The pinned core may reject features introduced
later. It currently warns that legacy Shadowsocks is deprecated.

## Troubleshooting and persistence

- `No profiles found`: fill `assets/links.txt` with actual share links.
- `UNSUPPORTED`: see the numbered private report log; export full JSON if needed.
- `CONFIG_ERROR`: see the private report and verify the profile matches this core.
- `TIMEOUT`/`CONNECT_ERROR`: check account validity, upstream reachability, DNS,
  and another test URL. This does not prove the provider detected a VPN.
- `TLS_ERROR`: verify the OS clock and trusted CA certificates. An optional
  `--ca-file /path/to/trusted-ca.pem` supplies trust for the **test URL** without
  disabling verification; it does not change an upstream profile's TLS settings.
- `Permission denied` launching the binary: retain executable permissions and
  extract to a filesystem that permits execution; this script cannot override
  the provider's mount restrictions.
- `Port ... in use`: stop the earlier client using its own launcher or select
  `--port 10809`. Never terminate arbitrary processes just to free the port.

This kit does not install a boot service or edit your shell configuration.
It detaches the client from the launching terminal, but the provider may still
stop it when the workspace shuts down or ends a session. After restarting the
workspace, run `bash connection_setup.sh start` or the command launcher again.

## Provenance and validation

Core binaries and geodata come from the previously downloaded official
[Xray v26.3.27 release](https://github.com/XTLS/Xray-core/releases/tag/v26.3.27).
Both architecture archives were checked against upstream SHA-256 digests before
repackaging. The original core license is included. Its source is available at
[XTLS/Xray-core](https://github.com/XTLS/Xray-core/tree/v26.3.27).
See also [SIP002](https://shadowsocks.org/doc/sip002.html) and the
[VLESS share-link proposal](https://github.com/XTLS/Xray-core/discussions/716).

See [VALIDATION.md](VALIDATION.md) for the checks performed on this package and their limits.
