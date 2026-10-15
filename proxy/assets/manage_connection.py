"""Manage a private, unprivileged localhost proxy. Invoked by the Bash scripts."""
import argparse
from contextlib import contextmanager, redirect_stdout
import fcntl
import errno
import hashlib
import json
import os
from pathlib import Path
import shlex
import signal
import socket
import subprocess
import sys
import tempfile
import time

from check_profiles import ASSETS, ProfileError, bundled_binary, stop_process

ROOT = ASSETS.parent
STATE = ROOT / '.connection'
CONFIG = STATE / 'client.json'
PID_FILE = STATE / 'process.json'
LOG = STATE / 'client.log'


def private_write(path, content):
    """Replace state atomically so interrupted writes cannot truncate a config."""
    fd, name = tempfile.mkstemp(prefix='.write-', dir=path.parent)
    try:
        with os.fdopen(fd, 'w') as handle:
            handle.write(content)
        os.replace(name, path)
    finally:
        if os.path.exists(name):
            os.unlink(name)


@contextmanager
def control_lock():
    with (STATE / 'control.lock').open('a') as handle:
        try:
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise ProfileError('Another setup/control operation is running; wait for it to finish.') from None
        yield


def environment():
    env = os.environ.copy()
    env['XRAY_LOCATION_ASSET'] = str(ASSETS / 'geodata')
    return env


def identity(pid):
    try:
        proc = Path('/proc') / str(pid)
        fields = (proc / 'stat').read_text().rsplit(')', 1)[1].split()
        if fields[0] == 'Z':
            return None
        executable = os.readlink(proc / 'exe')
        command = (proc / 'cmdline').read_bytes().split(b'\0')
        if executable != str(bundled_binary()) or os.fsencode(CONFIG) not in command:
            return None
        return fields[19]  # starttime (field 22); comm may itself contain spaces.
    except (OSError, IndexError, ValueError):
        return None


def tracked():
    try:
        saved = json.loads(PID_FILE.read_text())
        if type(saved['pid']) is not int or saved['pid'] <= 1:
            return None
        if type(saved['port']) is not int or not 1024 <= saved['port'] <= 65535:
            return None
        if identity(saved['pid']) == saved['starttime']:
            return saved
    except (OSError, ValueError, KeyError, TypeError):
        pass
    return None


def config_port(path=CONFIG):
    try:
        config = json.loads(path.read_text())
        inbound = config['inbounds'][0]
        port = inbound['port']
        if inbound['listen'] != '127.0.0.1' or inbound['protocol'] != 'mixed':
            raise ValueError('Expected the generated localhost mixed listener')
        if type(port) is not int or not 1024 <= port <= 65535:
            raise ValueError('Invalid unprivileged port')
        return port
    except (OSError, ValueError, KeyError, TypeError, IndexError) as exc:
        raise ProfileError('Missing/invalid saved configuration; run connection_setup.sh setup first.') from exc


def port_available(port):
    try:
        with socket.socket() as sock:
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            sock.bind(('127.0.0.1', port))
    except OSError:
        raise ProfileError(f'Port {port} is already in use. Stop its owner or run setup --port 10809. No other process was stopped.') from None


def verify_assets():
    for line in (ASSETS / 'SHA256SUMS').read_text().splitlines():
        digest, name = line.split('  ', 1)
        path = ASSETS / name
        if path.resolve().parent != ASSETS and ASSETS not in path.resolve().parents:
            raise ProfileError('Invalid checksum manifest path.')
        with path.open('rb') as handle:
            actual = hashlib.sha256()
            for block in iter(lambda: handle.read(1024 * 1024), b''):
                actual.update(block)
        if actual.hexdigest() != digest:
            raise ProfileError(f'Bundled asset failed its checksum: {name}. Extract the original bundle again.')


def validate(path=CONFIG):
    config_port(path)
    binary = bundled_binary()
    if not os.access(binary, os.X_OK):
        raise ProfileError('Bundled executable is missing/not executable; extract the complete tar.gz on an executable filesystem.')
    with (STATE / 'validation.log').open('w') as log:
        result = subprocess.run([str(binary), 'run', '-test', '-config', str(path)],
                                stdout=log, stderr=log, env=environment(), timeout=15)
    if result.returncode:
        raise ProfileError(f'Configuration validation failed; read {STATE / "validation.log"}.')


