"""Conservative ss:// and vless:// import. Unknown transport options are rejected."""
import base64
from urllib.parse import parse_qsl, unquote, urlsplit


class UnsupportedLink(ValueError):
    pass


def decode64(value):
    try:
        raw = value.encode('ascii')
        return base64.b64decode(raw + b'=' * (-len(raw) % 4), altchars=b'-_', validate=True).decode('utf-8')
    except (ValueError, UnicodeError):
        raise UnsupportedLink('Invalid base64 encoding.') from None


def query_fields(query):
    result = {}
    for key, value in parse_qsl(query, keep_blank_values=True):
        if key in result:
            raise UnsupportedLink('Duplicate query parameters.')
        result[key] = value
    return result


def reject_remaining(params):
    if params:
        raise UnsupportedLink('Unsupported parameters: ' + ', '.join(sorted(params)) + '. Export a full Xray JSON profile instead.')


def import_link(link):
    parsed = urlsplit(link)
    scheme = parsed.scheme.lower()
    label = unquote(parsed.fragment) or scheme or 'profile'
    params = query_fields(parsed.query)
    if scheme == 'ss':
        payload = link[5:].split('#', 1)[0].split('?', 1)[0].rstrip('/')
        if '@' not in payload:
            payload = decode64(unquote(payload))
        if '@' not in payload:
            raise UnsupportedLink('Missing Shadowsocks endpoint.')
        userinfo, endpoint = payload.rsplit('@', 1)
        userinfo = unquote(userinfo)
        if ':' not in userinfo:
            userinfo = decode64(userinfo)
        if ':' not in userinfo:
            raise UnsupportedLink('Missing Shadowsocks method/password.')
        method, password = userinfo.split(':', 1)
        if not method or not password:
            raise UnsupportedLink('Empty Shadowsocks method/password.')
        address = urlsplit('ss://' + endpoint)
        if params.get('plugin'):
            raise UnsupportedLink('Shadowsocks plugins are not imported; export a compatible full JSON profile.')
        params.pop('plugin', None)
        reject_remaining(params)
        if not address.hostname or not address.port:
            raise UnsupportedLink('Missing Shadowsocks host/port.')
        outbound = {'tag': 'proxy', 'protocol': 'shadowsocks', 'settings': {'servers': [{
            'address': address.hostname, 'port': address.port, 'method': method, 'password': password,
        }]}}
    elif scheme == 'vless':
        if not parsed.hostname or not parsed.port or not parsed.username or parsed.password:
            raise UnsupportedLink('Invalid VLESS host/port/user ID.')
        if parsed.path not in ('', '/'):
            raise UnsupportedLink('VLESS path must be encoded in the path query parameter.')
        user = {'id': unquote(parsed.username), 'encryption': params.pop('encryption', 'none')}
        flow = params.pop('flow', '')
        if flow:
            user['flow'] = flow
        transport = params.pop('type', 'tcp')
        if transport == 'tcp':
            transport = 'raw'
        if transport not in ('raw', 'ws', 'grpc'):
            raise UnsupportedLink('This link transport requires a full Xray JSON export (supported links: tcp/raw, ws, grpc).')
        security = params.pop('security', 'none')
        if security not in ('none', 'tls', 'reality'):
            raise UnsupportedLink('Unsupported security layer; export a full Xray JSON profile.')
        header = params.pop('headerType', 'none')
        if header != 'none':
            raise UnsupportedLink('TCP header camouflage requires a full JSON export.')
        stream = {'network': transport, 'security': security}
        if transport == 'ws':
            stream['wsSettings'] = {'path': params.pop('path', '/')}
            host = params.pop('host', '')
            if host:
                stream['wsSettings']['headers'] = {'Host': host}
        elif transport == 'grpc':
            mode = params.pop('mode', 'gun')
            if mode not in ('gun', 'multi'):
                raise UnsupportedLink('This gRPC mode requires a full JSON export.')
            stream['grpcSettings'] = {
                'serviceName': params.pop('serviceName', ''), 'multiMode': mode == 'multi',
            }
            authority = params.pop('authority', '')
            if authority:
                stream['grpcSettings']['authority'] = authority
        if security == 'reality':
            if transport == 'ws':
                raise UnsupportedLink('REALITY with WebSocket is not supported by Xray.')
            password = params.pop('pbk', '')
            if not password:
                raise UnsupportedLink('REALITY link is missing pbk.')
            reality = {
                'serverName': params.pop('sni', parsed.hostname),
                'fingerprint': params.pop('fp', 'chrome'), 'password': password,
                'shortId': params.pop('sid', ''), 'spiderX': params.pop('spx', '/'),
            }
            pqv = params.pop('pqv', '')
            if pqv:
                reality['mldsa65Verify'] = pqv
            stream['realitySettings'] = reality
        elif security == 'tls':
            tls = {'serverName': params.pop('sni', parsed.hostname), 'fingerprint': params.pop('fp', 'chrome')}
            alpn = params.pop('alpn', '')
            if alpn:
                tls['alpn'] = alpn.split(',')
            for param, setting in [('ech', 'echConfigList'), ('pcs', 'pinnedPeerCertSha256'), ('vcn', 'verifyPeerCertByName')]:
                value = params.pop(param, '')
                if value:
                    tls[setting] = value
            for key in ('allowInsecure', 'insecure'):
                if params.pop(key, '0').lower() not in ('0', 'false', ''):
                    raise UnsupportedLink('Insecure TLS options require a reviewed full JSON export.')
            stream['tlsSettings'] = tls
        reject_remaining(params)
        outbound = {'tag': 'proxy', 'protocol': 'vless', 'settings': {'vnext': [{
            'address': parsed.hostname, 'port': parsed.port, 'users': [user],
        }]}, 'streamSettings': stream}
    else:
        raise UnsupportedLink('Supported share links: ss:// and vless://. Other protocols require a full Xray JSON export.')
    return label, {
        'log': {'loglevel': 'warning'},
        'inbounds': [{'tag': 'local', 'listen': '127.0.0.1', 'port': 10808,
                      'protocol': 'mixed', 'settings': {'auth': 'noauth', 'udp': False}}],
        'outbounds': [outbound],
    }
