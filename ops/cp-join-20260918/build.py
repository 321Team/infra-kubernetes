#!/usr/bin/env python3
"""Deterministic one-file delivery; no runtime network dependencies for helpers."""
import base64
import hashlib
import json
from pathlib import Path
import zlib

ROOT = Path(__file__).resolve().parent
NAMES = ('cp_join_cluster.py', 'cp_join_node.py', 'cp1_join_handoff.py')
payloads = {name: base64.b64encode(zlib.compress((ROOT / name).read_bytes(), 9)).decode() for name in NAMES}
contract = json.loads((ROOT / 'CONTRACT.json').read_text())
source = (ROOT / 'controller_template.py').read_text()
source = source.replace('PAYLOADS = {}  # REPLACED_BY_BUILD', 'PAYLOADS = ' + repr(payloads))
source = source.replace('CONTRACT = {}  # REPLACED_BY_BUILD', 'CONTRACT = ' + repr(contract))
(ROOT / 'RUN_CP_JOIN.py').write_text(source)
raw = source.encode()
(ROOT / 'RUN_CP_JOIN.py.sha256').write_text(hashlib.sha256(raw).hexdigest() + '  RUN_CP_JOIN.py\n')
print(json.dumps({'file': 'RUN_CP_JOIN.py', 'bytes': len(raw), 'sha256': hashlib.sha256(raw).hexdigest()}))
