"""One-attempt HTTPS transport for the IF06B coordinator; no retry/redirect/DNS.

It connects only to a reviewed IP literal, authenticates the server against a
pinned CA and reviewed name, and bounds connect/TLS/write/read by one absolute
deadline on the caller's clock. A redirect is returned as its status, never
followed. Errors carry fixed codes; endpoint, credential and body text stay out.
"""
import hashlib
import http.client
import io
import ipaddress
import json
import os
import re
import socket
import ssl
import stat

import coordinator as c
import decision as d

NAMESPACE = '/api/v1/namespaces/kube-system'
ACCESS_REVIEW = '/apis/authorization.k8s.io/v1/selfsubjectaccessreviews'
DRY_RUN = c.COLLECTION + '?dryRun=All'
SESSION_REQUESTS = frozenset({('POST', c.COLLECTION), ('GET', d.PATH), ('DELETE', d.PATH),
                              ('GET', NAMESPACE)})
PREFLIGHT_REQUESTS = frozenset({('GET', d.PATH), ('GET', NAMESPACE), ('POST', DRY_RUN),
                                ('POST', ACCESS_REVIEW)})
MAX_HEAD = 65536
MAX_FILE = 1048576
TOKEN = re.compile(r'[A-Za-z0-9._~+/=-]{1,16384}\Z')


class TransportError(RuntimeError):
    """Fixed code only."""


def _need(ok, reason):
    if not ok:
        raise TransportError(reason)


class _Reader(io.RawIOBase):
    """Every receive gets only the time left before the absolute deadline."""
    def __init__(self, sock, remaining, limit):
        self._sock, self._remaining, self._left = sock, remaining, limit

    def readable(self):
        return True

    def readinto(self, buffer):
        self._sock.settimeout(self._remaining())
        count = self._sock.recv_into(buffer, min(len(buffer), self._left + 1))
        self._left -= count
        _need(self._left >= 0, 'RESPONSE_TOO_LARGE')
        return count


class _Stream:
    def __init__(self, raw):
        self._raw = raw

    def makefile(self, *_args, **_kwargs):
        return io.BufferedReader(self._raw)


def _encode(method, path, host, body, token):
    lines = [method + ' ' + path + ' HTTP/1.1', 'Host: ' + host, 'Accept: application/json',
             'Connection: close', 'User-Agent: fds-if06b-guardian']
    data = b''
    if body is not None:
        data = json.dumps(body, sort_keys=True, separators=(',', ':'),
                          ensure_ascii=False, allow_nan=False).encode('utf-8')
        lines += ['Content-Type: application/json', 'Content-Length: %d' % len(data)]
    if token is not None:
        lines.append('Authorization: Bearer ' + token)
    return ('\r\n'.join(lines) + '\r\n\r\n').encode('ascii') + data


