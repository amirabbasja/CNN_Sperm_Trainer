"""Localhost-only end-to-end checks. Python and openssl are test prerequisites."""
import base64
import http.server
import json
import os
from pathlib import Path
import shutil
import signal
import socket
import ssl
import subprocess
import sys
import tempfile
import threading
import time

ASSETS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ASSETS))
from check_profiles import bundled_binary, stop_process
from https_probe import https_probe
from share_links import import_link


def free_port():
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', 0))
        return sock.getsockname()[1]


class Handler(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b'local connection kit test')

    def log_message(self, *_):
        pass


def main():
    upstream = None
    sentinel = None
    https = None
    with tempfile.TemporaryDirectory(prefix='connection-kit-test-') as tmp:
        directory = Path(tmp)
        kit = directory / 'kit with spaces'
        kit.mkdir()
        shutil.copytree(ASSETS, kit / 'assets',
                        ignore=shutil.ignore_patterns('reports', '__pycache__', 'links.txt'))
        for name in ('connection_setup.sh', 'proxy_connect.sh'):
            shutil.copy2(ASSETS.parent / name, kit / name)
        env = os.environ.copy()
        env['PYTHONDONTWRITEBYTECODE'] = '1'
        setup = ['bash', str(kit / 'connection_setup.sh')]

        def run(arguments, expected=0, timeout=30):
            result = subprocess.run(arguments, cwd=directory, env=env, text=True,
                                    capture_output=True, timeout=timeout)
            assert result.returncode == expected, (arguments, result.returncode, result.stdout, result.stderr)
            return result

        try:
            subprocess.run(['openssl', 'req', '-x509', '-newkey', 'rsa:2048', '-nodes',
                            '-days', '1', '-subj', '/CN=localhost', '-addext', 'subjectAltName=DNS:localhost',
                            '-keyout', str(directory / 'key.pem'), '-out', str(directory / 'cert.pem')],
                           check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
            context.load_cert_chain(str(directory / 'cert.pem'), str(directory / 'key.pem'))
            https = http.server.ThreadingHTTPServer(('127.0.0.1', 0), Handler)
            https.socket = context.wrap_socket(https.socket, server_side=True)
            threading.Thread(target=https.serve_forever, daemon=True).start()
            url = f'https://localhost:{https.server_port}/'
            env['SSL_CERT_FILE'] = str(directory / 'cert.pem')

            upstream_port = free_port()
            secret = 'only-a-local-test-password'
            upstream_config = {
                'log': {'loglevel': 'warning'},
                'inbounds': [{'listen': '127.0.0.1', 'port': upstream_port, 'protocol': 'shadowsocks',
                              'settings': {'method': 'chacha20-ietf-poly1305', 'password': secret, 'network': 'tcp'}}],
                'outbounds': [{'protocol': 'freedom'}],
            }
            (directory / 'server.json').write_text(json.dumps(upstream_config))
            upstream = subprocess.Popen([str(bundled_binary()), 'run', '-config', str(directory / 'server.json')],
                                        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            for _ in range(100):
                assert upstream.poll() is None
                try:
                    with socket.create_connection(('127.0.0.1', upstream_port), .1):
                        break
                except OSError:
                    time.sleep(.05)

            def link(password, label):
                user = base64.urlsafe_b64encode(('chacha20-ietf-poly1305:' + password).encode()).decode().rstrip('=')
                return f'ss://{user}@127.0.0.1:{upstream_port}#{label}'

            good = directory / 'good.txt'
            good.write_text(link(secret, 'working') + '\n')
            bad = directory / 'bad.txt'
            bad.write_text(link('wrong', 'bad-password') + '\n')
            mixed = directory / 'links.txt'
            mixed.write_text(good.read_text() + bad.read_text() +
                             link(secret, 'plugin').split('#')[0] + '?plugin=obfs-local#unsupported\n')
            local_port = free_port()
            test_options = ['--url', url, '--timeout', '2', '--attempts', '1', '--port', str(local_port)]
            result = run(setup + ['setup', '--links', str(mixed)] + test_options)
            assert secret not in result.stdout + result.stderr
            reports = kit / 'assets' / 'reports'
            report = json.loads(next(reports.glob('*/results.json')).read_text())
            assert [r['status'] for r in report['profiles']][0] == 'OK', report
            assert report['profiles'][1]['status'] not in ('OK', 'PARTIAL'), report
            assert report['profiles'][2]['status'] == 'UNSUPPORTED', report
            state = kit / '.connection'
            initial_pid = json.loads((state / 'process.json').read_text())
            initial_config = (state / 'client.json').read_bytes()
            assert json.loads(initial_config)['inbounds'][0]['protocol'] == 'mixed'
            assert (state / 'client.json').stat().st_mode & 0o777 == 0o600
            assert state.stat().st_mode & 0o777 == 0o700
            assert https_probe(local_port, url, 3, str(directory / 'cert.pem'))['status'] == 'OK'
            assert https_probe(local_port, url, 3, '/etc/ssl/certs/ca-certificates.crt')['status'] == 'TLS_ERROR'
            run(setup + ['status'])
            run(setup + ['check'])
            run(setup + ['start'])
            assert json.loads((state / 'process.json').read_text()) == initial_pid
            print('Profile selection, HTTPS verification, mixed listener, permissions, idempotent start: PASS')

            whole = ['bash', str(kit / 'proxy_connect.sh')]
            command = [sys.executable, '-c',
                       'import os, urllib.request; '
                       'assert os.environ["all_proxy"].startswith("socks5h://127.0.0.1:"); '
                       'os.environ["NO_PROXY"]=""; os.environ["no_proxy"]=""; '
                       'print(urllib.request.urlopen(' + repr(url) + ',timeout=4).status)']
            result = run(whole + command)
            assert result.stdout.strip() == '200', result
            exports = run(whole + ['--print-env']).stdout.splitlines()
            assert len(exports) == 8 and all(line.startswith('export ') for line in exports), exports
            assert 'socks5h://127.0.0.1:' + str(local_port) in '\n'.join(exports)
            run(whole + [sys.executable, '-c', 'raise SystemExit(17)'], expected=17)
            print('Command launcher, real HTTP CONNECT forwarding, clean shell exports, exit-code propagation: PASS')

            run(setup + ['setup', '--links', str(bad)] + test_options, expected=1)
            assert (state / 'client.json').read_bytes() == initial_config
            assert json.loads((state / 'process.json').read_text()) == initial_pid
            _, direct_bad = import_link(link('wrong', 'no-direct-fallback'))
            direct_bad['outbounds'].insert(0, {'tag': 'direct', 'protocol': 'freedom'})
            direct_bad['routing'] = {'rules': [{'type': 'field', 'network': 'tcp', 'outboundTag': 'direct'}]}
            bad_json = directory / 'bad-direct.json'
            bad_json.write_text(json.dumps(direct_bad))
            run(setup + ['test', '--links', str(bad_json)] + test_options, expected=1)
            assert json.loads((state / 'process.json').read_text()) == initial_pid
            with socket.socket() as busy:
                busy.bind(('127.0.0.1', 0))
                busy.listen(1)
                busy_port = busy.getsockname()[1]
                busy_options = test_options[:-2] + ['--port', str(busy_port)]
                result = run(setup + ['setup', '--links', str(good)] + busy_options, expected=1)
                assert 'already in use' in result.stderr
            assert (state / 'client.json').read_bytes() == initial_config
            assert json.loads((state / 'process.json').read_text()) == initial_pid
            print('All-failed preservation, direct-route false-positive prevention, port conflict safety: PASS')

            with (directory / 'interrupt.log').open('w') as output:
                testing = subprocess.Popen(setup + ['test', '--links', str(bad), '--timeout', '30',
                                                    '--attempts', '5', '--url', url], cwd=directory,
                                           stdout=output, stderr=output, env=env, start_new_session=True)
                try:
                    time.sleep(.8)
                    testing.send_signal(signal.SIGTERM)
                    assert testing.wait(timeout=8) == 130
                finally:
                    stop_process(testing)
            assert not list(reports.glob('*/run-*')), 'Temporary test config leaked after interruption'
            run(setup + ['restart'])
            new_pid = json.loads((state / 'process.json').read_text())
            assert new_pid['pid'] != initial_pid['pid']
            run(setup + ['stop'])
            run(setup + ['status'], expected=1)
            run(whole + ['--print-env'])  # automatically starts the saved config
            run(setup + ['stop'])
            sentinel = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)'])
            (state / 'process.json').write_text(json.dumps({'pid': sentinel.pid, 'starttime': '1', 'port': local_port}))
            run(setup + ['stop'])
            assert sentinel.poll() is None, 'Unrelated process was stopped'
            print('Interrupted checker cleanup, restart/stop, launcher auto-start, stale PID safeguard: PASS')

            run(setup + ['start'])
            stop_process(upstream)
            result = https_probe(local_port, url, 2, str(directory / 'cert.pem'))
            assert result['status'] != 'OK', result
            run(whole + command, expected=1)
            print('Unavailable upstream fails for both SOCKS and HTTP; no direct fallback: PASS')
            print('All connections were loopback-only. No user endpoint was contacted.')
        finally:
            subprocess.run(setup + ['stop'], cwd=directory, env=env, stdout=subprocess.DEVNULL,
                           stderr=subprocess.DEVNULL, timeout=10)
            stop_process(upstream)
            stop_process(sentinel)
            if https is not None:
                https.shutdown()
                https.server_close()


if __name__ == '__main__':
    main()
