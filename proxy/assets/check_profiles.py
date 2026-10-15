#!/usr/bin/env python3
"""Test exported Xray client profiles with temporary local SOCKS listeners."""
import argparse
import copy
import datetime
import json
import os
from pathlib import Path
import platform
import signal
import socket
import statistics
import subprocess
import sys
import tempfile
import time
from urllib.parse import urlsplit
from share_links import UnsupportedLink, import_link
from https_probe import https_probe

PROXY_PROTOCOLS = {'shadowsocks', 'vless', 'vmess', 'trojan', 'socks', 'http'}
ASSETS = Path(__file__).resolve().parent


def bundled_binary():
    machine = platform.machine().lower()
    arch = {'x86_64': 'amd64', 'amd64': 'amd64', 'aarch64': 'arm64', 'arm64': 'arm64'}.get(machine)
    if platform.system() != 'Linux' or arch is None:
        raise ProfileError('This bundle supports Linux x86_64 and aarch64 only.')
    return ASSETS / 'bin' / ('linux-' + arch) / 'net-client'


class ProfileError(Exception):
    pass


def selected_outbound(config, requested_tag=None):
    outbounds = config.get('outbounds', [])
    candidates = [o for o in outbounds if o.get('protocol') in PROXY_PROTOCOLS]
    if requested_tag:
        candidates = [o for o in candidates if o.get('tag') == requested_tag]
    elif any(o.get('tag') == 'proxy' for o in candidates):
        candidates = [o for o in candidates if o.get('tag') == 'proxy']
    if len(candidates) != 1:
        raise ProfileError('Need one supported proxy outbound, or select it with --tag.')
    return candidates[0]


def make_test_config(source, port, requested_tag=None):
    original = copy.deepcopy(source)
    if not isinstance(original, dict):
        raise ProfileError('Expected a complete Xray JSON configuration object.')
    outbound = selected_outbound(original, requested_tag)
    tags = {o.get('tag') for o in original.get('outbounds', [])}
    if not outbound.get('tag'):
        tag = 'profile-check-upstream'
        while tag in tags:
            tag += '-x'
        outbound['tag'] = tag
    tag = outbound['tag']
    if sum(o.get('tag') == tag for o in original['outbounds']) != 1:
        raise ProfileError('Duplicate outbound tags; use a profile with unique tags.')
    # Preserve resolver hosts/rules and outbound chaining. Do not import public
    # listeners, TUN devices, control APIs, or background health monitors.
    config = {k: original[k] for k in ('dns', 'outbounds', 'routing', 'policy', 'fakedns') if k in original}
    config['log'] = {'loglevel': 'info', 'access': 'none'}
    config['inbounds'] = [{
        'tag': 'profile-check-input', 'listen': '127.0.0.1', 'port': port,
        'protocol': 'socks', 'settings': {'auth': 'noauth', 'udp': False},
    }]
    routing = config.setdefault('routing', {})
    routing['rules'] = [{
        'type': 'field', 'inboundTag': ['profile-check-input'], 'outboundTag': tag,
    }] + routing.get('rules', [])
    return config


def free_port():
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', 0))
        return sock.getsockname()[1]


def stop_process(process):
    if process is not None and process.poll() is None:
        process.terminate()
        try:
            process.wait(timeout=3)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait()