def start():
    saved = tracked()
    if saved:
        print(f'Already running: net-client PID {saved["pid"]}, 127.0.0.1:{saved["port"]}')
        return
    verify_assets()
    validate()
    port = config_port()
    port_available(port)
    if LOG.exists() and LOG.stat().st_size > 5 * 1024 * 1024:
        LOG.replace(STATE / 'client.log.previous')
    process = None
    try:
        with LOG.open('a') as log:
            process = subprocess.Popen([str(bundled_binary()), 'run', '-config', str(CONFIG)],
                                       stdin=subprocess.DEVNULL, stdout=log, stderr=log,
                                       env=environment(), start_new_session=True)
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            if process.poll() is not None:
                break
            ticks = identity(process.pid)
            if ticks:
                try:
                    with socket.create_connection(('127.0.0.1', port), timeout=.2):
                        time.sleep(.1)
                        if process.poll() is not None:
                            break
                        private_write(PID_FILE, json.dumps({'pid': process.pid, 'starttime': ticks, 'port': port}) + '\n')
                        print(f'Started net-client (PID {process.pid}). SOCKS5/HTTP: 127.0.0.1:{port}')
                        return
                except OSError:
                    pass
            time.sleep(.05)
        raise ProfileError(f'Client did not start; read {LOG}.')
    except BaseException:
        stop_process(process)
        raise


def stop():
    saved = tracked()
    if not saved:
        print('No matching client is running; no process was signalled.')
        return
    pid = saved['pid']
    fd = None
    try:
        if hasattr(os, 'pidfd_open') and hasattr(signal, 'pidfd_send_signal'):
            try:
                fd = os.pidfd_open(pid)
            except OSError as exc:
                if exc.errno not in (errno.ENOSYS, errno.EINVAL, errno.EPERM):
                    raise
        if identity(pid) == saved['starttime']:
            if fd is None:
                os.kill(pid, signal.SIGTERM)
            else:
                signal.pidfd_send_signal(fd, signal.SIGTERM)
    except ProcessLookupError:
        pass
    finally:
        if fd is not None:
            os.close(fd)
    for _ in range(60):
        if identity(pid) != saved['starttime']:
            PID_FILE.unlink(missing_ok=True)
            print('Stopped net-client.')
            return
        time.sleep(.1)
    raise ProfileError('Client has not exited; check the log. No forced kill was sent.')


def check_links(args, candidate=None):
    verify_assets()
    command = [sys.executable, str(ASSETS / 'check_profiles.py'), str(args.links),
               '--attempts', str(args.attempts), '--timeout', str(args.timeout),
               '--url', args.url, '--port', str(args.port)]
    for flag, value in [('--tag', args.tag), ('--ca-file', args.ca_file), ('--best-output', candidate)]:
        if value is not None:
            command.extend([flag, str(value)])
    process = subprocess.Popen(command, start_new_session=True)
    try:
        return process.wait()
    finally:
        stop_process(process)


def setup(args):
    with tempfile.TemporaryDirectory(prefix='candidate-', dir=STATE) as scratch:
        candidate = Path(scratch) / 'client.json'
        code = check_links(args, candidate)
        if code:
            print('No new profile activated. The existing saved/running client was kept.', file=sys.stderr)
            return code
        validate(candidate)
        current = tracked()
        if not current or current['port'] != args.port:
            port_available(args.port)
        previous = CONFIG.read_text() if CONFIG.exists() else None
        if previous is not None:
            private_write(STATE / 'client.previous.json', previous)
        if current:
            stop()
        try:
            private_write(CONFIG, candidate.read_text())
            start()
        except BaseException:
            if previous is None:
                CONFIG.unlink(missing_ok=True)
            else:
                private_write(CONFIG, previous)
                if current:
                    print('Activation failed; restarting the previous configuration.', file=sys.stderr)
                    start()
            raise
    print('Use: bash proxy_connect.sh [command ...]')
    return 0


def proxy_variables(port):
    http = f'http://127.0.0.1:{port}'
    socks = f'socks5h://127.0.0.1:{port}'
    return {'http_proxy': http, 'https_proxy': http, 'all_proxy': socks,
            'HTTP_PROXY': http, 'HTTPS_PROXY': http, 'ALL_PROXY': socks,
            'no_proxy': 'localhost,.localhost,127.0.0.1,::1',
            'NO_PROXY': 'localhost,.localhost,127.0.0.1,::1'}


