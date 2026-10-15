"""A verified HTTPS request over SOCKS5, using only the Python standard library."""
import socket
import ssl
import time
from urllib.parse import quote, urlsplit


def https_probe(proxy_port, url, timeout, ca_file=None):
    """Return status and time to HTTP headers; send the hostname to the proxy."""
    started = time.monotonic()
    deadline = started + timeout
    sock = None
    code = 0

    def remaining():
        value = deadline - time.monotonic()
        if value <= 0:
            raise TimeoutError('HTTPS probe deadline reached')
        return value

    def receive(size):
        result = b''
        while len(result) < size:
            sock.settimeout(remaining())
            chunk = sock.recv(size - len(result))
            if not chunk:
                raise ConnectionError('Proxy closed the connection')
            result += chunk
        return result

    try:
        target = urlsplit(url)
        hostname = target.hostname.encode('idna')
        if not 1 <= len(hostname) <= 255:
            raise ValueError('Test hostname is too long for SOCKS5')
        port = target.port or 443
        context = ssl.create_default_context(cafile=ca_file)
        context.set_alpn_protocols(['http/1.1'])
        sock = socket.create_connection(('127.0.0.1', proxy_port), timeout=remaining())
        sock.sendall(b'\x05\x01\x00')
        if receive(2) != b'\x05\x00':
            raise ConnectionError('SOCKS5 authentication negotiation failed')
        sock.settimeout(remaining())
        sock.sendall(b'\x05\x01\x00\x03' + bytes([len(hostname)]) + hostname + port.to_bytes(2, 'big'))
        reply = receive(4)
        if reply[0] != 5 or reply[1] != 0:
            raise ConnectionError('SOCKS5 upstream connection failed (reply %d)' % reply[1])
        if reply[3] == 1:
            receive(4)
        elif reply[3] == 4:
            receive(16)
        elif reply[3] == 3:
            receive(receive(1)[0])
        else:
            raise ConnectionError('Invalid SOCKS5 response address')
        receive(2)
        sock.settimeout(remaining())
        sock = context.wrap_socket(sock, server_hostname=target.hostname)
        host = hostname.decode('ascii')
        if ':' in host:
            host = '[' + host + ']'
        if port != 443:
            host += ':' + str(port)
        path = quote(target.path or '/', safe="/%:@!$&'()*+,;=-._~")
        if target.query:
            path += '?' + quote(target.query, safe="/%?:@!$&'()*+,;=-._~")
        request = ('GET ' + path + ' HTTP/1.1\r\nHost: ' + host +
                   '\r\nUser-Agent: connection-check/1\r\nConnection: close\r\n\r\n')
        sock.settimeout(remaining())
        sock.sendall(request.encode('ascii'))
        header = b''
        while b'\r\n\r\n' not in header:
            sock.settimeout(remaining())
            chunk = sock.recv(4096)
            if not chunk:
                raise ConnectionError('HTTPS connection closed before HTTP headers')
            header += chunk
            if len(header) > 65536:
                raise ValueError('HTTP response headers exceed 64 KiB')
        fields = header.split(b'\r\n', 1)[0].split()
        if len(fields) < 2 or fields[0] not in (b'HTTP/1.0', b'HTTP/1.1'):
            raise ValueError('Invalid HTTP response')
        code = int(fields[1])
        status = 'OK' if 200 <= code < 400 else 'HTTP_' + str(code)
        detail = ''
    except (TimeoutError, socket.timeout) as exc:
        status, detail = 'TIMEOUT', str(exc)
    except ssl.SSLError as exc:
        status, detail = 'TLS_ERROR', str(exc)
    except OSError as exc:
        status, detail = 'CONNECT_ERROR', str(exc)
    except ValueError as exc:
        status, detail = 'PROBE_ERROR', str(exc)
    finally:
        if sock is not None:
            sock.close()
    return {'status': status, 'http_code': code,
            'ms': round((time.monotonic() - started) * 1000), 'detail': detail}
