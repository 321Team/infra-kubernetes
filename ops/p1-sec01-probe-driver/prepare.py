#!/usr/bin/env python3
"""Offline build from exact Git blobs, including driver from the SAME commit."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess

PREFIX = 'ops/p1-sec01-isolated-tests/'
DRIVER = 'ops/p1-sec01-probe-driver/driver.py'
NAMES = ('probe.sh', 'backup.sh', 'common.sh', 'checks.py')


def git(repo, *args):
    return subprocess.check_output(['git', '-C', str(repo), *args], timeout=30)


def build(repo, commit, output):
    if not re.fullmatch(r'[0-9a-f]{40}', commit):
        raise ValueError('full commit SHA required')
    if git(repo, 'rev-parse', commit + '^{commit}').decode().strip() != commit:
        raise ValueError('commit mismatch')
    if output.exists():
        raise ValueError('output already exists')
    source_manifest = dict((name, sha) for sha, name in
                           (line.split('  ', 1) for line in git(repo, 'show', commit + ':' + PREFIX + 'SHA256SUMS').decode().splitlines()))
    payload = {}
    for name in NAMES:
        raw = git(repo, 'show', commit + ':' + PREFIX + name)
        if hashlib.sha256(raw).hexdigest() != source_manifest[name]:
            raise ValueError('source manifest mismatch')
        payload['source/' + name] = raw
    payload['driver.py'] = git(repo, 'show', commit + ':' + DRIVER)
    meta = {'schema': 'fds.sec01.probe-driver.v1', 'commit': commit,
            'files': {name: hashlib.sha256(data).hexdigest() for name, data in sorted(payload.items())}}
    raw_meta = (json.dumps(meta, sort_keys=True, indent=2) + '\n').encode()
    os.umask(0o077)
    output.mkdir(mode=0o700)
    (output / 'source').mkdir(mode=0o700)
    for name, raw in payload.items():
        (output / name).write_bytes(raw)
    (output / 'BUNDLE.json').write_bytes(raw_meta)
    return {'bundle': str(output), 'source_commit': commit,
            'bundle_sha256': hashlib.sha256(raw_meta).hexdigest(),
            'GO': 'NOT_VERIFIED', 'runtime': 'NOT_RUN'}


if __name__ == '__main__':
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('repo', type=Path); p.add_argument('commit'); p.add_argument('output', type=Path)
    a = p.parse_args()
    print(json.dumps(build(a.repo, a.commit, a.output)))