class ApiTransport:
    """Callable for Coordinator(transport=...). `last` is a secret-free diagnostic."""
    def __init__(self, *, connect_ip, port, tls_server_name, context, clock, allowed, token=None):
        try:
            address = ipaddress.ip_address(connect_ip)
        except (ValueError, TypeError):
            # A name would need a resolver this module cannot bound or pin.
            raise TransportError('ENDPOINT_INVALID') from None
        _need(type(port) is int and 0 < port < 65536 and isinstance(tls_server_name, str) and
              re.fullmatch(r'[A-Za-z0-9.:-]{1,253}', tls_server_name), 'ENDPOINT_INVALID')
        _need(isinstance(context, ssl.SSLContext) and context.verify_mode == ssl.CERT_REQUIRED and
              context.check_hostname, 'TLS_VERIFICATION_REQUIRED')
        _need(allowed in (SESSION_REQUESTS, PREFLIGHT_REQUESTS) and callable(clock), 'INPUT_INVALID')
        _need(token is None or (isinstance(token, str) and TOKEN.match(token)), 'CREDENTIAL_INVALID')
        self._family = socket.AF_INET6 if address.version == 6 else socket.AF_INET
        self._ip, self._port, self._name = str(address), port, tls_server_name
        bracket = '[%s]' % tls_server_name if ':' in tls_server_name else tls_server_name
        self._host = '%s:%d' % (bracket, port)
        self._context, self._clock, self._allowed, self._token = context, clock, allowed, token
        self.last = dict(method=None, stage='NONE', http_status=None)

    def __call__(self, *, method, path, body, deadline_mono):
        self.last = dict(method=method if method in ('GET', 'POST', 'DELETE') else None,
                         stage='REJECTED', http_status=None)
        _need((method, path) in self._allowed, 'REQUEST_NOT_ALLOWED')
        _need(d.number(deadline_mono), 'DEADLINE_INVALID')

        def remaining():
            left = deadline_mono - self._clock()
            if not left > 0:
                raise TimeoutError
            return left
        request = _encode(method, path, self._host, body, self._token)
        remaining()
        sock = socket.socket(self._family, socket.SOCK_STREAM)
        try:
            self.last['stage'] = 'CONNECT'
            sock.settimeout(remaining())
            sock.connect((self._ip, self._port))
            self.last['stage'] = 'TLS'
            sock = self._context.wrap_socket(sock, server_hostname=self._name,
                                             do_handshake_on_connect=False, suppress_ragged_eofs=False)
            sock.settimeout(remaining())
            sock.do_handshake()
            self.last['stage'] = 'WRITE'
            pending = memoryview(request)
            while pending:
                sock.settimeout(remaining())
                pending = pending[sock.send(pending):]
            self.last['stage'] = 'READ'
            raw = _Reader(sock, remaining, MAX_HEAD + c.MAX_RESPONSE)
            response = http.client.HTTPResponse(_Stream(raw), method=method)
            response.begin()
            data = response.read(c.MAX_RESPONSE + 1)
            _need(len(data) <= c.MAX_RESPONSE, 'RESPONSE_TOO_LARGE')
            self.last.update(stage='DONE', http_status=response.status)
            return response.status, data
        finally:
            sock.close()


def _open_private(path, secret):
    """Reviewed local input: absolute, regular, no final symlink, not writable by others."""
    _need(isinstance(path, str) and os.path.isabs(path), 'FILE_INVALID')
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC | os.O_NONBLOCK)
    try:
        info = os.fstat(fd)
        _need(stat.S_ISREG(info.st_mode) and info.st_uid in (0, os.geteuid()) and
              not stat.S_IMODE(info.st_mode) & (0o077 if secret else 0o022) and
              0 < info.st_size <= MAX_FILE, 'FILE_INVALID')
    except BaseException:
        os.close(fd)
        raise
    return fd


def read_private(path, *, secret):
    fd = _open_private(path, secret)
    try:
        data = bytearray()
        while len(data) <= MAX_FILE:
            chunk = os.read(fd, MAX_FILE + 1 - len(data))
            if not chunk:
                break
            data.extend(chunk)
        _need(0 < len(data) <= MAX_FILE, 'FILE_INVALID')
        return bytes(data)
    finally:
        os.close(fd)


def build(api, credential, *, clock, allowed):
    """Bind reviewed endpoint, pinned CA and one existing credential. Sends nothing."""
    authority = read_private(api['ca_file'], secret=False)
    _need(hashlib.sha256(authority).hexdigest() == api['ca_sha256'], 'CA_PIN_MISMATCH')
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)  # No system trust store is loaded.
    context.minimum_version = ssl.TLSVersion.TLSv1_2
    try:
        context.load_verify_locations(cadata=authority.decode('ascii'))
    except (ssl.SSLError, ValueError):
        raise TransportError('CA_INVALID') from None
    token = None
    if credential['kind'] == 'bearer_token_file':
        try:
            token = read_private(credential['token_file'], secret=True).decode('ascii').strip()
        except UnicodeError:
            raise TransportError('CREDENTIAL_INVALID') from None
    else:
        _need(credential['kind'] == 'client_cert_files', 'CREDENTIAL_INVALID')
        cert = key = None
        try:
            cert = _open_private(credential['cert_file'], False)
            key = _open_private(credential['key_file'], True)
            # Load the descriptors that were just checked, not the paths again.
            context.load_cert_chain('/proc/self/fd/%d' % cert, '/proc/self/fd/%d' % key)
        except (ssl.SSLError, ValueError):
            raise TransportError('CREDENTIAL_INVALID') from None
        finally:
            for fd in (cert, key):
                if fd is not None:
                    os.close(fd)
    return ApiTransport(connect_ip=api['connect_ip'], port=api['port'],
                        tls_server_name=api['tls_server_name'], context=context,
                        clock=clock, allowed=allowed, token=token)
