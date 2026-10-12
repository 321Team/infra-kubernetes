"""Real sockets and TLS against a SYNTHETIC loopback server. No cluster, no real credential."""
import hashlib
import json
import os
from pathlib import Path
import socket
import ssl
import sys
import tempfile
import time
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parent))
import fake_api as f
import coordinator as c
import decision as d
import transport as t

TOKEN = 'SYNTHETIC-not-a-credential'


class FullRunTests(unittest.TestCase):
    def test_ci_does_not_silently_skip_the_integration_tests(self):
        if os.environ.get('IF06B_REQUIRE_FULL') == '1':
            self.assertEqual(sys.platform, 'linux')
            self.assertIsNotNone(f.OPENSSL)


@unittest.skipUnless(f.OPENSSL, 'openssl CLI generates the throwaway test PKI')
class TransportTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory()
        cls.pki = f.make_pki(cls.temp.name)
        cls.api = f.FakeApi(cls.pki)

    @classmethod
    def tearDownClass(cls):
        cls.api.stop()
        cls.temp.cleanup()

    def setUp(self):
        self.api.requests.clear()
        self.api.script.clear()
        self.api.object = None

    def make(self, **changes):
        kwargs = dict(connect_ip='127.0.0.1', port=self.api.port, tls_server_name='127.0.0.1',
                      context=f.client_context(self.pki / 'ca.crt'), clock=time.monotonic,
                      allowed=t.SESSION_REQUESTS, token=TOKEN)
        kwargs.update(changes)
        return t.ApiTransport(**kwargs)

    def call(self, transport, method='GET', path=d.PATH, body=None, budget=5):
        return transport(method=method, path=path, body=body, deadline_mono=time.monotonic() + budget)

    def test_get_is_one_request_with_status_and_body(self):
        transport = self.make()
        status, raw = self.call(transport)
        self.assertEqual((status, json.loads(raw)['reason']), (404, 'NotFound'))
        self.assertEqual(self.api.methods(), [('GET', d.PATH)])
        self.assertEqual(self.api.requests[0]['connection'], 'close')
        self.assertEqual(transport.last, dict(method='GET', stage='DONE', http_status=404))

    def test_dns_san_is_verified_while_connecting_to_the_ip(self):
        self.assertEqual(self.call(self.make(tls_server_name=f.SERVER_NAME))[0], 404)

    def test_post_sends_canonical_body_and_bearer_token_once(self):
        manifest = dict(apiVersion=d.API, kind='GlobalNetworkPolicy', metadata=dict(name=d.NAME),
                        spec=dict(selector='synthetic == "test"', types=['Egress']))
        status, raw = self.call(self.make(), 'POST', c.COLLECTION, manifest)
        self.assertEqual(status, 201)
        self.assertTrue(json.loads(raw)['metadata']['uid'])
        sent = self.api.requests[0]
        self.assertEqual(sent['authorization'], 'Bearer ' + TOKEN)
        self.assertEqual(hashlib.sha256(sent['body']).hexdigest(), c.manifest_hash(manifest))
        self.assertEqual(len(self.api.requests), 1)

    def test_redirect_is_returned_and_never_followed(self):
        self.api.script.append(lambda h, _b: f.reply(h, 307, {'kind': 'Status'}, headers=[('Location', d.PATH)]))
        self.assertEqual(self.call(self.make(), 'DELETE', d.PATH, {'kind': 'DeleteOptions'})[0], 307)
        self.assertEqual(self.api.methods(), [('DELETE', d.PATH)])

    def test_server_error_is_not_retried(self):
        self.api.script.append(lambda h, _b: f.reply(h, 503, {'kind': 'Status'}))
        self.assertEqual(self.call(self.make())[0], 503)
        self.assertEqual(len(self.api.requests), 1)

    def test_chunked_response_is_decoded(self):
        self.api.script.append(lambda h, _b: f.reply(h, 200, {'kind': 'Status', 'pad': 'x' * 300}, chunked=True))
        status, raw = self.call(self.make())
        self.assertEqual((status, len(json.loads(raw)['pad'])), (200, 300))

    def test_untrusted_ca_or_wrong_name_sends_no_request(self):
        for changes in (dict(context=f.client_context(self.pki / 'other-ca.crt')),
                        dict(tls_server_name='other.invalid'), dict(tls_server_name='127.0.0.2')):
            transport = self.make(**changes)
            with self.subTest(changes=sorted(changes)), self.assertRaises(ssl.SSLError):
                self.call(transport, 'POST', c.COLLECTION, {'kind': 'GlobalNetworkPolicy'})
            self.assertEqual(transport.last['stage'], 'TLS')
        self.assertEqual(self.api.requests, [])

    def test_endpoint_must_be_an_ip_literal_with_verifying_context(self):
        for changes in (dict(connect_ip='localhost'), dict(connect_ip=f.SERVER_NAME), dict(port=0),
                        dict(port='443'), dict(tls_server_name='bad name'), dict(connect_ip=None)):
            with self.subTest(changes=changes), self.assertRaisesRegex(t.TransportError, '^ENDPOINT_INVALID$'):
                self.make(**changes)
        loose = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        loose.check_hostname = False
        loose.verify_mode = ssl.CERT_NONE
        with self.assertRaisesRegex(t.TransportError, '^TLS_VERIFICATION_REQUIRED$'):
            self.make(context=loose)
        with self.assertRaisesRegex(t.TransportError, '^CREDENTIAL_INVALID$'):
            self.make(token='two words\r\nX-Injected: 1')
        with self.assertRaisesRegex(t.TransportError, '^INPUT_INVALID$'):
            self.make(allowed=frozenset({('GET', '/')}))

    def test_request_outside_the_fixed_set_is_not_sent(self):
        transport = self.make()
        for method, path in (('GET', '/api/v1/secrets'), ('PUT', d.PATH), ('DELETE', c.COLLECTION),
                             ('POST', t.DRY_RUN), ('GET', d.PATH + '?watch=1')):
            with self.subTest(method=method, path=path), self.assertRaisesRegex(t.TransportError, '^REQUEST_NOT_ALLOWED$'):
                self.call(transport, method, path)
        self.assertEqual(transport.last['stage'], 'REJECTED')
        with self.assertRaisesRegex(t.TransportError, '^REQUEST_NOT_ALLOWED$'):
            self.call(self.make(allowed=t.PREFLIGHT_REQUESTS), 'DELETE', d.PATH)
        self.assertEqual(self.api.requests, [])

    def test_expired_or_invalid_deadline_does_not_connect(self):
        transport = self.make()
        with self.assertRaises(TimeoutError):
            self.call(transport, budget=-1)
        with self.assertRaisesRegex(t.TransportError, '^DEADLINE_INVALID$'):
            transport(method='GET', path=d.PATH, body=None, deadline_mono=float('inf'))
        self.assertEqual(transport.last['stage'], 'REJECTED')
        self.assertEqual(self.api.requests, [])

    def test_stalled_response_stops_at_the_absolute_deadline(self):
        self.api.script.append(lambda h, _b: (time.sleep(3), f.reply(h, 200, {'kind': 'Status'})))
        transport, started = self.make(), time.monotonic()
        with self.assertRaises(TimeoutError):
            self.call(transport, 'DELETE', d.PATH, {'kind': 'DeleteOptions'}, budget=0.5)
        self.assertLess(time.monotonic() - started, 2)
        self.assertEqual(transport.last, dict(method='DELETE', stage='READ', http_status=None))
        self.assertEqual(len(self.api.requests), 1)  # Reached the server: outcome is uncertain.

    def test_slow_drip_cannot_extend_the_deadline(self):
        def drip(handler, _body):
            handler.send_response(200)
            handler.send_header('Content-Length', '40')
            handler.end_headers()
            for _ in range(40):  # Each byte arrives well inside any per-read timeout.
                handler.wfile.write(b' ')
                handler.wfile.flush()
                time.sleep(0.1)
        self.api.script.append(drip)
        started = time.monotonic()
        with self.assertRaises(TimeoutError):
            self.call(self.make(), budget=0.6)
        self.assertLess(time.monotonic() - started, 2)

    def test_oversized_or_truncated_body_is_an_error(self):
        self.api.script.append(lambda h, _b: f.reply(h, 200, b' ' * (c.MAX_RESPONSE + 1)))
        with self.assertRaisesRegex(t.TransportError, '^RESPONSE_TOO_LARGE$'):
            self.call(self.make())

        def short(handler, _body):
            handler.send_response(200)
            handler.send_header('Content-Length', '50')
            handler.end_headers()
            handler.wfile.write(b'{"kind":')
        self.api.script.append(short)
        with self.assertRaises(Exception):
            self.call(self.make())

    def test_refused_connection_has_no_response(self):
        probe = socket.socket()
        probe.bind(('127.0.0.1', 0))
        port = probe.getsockname()[1]
        probe.close()
        transport = self.make(port=port)
        with self.assertRaises(OSError):
            self.call(transport, budget=2)
        self.assertEqual(transport.last, dict(method='GET', stage='CONNECT', http_status=None))

    def test_decoder_accepts_real_responses(self):
        status, body = c.decode_response(self.call(self.make(), 'GET', t.NAMESPACE))
        self.assertEqual((status, body['metadata']['uid']), (200, f.CLUSTER_UID))


