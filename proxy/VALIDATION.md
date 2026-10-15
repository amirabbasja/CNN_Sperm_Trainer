# Validation of this package

Revalidated after integration into `GPU-Miner/proxy/` on 2026-10-15:

- All eight proxy unit tests and five training setup tests passed.
- Bash syntax checks and bundled asset checksum verification passed.
- The full localhost integration suite passed with the renamed
  `proxy_connect.sh`, including SOCKS5/HTTP forwarding, lifecycle operations,
  failure recovery, and operation from another directory with spaces in its path.
- Private links, reports, and runtime state are excluded by Git rules.

The integration test required execution outside the sandbox because the sandbox
blocks localhost sockets. It used dummy profiles and temporary local servers;
no real upstream profile was tested. The copied input contains no configured
profiles, so add a share link before running setup.

## Original package validation

Prepared on 2026-10-14. Tests ran on Linux x86_64 / Ubuntu 24.04 with Python
3.12 as an ordinary user. No sudo, TUN device, or routing changes were used.

Passed:

- Bash syntax checks for both entry-point scripts.
- Eight parser/configuration tests: modern and legacy Shadowsocks, encoded
  credentials and IPv6, VLESS REALITY, WebSocket/TLS, gRPC, unsupported options,
  DNS preservation, and ambiguous outbound rejection.
- Checksum verification of packaged executables, geodata, Python helpers, and
  the included core license. Both executables are statically linked Linux ELF
  binaries with the expected amd64/aarch64 machine type.
- Real localhost HTTPS and Shadowsocks integration: successful profile selected;
  wrong password and unsupported plugin rejected; an untrusted TLS certificate
  rejected; credentials not printed in the summary.
- Local SOCKS5 and HTTP CONNECT forwarding through the selected upstream.
  The command launcher correctly supplied proxy variables to a real HTTPS
  request, preserved command exit status, and printed clean shell exports.
- Private state/config permissions, repeated start without duplicates, restart,
  stop, saved-client auto-start, and operation from a different working directory
  with spaces in the installation path.
- All-failed candidate testing kept the running PID and configuration unchanged.
  An occupied port was rejected without stopping the current client.
- A source JSON direct-routing rule could not produce a false successful test
  when proxy authentication failed.
- Interrupting testing cleaned up temporary test configurations. A stale PID
  record did not cause an unrelated process to be signalled.
- After stopping the test upstream, SOCKS and HTTP requests failed instead of
  falling back to a direct connection. Temporary managed clients were stopped.

The integration tests contacted only loopback endpoints with dummy credentials.
They did not contact any user-supplied server. No background client is installed
or left active by preparation of this package.

Limits: this package was not executed on an ARM64 machine, a Debian 12 image,
Ubuntu 22.04, or the user's cloud workspace. VLESS link parsing was tested; live
remote VLESS/REALITY service availability was not. The common scripts and static
Linux binaries are intended to work on both Debian and Ubuntu, subject to the
prerequisites in README.md and provider restrictions. A successful short HTTPS
test does not guarantee long-term availability, bandwidth, or concealment.

To repeat the tests from the project's `proxy/` directory:

```bash
bash -n connection_setup.sh proxy_connect.sh
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=assets python3 -m unittest discover -s assets/tests -v
bash connection_setup.sh verify
PYTHONDONTWRITEBYTECODE=1 python3 assets/tests/integration_test.py
```

Only the integration test additionally needs the `openssl` command to create a
temporary local certificate and permission to open localhost sockets. Setup and
normal use do not require that command. The tests do not require root.

The integration suite also checks password-only SOCKS5 negotiation, authenticated
HTTP CONNECT, encoded credentials in the launcher environment, and preservation
of the active authenticated profile when a candidate fails.

From the project root, `python3 tests/integration_wildrig_proxy.py` additionally
starts the bundled WildRig and Xray executables with the generated mining runner.
It verifies that WildRig authenticates and sends data to a loopback test pool.
Only dummy credentials are used, and no mining jobs are supplied. This verifies
the local protocol fix, not acceptance of shares by a live pool.