def run_profile(path, index, args, result_dir, environment):
    label = ''.join(c if c.isprintable() else '?' for c in path.name)
    row = {'profile': label, 'path': str(path.resolve()), 'status': '', 'median_ms': None, 'attempts': []}
    log_path = result_dir / f'{index:03d}.log'
    row['log'] = log_path.name
    process = None
    with log_path.open('w') as log, tempfile.TemporaryDirectory(prefix='run-', dir=result_dir) as scratch:
        try:
            source = json.loads(path.read_text(encoding='utf-8-sig'))
            if isinstance(source, dict) and '_profile_import_error' in source:
                log.write(source['_profile_import_error'] + '\n')
                row['status'] = 'UNSUPPORTED'
                return row
            port = free_port()
            config = make_test_config(source, port, args.tag)
            config_path = Path(scratch) / 'client.json'
            config_path.write_text(json.dumps(config))
            config_path.chmod(0o600)
            checked = subprocess.run(
                [str(args.binary), 'run', '-test', '-config', str(config_path)],
                stdout=log, stderr=log, env=environment, timeout=12,
            )
            if checked.returncode:
                row['status'] = 'CONFIG_ERROR'
                return row
            process = subprocess.Popen(
                [str(args.binary), 'run', '-config', str(config_path)],
                stdin=subprocess.DEVNULL, stdout=log, stderr=log, env=environment,
            )
            deadline = time.monotonic() + 5
            ready = False
            while time.monotonic() < deadline:
                if process.poll() is not None:
                    break
                try:
                    with socket.create_connection(('127.0.0.1', port), timeout=0.15):
                        ready = True
                        break
                except OSError:
                    time.sleep(0.05)
            if not ready or process.poll() is not None:
                row['status'] = 'START_FAILED'
                return row
            for _ in range(args.attempts):
                attempt = https_probe(port, args.url, args.timeout, args.ca_file)
                log.write('\nProbe: ' + json.dumps(attempt) + '\n')
                row['attempts'].append(attempt)
            successes = [a['ms'] for a in row['attempts'] if a['status'] == 'OK']
            if successes:
                row['status'] = 'OK' if len(successes) == args.attempts else 'PARTIAL'
                row['median_ms'] = round(statistics.median(successes))
            else:
                row['status'] = row['attempts'][-1]['status']
            return row
        except (ProfileError, ValueError, KeyError, TypeError, AttributeError, OSError) as exc:
            # Details stay in the private report, never echo raw profile contents.
            log.write(f'Profile preparation error: {type(exc).__name__}: {exc}\n')
            row['status'] = 'INPUT_ERROR'
            return row
        except subprocess.TimeoutExpired:
            row['status'] = 'CONFIG_TIMEOUT'
            return row
        finally:
            stop_process(process)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('profiles', nargs='?', type=Path, default=ASSETS / 'links.txt', help='defaults to assets/links.txt; also accepts a JSON file/directory')
    parser.add_argument('--binary', type=Path, help='defaults to the bundled binary for this architecture')
    parser.add_argument('--assets', type=Path, default=ASSETS / 'geodata')
    parser.add_argument('--report-dir', type=Path, default=ASSETS / 'reports')
    parser.add_argument('--best-output', type=Path, help='also write the selected config here, only on success')
    parser.add_argument('--port', type=int, default=10808, help='localhost port in exported best.json')
    parser.add_argument('--ca-file', help='optional trusted CA PEM for the HTTPS test; verification remains enabled')
    parser.add_argument('--tag', help='Outbound tag to test; otherwise prefer proxy or the sole supported proxy outbound')
    parser.add_argument('--url', default='https://example.com/', help='Public HTTPS test URL; no credentials or tokens')
    parser.add_argument('--timeout', type=int, default=15, help='Seconds allowed for each HTTPS request')
    parser.add_argument('--attempts', type=int, default=1, help='Requests per profile, from 1 to 5')
    args = parser.parse_args(argv)
    if not 1 <= args.attempts <= 5 or not 2 <= args.timeout <= 60:
        parser.error('Use 1..5 attempts and a timeout of 2..60 seconds.')
    if not 1024 <= args.port <= 65535:
        parser.error('--port must be between 1024 and 65535.')
    try:
        url = urlsplit(args.url)
        valid_url = url.scheme == 'https' and url.hostname and not url.username and not url.password
        valid_url = valid_url and (url.port is None or 1 <= url.port <= 65535)
    except ValueError:
        valid_url = False
    if not valid_url or any(ord(c) < 32 for c in args.url):
        parser.error('--url must be an HTTPS URL without user/password fields.')
    if args.binary is None:
        try:
            args.binary = bundled_binary()
        except ProfileError as exc:
            parser.error(str(exc))
    args.binary = args.binary.resolve()
    if not args.binary.is_file() or not os.access(args.binary, os.X_OK):
        parser.error('Executable not found; pass --binary /path/to/net-client.')
    if not args.profiles.exists():
        parser.error('Input file/directory does not exist.')
    os.umask(0o077)
    report_root = args.report_dir
    report_root.mkdir(parents=True, exist_ok=True, mode=0o700)
    report_root.chmod(0o700)
    stamp = datetime.datetime.now().strftime('%Y%m%d-%H%M%S-')
    result_dir = Path(tempfile.mkdtemp(prefix=stamp, dir=report_root)).resolve()
    if args.profiles.is_dir():
        profiles = sorted(args.profiles.glob('*.json'))
    elif args.profiles.suffix.lower() == '.json':
        profiles = [args.profiles]
    else:
        imported = result_dir / 'profiles'
        imported.mkdir(mode=0o700)
        profiles = []
        for line in args.profiles.read_text(encoding='utf-8-sig').splitlines():
            line = line.strip()
            if not line or line.startswith('#'):
                continue
            number = len(profiles) + 1
            try:
                label, config = import_link(line)
                label = ''.join(c if c.isalnum() or c in '-_' else '_' for c in label)[:32]
            except (UnsupportedLink, ValueError, TypeError, UnicodeError) as exc:
                label = 'unsupported'
                # Parser detail is private, not printed alongside credentials.
                config = {'_profile_import_error': str(exc)}
            path = imported / f'{number:03d}-{label or "profile"}.json'
            path.write_text(json.dumps(config, indent=2) + '\n')
            path.chmod(0o600)
            profiles.append(path)
    if not profiles:
        parser.error('No profiles found. Use one complete share link per line or *.json files.')
    environment = os.environ.copy()
    environment['XRAY_LOCATION_ASSET'] = str(args.assets.resolve())
    rows = []
    interrupted_run = False
    print('Testing each profile through its selected proxy outbound. Current client stays running.', flush=True)
    print(f'{"PROFILE":32} {"RESULT":16} {"HTTPS ms":>10} {"PASS":>6}', flush=True)
    try:
        for index, path in enumerate(profiles, 1):
            row = run_profile(path, index, args, result_dir, environment)
            rows.append(row)
            good = sum(a['status'] == 'OK' for a in row['attempts'])
            latency = str(row['median_ms']) if row['median_ms'] is not None else '-'
            print(f'{row["profile"][:32]:32} {row["status"]:16} {latency:>10} {good:>2}/{args.attempts:<3}', flush=True)
    except KeyboardInterrupt:
        interrupted_run = True
        print('\nInterrupted; temporary client stopped. Partial results saved.', flush=True)
    finally:
        (result_dir / 'results.json').write_text(json.dumps({'url': args.url, 'profiles': rows}, indent=2) + '\n')
        print(f'Private report: {result_dir}', flush=True)
    passing = [r for r in rows if r['status'] == 'OK'] if not interrupted_run else []
    if passing:
        best = min(passing, key=lambda r: r['median_ms'])
        best_source = json.loads(Path(best['path']).read_text(encoding='utf-8-sig'))
        best_config = make_test_config(best_source, args.port, args.tag)
        best_config['inbounds'][0]['protocol'] = 'mixed'
        best_config['log'] = {'loglevel': 'warning'}
        best_path = result_dir / 'best.json'
        best_path.write_text(json.dumps(best_config, indent=2) + '\n')
        best_path.chmod(0o600)
        if args.best_output:
            args.best_output.write_text(json.dumps(best_config, indent=2) + '\n')
            args.best_output.chmod(0o600)
        print(f'Lowest observed HTTPS time: {best["profile"]} ({best["median_ms"]} ms). No active profile was changed.')
        print(f'Ready-to-use selected configuration: {best_path}')
    print('Times include proxy setup, TLS and HTTP response headers; not ICMP ping or throughput.')
    return 130 if interrupted_run else (0 if passing else 1)


if __name__ == '__main__':
    def interrupted(*_):
        raise KeyboardInterrupt
    signal.signal(signal.SIGTERM, interrupted)
    sys.exit(main())
