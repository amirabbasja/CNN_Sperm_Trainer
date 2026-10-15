import base64
import copy
import unittest
from check_profiles import make_test_config, ProfileError
from share_links import import_link, UnsupportedLink


def b64(value):
    return base64.urlsafe_b64encode(value.encode()).decode().rstrip('=')


class ImportTests(unittest.TestCase):
    def test_ss_modern_and_legacy_preserve_credentials(self):
        secret = 'test:p+a/ss='
        links = [
            'ss://' + b64('chacha20-ietf-poly1305:' + secret) + '@127.0.0.1:12345#Test',
            'ss://' + b64('chacha20-ietf-poly1305:' + secret + '@127.0.0.1:12345') + '#Test',
        ]
        for link in links:
            label, config = import_link(link)
            s = config['outbounds'][0]['settings']['servers'][0]
            self.assertEqual(s['password'], secret)
            self.assertEqual(s['address'], '127.0.0.1')
            self.assertEqual(label, 'Test')

    def test_percent_encoded_ss_ipv6(self):
        _, config = import_link('ss://chacha20-ietf-poly1305:p%2Ba%3Ass@[::1]:12345')
        s = config['outbounds'][0]['settings']['servers'][0]
        self.assertEqual(s['password'], 'p+a:ss')
        self.assertEqual(s['address'], '::1')

    def test_vless_reality(self):
        _, config = import_link('vless://d342d11e-d424-4583-b36e-524ab1f0afa4@127.0.0.1:443?type=tcp&security=reality&flow=xtls-rprx-vision&fp=chrome&sni=example.com&pbk=testkey&sid=aabb&spx=%2Fhello')
        stream = config['outbounds'][0]['streamSettings']
        self.assertEqual(stream['network'], 'raw')
        self.assertEqual(stream['realitySettings']['password'], 'testkey')
        self.assertEqual(stream['realitySettings']['spiderX'], '/hello')

    def test_vless_websocket_tls(self):
        _, config = import_link('vless://d342d11e-d424-4583-b36e-524ab1f0afa4@example.com:443?type=ws&security=tls&path=%2Fws%3Fa%3D1&host=cdn.example.com&sni=example.com&alpn=http%2F1.1')
        stream = config['outbounds'][0]['streamSettings']
        self.assertEqual(stream['wsSettings']['path'], '/ws?a=1')
        self.assertEqual(stream['tlsSettings']['alpn'], ['http/1.1'])

    def test_vless_grpc(self):
        _, config = import_link('vless://d342d11e-d424-4583-b36e-524ab1f0afa4@example.com:443?type=grpc&security=tls&serviceName=example&mode=multi&authority=example.com')
        self.assertTrue(config['outbounds'][0]['streamSettings']['grpcSettings']['multiMode'])

    def test_unsupported_options_not_ignored(self):
        for link in [
            'ss://' + b64('chacha20-ietf-poly1305:test') + '@127.0.0.1:12345?plugin=obfs-local',
            'vless://id@example.com:443?type=tcp&security=tls&security=none',
            'vless://id@example.com:443?type=xhttp&security=tls',
            'vless://id@example.com:443?type=ws&security=reality&pbk=x',
        ]:
            with self.assertRaises((UnsupportedLink, ValueError)):
                import_link(link)

    def test_dns_preserved_and_direct_route_overridden_for_test(self):
        _, source = import_link('ss://' + b64('chacha20-ietf-poly1305:test') + '@127.0.0.1:12345')
        source['dns'] = {'hosts': {'proxy.example': '192.0.2.1'}, 'servers': ['localhost']}
        source['routing'] = {'rules': [{'type': 'field', 'outboundTag': 'direct', 'network': 'tcp'}]}
        source['outbounds'].append({'tag': 'direct', 'protocol': 'freedom'})
        before = copy.deepcopy(source)
        result = make_test_config(source, 23456)
        self.assertEqual(result['dns'], source['dns'])
        self.assertEqual(result['routing']['rules'][0]['outboundTag'], 'proxy')
        self.assertEqual(result['inbounds'][0]['listen'], '127.0.0.1')
        self.assertEqual(result['inbounds'][0]['port'], 23456)
        self.assertEqual(source, before)

    def test_ambiguous_outbounds_rejected(self):
        source = {'outbounds': [{'tag': 'a', 'protocol': 'socks'}, {'tag': 'b', 'protocol': 'socks'}]}
        with self.assertRaises(ProfileError):
            make_test_config(source, 23456)


if __name__ == '__main__':
    unittest.main()