@unittest.skipUnless(f.OPENSSL and sys.platform == 'linux', 'Linux private-file checks and /proc descriptors')
class BuildTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory()
        cls.pki = f.make_pki(cls.temp.name)
        os.chmod(cls.pki / 'client.key', 0o600)
        cls.api = f.FakeApi(cls.pki, require_client=True)

    @classmethod
    def tearDownClass(cls):
        cls.api.stop()
        cls.temp.cleanup()

    def setUp(self):
        self.api.requests.clear()
        self.token = self.pki / 'token'
        self.token.write_text(TOKEN + '\n')
        os.chmod(self.token, 0o600)
        ca = self.pki / 'ca.crt'
        self.endpoint = dict(connect_ip='127.0.0.1', port=self.api.port, tls_server_name='127.0.0.1',
                             ca_file=str(ca), ca_sha256=hashlib.sha256(ca.read_bytes()).hexdigest())
        self.cert = dict(kind='client_cert_files', cert_file=str(self.pki / 'client.crt'),
                         key_file=str(self.pki / 'client.key'))
        self.bearer = dict(kind='bearer_token_file', token_file=str(self.token))

    def build(self, credential, **endpoint):
        return t.build(dict(self.endpoint, **endpoint), credential, clock=time.monotonic,
                       allowed=t.SESSION_REQUESTS)

    def get(self, transport):
        return transport(method='GET', path=d.PATH, body=None, deadline_mono=time.monotonic() + 5)

    def test_client_certificate_authenticates(self):
        self.assertEqual(self.get(self.build(self.cert))[0], 404)
        self.assertIsNone(self.api.requests[0]['authorization'])

    def test_missing_client_certificate_gets_no_response(self):
        with self.assertRaises(Exception):
            self.get(self.build(self.bearer))
        self.assertEqual(self.api.requests, [])

    def test_ca_pin_must_match_before_anything_is_sent(self):
        with self.assertRaisesRegex(t.TransportError, '^CA_PIN_MISMATCH$'):
            self.build(self.cert, ca_sha256='0' * 64)
        with self.assertRaisesRegex(t.TransportError, '^CA_INVALID$'):
            junk = self.pki / 'junk.crt'
            junk.write_text('not a certificate\n')
            self.build(self.cert, ca_file=str(junk), ca_sha256=hashlib.sha256(junk.read_bytes()).hexdigest())

    def test_credential_files_must_be_private_regular_and_absolute(self):
        os.chmod(self.token, 0o644)
        with self.assertRaisesRegex(t.TransportError, '^FILE_INVALID$'):
            self.build(self.bearer)
        os.chmod(self.token, 0o600)
        link = self.pki / 'token-link'
        if not link.is_symlink():
            link.symlink_to(self.token)
        with self.assertRaises(OSError):
            self.build(dict(self.bearer, token_file=str(link)))
        with self.assertRaisesRegex(t.TransportError, '^FILE_INVALID$'):
            self.build(dict(self.bearer, token_file='token'))
        self.token.write_text('two words')
        with self.assertRaisesRegex(t.TransportError, '^CREDENTIAL_INVALID$'):
            self.build(self.bearer)
        with self.assertRaisesRegex(t.TransportError, '^CREDENTIAL_INVALID$'):
            self.build(dict(kind='client_cert_files', cert_file=str(self.pki / 'ca.crt'),
                            key_file=str(self.pki / 'client.key')))
        self.assertEqual(self.api.requests, [])


if __name__ == '__main__':
    unittest.main()
