#!/usr/bin/env python3
"""Record a bound-source TCP or UDP/TCP DNS probe. Failure is NOT policy proof."""
import argparse
import ipaddress
import json
import secrets
import socket
import struct
import time


def read_exact(sock, count):
    out = b''
    while len(out) < count:
        b = sock.recv(count - len(out))
        if not b:
            raise EOFError('short DNS TCP response')
        out += b
    return out


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('mode', choices=['tcp', 'dns', 'udp', 'ipip'])
    p.add_argument('--source', required=True)
    p.add_argument('--destination', required=True)
    p.add_argument('--port', type=int, default=22)
    p.add_argument('--name', default='kubernetes.default.svc.cluster.local')
    p.add_argument('--dns-tcp', action='store_true')
    a = p.parse_args()
    for value in (a.source, a.destination):
        assert ipaddress.ip_address(value).version == 4
    result = {'time_epoch': int(time.time()), 'source': a.source, 'destination': a.destination,
              'mode': a.mode, 'protocol': 'TCP' if a.mode == 'tcp' or a.dns_tcp else 'UDP',
              'port': 53 if a.mode == 'dns' else a.port}
    if a.mode == 'ipip':
        result['protocol'] = 'IP4'
        result.pop('port')
        try:
            # One valid inner IPv4/UDP packet. Outer protocol 4 is supplied by the kernel.
            h = struct.pack('!BBHHHBBH4s4s', 0x45, 0, 28, 1, 0, 32, 17, 0,
                            socket.inet_aton(a.source), socket.inet_aton(a.destination))
            total = sum(struct.unpack('!10H', h))
            total = (total & 65535) + (total >> 16)
            total = (total & 65535) + (total >> 16)
            h = h[:10] + struct.pack('!H', (~total) & 65535) + h[12:]
            with socket.socket(socket.AF_INET, socket.SOCK_RAW, 4) as s:
                s.bind((a.source, 0))
                s.sendto(h + struct.pack('!4H', 49001, 9, 8, 0), (a.destination, 0))
            result['result'] = 'ONE_PACKET_SENT_NOT_A_POLICY_VERDICT'
        except Exception as exc:
            result.update(result='SEND_FAILED', error=str(exc))
        print(json.dumps(result))
        raise SystemExit(0 if result['result'].startswith('ONE_PACKET') else 2)
    typ = socket.SOCK_STREAM if result['protocol'] == 'TCP' else socket.SOCK_DGRAM
    try:
        with socket.socket(socket.AF_INET, typ) as s:
            s.settimeout(5)
            s.bind((a.source, 0))
            s.connect((a.destination, result['port']))
            result['local_socket'] = list(s.getsockname())
            if a.mode == 'udp':
                s.send(b'FDS_GATE2_NEGATIVE_PROBE')
                result['result'] = 'ONE_PACKET_SENT_NOT_A_POLICY_VERDICT'
            elif a.mode == 'tcp':
                result['result'] = 'TCP_CONNECTED'
            else:
                ident = secrets.randbelow(65536)
                labels = a.name.rstrip('.').encode('idna').split(b'.')
                assert all(0 < len(x) <= 63 for x in labels)
                q = struct.pack('!6H', ident, 0x0100, 1, 0, 0, 0)
                q += b''.join(bytes([len(x)]) + x for x in labels) + b'\0' + struct.pack('!HH', 1, 1)
                if a.dns_tcp:
                    s.sendall(struct.pack('!H', len(q)) + q)
                    answer = read_exact(s, struct.unpack('!H', read_exact(s, 2))[0])
                else:
                    s.send(q)
                    answer = s.recv(4096)
                rxid, flags, questions, answers, _, _ = struct.unpack('!6H', answer[:12])
                assert rxid == ident and flags & 0x8000, 'unexpected DNS response'
                result.update(rcode=flags & 15, answer_count=answers, truncated=bool(flags & 0x0200))
                result['result'] = 'DNS_RESPONSE'
    except Exception as exc:
        result.update(result='CONNECTION_OR_QUERY_FAILED', error=type(exc).__name__ + ': ' + str(exc))
    print(json.dumps(result))
    # Always require explicit interpretation of the result and packet/counter evidence.
    if result['result'] == 'CONNECTION_OR_QUERY_FAILED':
        raise SystemExit(2)


if __name__ == '__main__':
    main()