def connect(arguments):
    if arguments[:1] in (['-h'], ['--help']):
        print('Usage: bash proxy_connect.sh [--print-env | -- command args...]\n'
              'No command: launch an interactive Bash shell with proxy variables.\n'
              'Only applications that honor proxy settings are affected; OS routes are unchanged.')
        return 0
    print_env = arguments == ['--print-env']
    if arguments[:1] == ['--print-env'] and not print_env:
        raise ProfileError('--print-env takes no additional arguments.')
    with control_lock(), redirect_stdout(sys.stderr):
        if not tracked():
            start()
        saved = tracked()
        if not saved:
            raise ProfileError('Client is not running.')
    variables = proxy_variables(saved['port'])
    if print_env:
        for name, value in variables.items():
            print('export ' + name + '=' + shlex.quote(value))
        return 0
    if arguments[:1] == ['--']:
        arguments = arguments[1:]
    command = arguments or ['bash', '--noprofile', '--norc', '-i']
    env = os.environ.copy()
    env.update(variables)
    print('Proxy settings applied to this command/shell. Only proxy-aware applications use them.', file=sys.stderr)
    os.execvpe(command[0], command, env)


def main():
    os.umask(0o077)
    STATE.mkdir(mode=0o700, exist_ok=True)
    STATE.chmod(0o700)
    arguments = sys.argv[1:]
    if arguments[:1] == ['connect']:
        return connect(arguments[1:])
    parser = argparse.ArgumentParser(description='Rootless proxy setup for Debian/Ubuntu. Default action: setup.')
    sub = parser.add_subparsers(dest='action', required=True)
    for action in ('setup', 'test'):
        item = sub.add_parser(action, help='test links and start the best profile' if action == 'setup' else 'test links without changing the active client')
        item.add_argument('--links', type=Path, default=ASSETS / 'links.txt', help='share-link file, JSON file, or JSON directory')
        item.add_argument('--port', type=int, default=10808)
        item.add_argument('--attempts', type=int, default=2)
        item.add_argument('--timeout', type=int, default=15)
        item.add_argument('--url', default='https://example.com/')
        item.add_argument('--tag')
        item.add_argument('--ca-file')
    for action in ('start', 'stop', 'restart', 'status', 'check', 'logs', 'verify'):
        sub.add_parser(action)
    if not arguments or (arguments[0].startswith('-') and arguments[0] not in ('-h', '--help')):
        arguments.insert(0, 'setup')
    args = parser.parse_args(arguments)
    if args.action == 'status':
        saved = tracked()
        if saved:
            print(f'Running: net-client PID {saved["pid"]}. SOCKS5/HTTP: 127.0.0.1:{saved["port"]}')
            print('This confirms the local process, not upstream connectivity. Use test to check links.')
        else:
            print('No matching client is running.')
        return 0 if saved else 1
    if args.action == 'logs':
        if LOG.exists():
            # Seek near the end instead of loading an arbitrarily large log.
            with LOG.open('rb') as handle:
                handle.seek(max(0, LOG.stat().st_size - 65536))
                print('\n'.join(handle.read().decode('utf-8', errors='replace').splitlines()[-60:]))
        else:
            print('No client log yet.')
        return 0
    if args.action == 'test':
        return check_links(args)
    with control_lock():
        if args.action == 'setup':
            return setup(args)
        if args.action == 'start':
            start()
        elif args.action == 'stop':
            stop()
        elif args.action == 'restart':
            verify_assets()
            validate()
            stop()
            start()
        elif args.action == 'check':
            verify_assets()
            validate()
            print('Configuration OK. This does not test the remote server.')
        elif args.action == 'verify':
            verify_assets()
            print('Bundled asset checksums OK.')
    return 0


if __name__ == '__main__':
    def interrupted(*_):
        raise KeyboardInterrupt
    signal.signal(signal.SIGTERM, interrupted)
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        print('\nInterrupted.', file=sys.stderr)
        sys.exit(130)
    except (ProfileError, OSError, ValueError, subprocess.TimeoutExpired) as exc:
        print('Error: ' + str(exc), file=sys.stderr)
        sys.exit(1)
