"""SYNTHETIC loopback TLS stand-in for the API server. Never a real cluster.

Certificates and keys are generated per test run into a temporary directory
and discarded. All identifiers are synthetic.
"""
import http.server
import json
from pathlib import Path
import shutil
import ssl
import subprocess
import sys
import threading

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import coordinator as c
import decision as d
import transport as t

OPENSSL = shutil.which('openssl')
SERVER_NAME = 'synthetic-api.invalid'
CLUSTER_UID = 'SYNTHETIC-cluster'
NOT_FOUND = dict(apiVersion='v1', kind='Status', status='Failure', reason='NotFound', code=404,
                 details=dict(name=d.NAME, group=d.GROUP, kind=d.RESOURCE))
EC = ['-newkey', 'ec', '-pkeyopt', 'ec_paramgen_curve:prime256v1', '-nodes']


def make_pki(directory):
    """ca/other-ca (unrelated), server (IP 127.0.0.1 + DNS SAN) and client certificates."""
    directory = Path(directory)

    def run(*args):
        subprocess.run([OPENSSL, *args], check=True, capture_output=True, cwd=directory)
    for name in ('ca', 'other-ca'):
        (directory / (name + '.cnf')).write_text(
            '[req]\ndistinguished_name=dn\nx509_extensions=v3\nprompt=no\n[dn]\nCN=SYNTHETIC IF06B %s\n'
            '[v3]\nbasicConstraints=critical,CA:TRUE\nkeyUsage=critical,keyCertSign,cRLSign\n'
            'subjectKeyIdentifier=hash\n' % name)
        run('req', '-x509', '-config', name + '.cnf', *EC, '-keyout', name + '.key', '-out', name + '.crt',
            '-days', '2')
    extensions = dict(server='subjectAltName=IP:127.0.0.1,DNS:%s\nextendedKeyUsage=serverAuth\n' % SERVER_NAME,
                      client='extendedKeyUsage=clientAuth\n')
    for name, extension in extensions.items():
        (directory / (name + '.cnf')).write_text(
            '[req]\ndistinguished_name=dn\nprompt=no\n[dn]\nCN=SYNTHETIC-%s\n' % name)
        (directory / (name + '.ext')).write_text(
            extension + 'authorityKeyIdentifier=keyid\nsubjectKeyIdentifier=hash\n')
        run('req', '-new', '-config', name + '.cnf', *EC, '-keyout', name + '.key', '-out', name + '.csr')
        run('x509', '-req', '-in', name + '.csr', '-CA', 'ca.crt', '-CAkey', 'ca.key', '-CAcreateserial',
            '-out', name + '.crt', '-days', '2', '-extfile', name + '.ext')
    return directory


def client_context(ca_file):
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    context.load_verify_locations(cafile=str(ca_file))
    return context


def reply(handler, status, body, *, headers=(), chunked=False):
    raw = body if isinstance(body, bytes) else json.dumps(body).encode()
    handler.send_response(status)
    handler.send_header('Content-Type', 'application/json')
    for key, value in headers:
        handler.send_header(key, value)
    if chunked:
        handler.send_header('Transfer-Encoding', 'chunked')
        handler.end_headers()
        half = len(raw) // 2
        for part in (raw[:half], raw[half:]):
            handler.wfile.write(b'%x\r\n%s\r\n' % (len(part), part))
        handler.wfile.write(b'0\r\n\r\n')
    else:
        handler.send_header('Content-Length', str(len(raw)))
        handler.end_headers()
        handler.wfile.write(raw)


class _Handler(http.server.BaseHTTPRequestHandler):
    protocol_version = 'HTTP/1.1'

    def setup(self):
        self.request = self.server.tls.wrap_socket(self.request, server_side=True)
        super().setup()

    def finish(self):
        try:
            super().finish()
        finally:
            self.request.close()  # The server only closes the socket it accepted.

    def log_message(self, *_args):
        pass

    def _any(self):
        api = self.server.api
        length = int(self.headers.get('Content-Length') or 0)
        body = self.rfile.read(length) if length else b''
        with api.lock:
            api.requests.append(dict(method=self.command, path=self.path, body=body,
                                     authorization=self.headers.get('Authorization'),
                                     connection=self.headers.get('Connection')))
            action = api.script.pop(0) if api.script else api.default
        action(self, body)

    do_GET = do_POST = do_DELETE = do_PUT = _any


class _Server(http.server.ThreadingHTTPServer):
    daemon_threads = True
    block_on_close = False

    def handle_error(self, *_args):
        pass  # Failed handshakes and clients that left early are expected here.


class FakeApi:
    """Scripted replies first; otherwise a one-object GlobalNetworkPolicy store."""
    def __init__(self, pki, *, require_client=False):
        tls = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        tls.load_cert_chain(str(pki / 'server.crt'), str(pki / 'server.key'))
        if require_client:
            tls.verify_mode = ssl.CERT_REQUIRED
            tls.load_verify_locations(cafile=str(pki / 'ca.crt'))
        self.lock, self.requests, self.script = threading.Lock(), [], []
        self.object, self.version, self.allowed, self.cluster_uid = None, 100, True, CLUSTER_UID
        self.spec_default = {}
        self._server = _Server(('127.0.0.1', 0), _Handler)
        self._server.tls, self._server.api = tls, self
        self.port = self._server.server_address[1]
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)
        self._thread.start()

    def stop(self):
        self._server.shutdown()
        self._server.server_close()

    def methods(self):
        with self.lock:
            return [(r['method'], r['path']) for r in self.requests]

    def _stored(self, body, persist):
        self.version += 1
        stored = json.loads(body)
        stored['spec'] = dict(stored['spec'], **self.spec_default)
        stored['metadata'] = dict(stored['metadata'], uid='SYNTHETIC-uid-%d' % self.version, generation=1)
        if persist:
            stored['metadata']['resourceVersion'] = str(self.version)
            self.object = stored
        return stored

    def default(self, handler, body):
        key = (handler.command, handler.path)
        if key == ('GET', t.NAMESPACE):
            reply(handler, 200, dict(apiVersion='v1', kind='Namespace',
                                     metadata=dict(name='kube-system', uid=self.cluster_uid)))
        elif key == ('POST', t.ACCESS_REVIEW):
            reply(handler, 201, dict(kind='SelfSubjectAccessReview', status=dict(allowed=self.allowed)))
        elif key == ('POST', t.DRY_RUN):
            reply(handler, 201, self._stored(body, False))
        elif key == ('POST', c.COLLECTION):
            if self.object is not None:
                reply(handler, 409, dict(apiVersion='v1', kind='Status', reason='AlreadyExists', code=409))
            else:
                reply(handler, 201, self._stored(body, True))
        elif key == ('GET', d.PATH):
            reply(handler, *((200, self.object) if self.object is not None else (404, NOT_FOUND)))
        elif key == ('DELETE', d.PATH):
            wanted = json.loads(body).get('preconditions', {}) if body else {}
            if self.object is None:
                reply(handler, 404, NOT_FOUND)
            elif (wanted.get('uid'), wanted.get('resourceVersion')) != (
                    self.object['metadata']['uid'], self.object['metadata']['resourceVersion']):
                reply(handler, 409, dict(apiVersion='v1', kind='Status', reason='Conflict', code=409))
            else:
                self.object = None
                reply(handler, 200, dict(apiVersion='v1', kind='Status', status='Success'))
        else:
            reply(handler, 400, dict(apiVersion='v1', kind='Status', reason='BadRequest', code=400))
