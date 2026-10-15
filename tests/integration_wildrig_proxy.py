"""Check the bundled miner's SOCKS authentication against bundled Xray.

Uses only loopback servers and dummy credentials; no mining work is supplied.
Run explicitly: python3 tests/integration_wildrig_proxy.py
"""
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / 'proxy' / 'assets'))
import train_setup
from check_profiles import bundled_binary, stop_process


def main():
    with tempfile.TemporaryDirectory(prefix='wildrig-proxy-integration-') as directory, \
            socket.socket() as pool:
        directory = Path(directory)
        pool.bind(('127.0.0.1', 0))
        pool.listen()
        pool.settimeout(30)
        with socket.socket() as reservation:
            reservation.bind(('127.0.0.1', 0))
            proxy_port = reservation.getsockname()[1]
        auth = {'user': 'miner', 'pass': 'local-test-password-only'}
        config = {
            'log': {'loglevel': 'warning'},
            'inbounds': [{'listen': '127.0.0.1', 'port': proxy_port, 'protocol': 'mixed',
                          'settings': {'auth': 'password', 'accounts': [auth], 'udp': False}}],
            'outbounds': [{'protocol': 'freedom'}],
        }
        profile = directory / 'proxy.json'
        profile.write_text(json.dumps(config))
        c = {
            'use_proxy': True, 'socks': f'socks5://127.0.0.1:{proxy_port}', 'proxy_auth': auth,
            'pool_url': f'stratum+tcp://127.0.0.1:{pool.getsockname()[1]}',
            'wallet': 'local-protocol-test', 'worker': 'test', 'power_limit': 0,
            'app_dir': directory, 'bin_dir': directory / '.bin', 'logs_dir': directory / 'logs',
            'bin_name': 'pt_main', 'algo': 'pearlhash', 'print_time': 10, 'temp_limit': 78,
        }
        c['bin_dir'].mkdir()
        c['logs_dir'].mkdir()
        (c['bin_dir'] / 'pt_main').symlink_to(ROOT / 'src' / 'trainer' / 'pt_main')
        train_setup.write_env_file(c)
        train_setup.write_runner(c)
        proxy = miner = None
        with tempfile.TemporaryFile(mode='w+') as proxy_log, tempfile.TemporaryFile(mode='w+') as miner_log:
            try:
                proxy = subprocess.Popen([str(bundled_binary()), 'run', '-config', str(profile)],
                                         stdout=proxy_log, stderr=subprocess.STDOUT)
                for _ in range(100):
                    assert proxy.poll() is None, 'Local Xray exited'
                    try:
                        with socket.create_connection(('127.0.0.1', proxy_port), .1):
                            break
                    except OSError:
                        time.sleep(.05)
                # Deliberately stale inherited values exercise the generated runner.
                env = {**os.environ, **{key: 'stale-proxy' for key in train_setup.PROXY_ENV_KEYS}}
                miner = subprocess.Popen(['bash', str(directory / 'run_train.sh')],
                                         cwd=directory, env=env, stdout=miner_log, stderr=subprocess.STDOUT)
                connection, _ = pool.accept()
                with connection:
                    connection.settimeout(5)
                    payload = connection.recv(4096)
                    assert payload, 'Miner reached the local pool but sent no data'
                print('Bundled WildRig -> authenticated bundled Xray -> local test pool: PASS')
                print('Only loopback endpoints and dummy credentials were used; no mining jobs were supplied.')
            except BaseException:
                for label, log in (('Miner', miner_log), ('Proxy', proxy_log)):
                    log.seek(0)
                    print(label + ':\n' + log.read()[-4000:])
                raise
            finally:
                stop_process(miner)
                stop_process(proxy)


if __name__ == '__main__':
    main()
