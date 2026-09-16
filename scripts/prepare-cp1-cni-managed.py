#!/usr/bin/env python3
"""Read-only managed activation preparation; never starts services or policies.

check validates archived evidence offline. collect queries local CP1 as root.
Both emit bounded-input JSON to stdout; use a private operator-owned output.
Source/request hash consistency is not an independent operational approval.
"""
import argparse
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import sys
import time

# -I excludes script siblings. This executable's trusted, verified directory is
# explicit; no current-directory, PYTHONPATH or user site import is enabled.
SCRIPTS=Path(__file__).resolve().parent
sys.path.insert(0,str(SCRIPTS))
import cp1_cni_activation as a
import cp1_cni_membership as m
from cp1_cni_observer import collect, protection, parse_json
from cp1_cni_transition_io import peer_capture

MAX_BYTES=32*1024*1024


def read(path):
    with Path(path).open('rb') as stream:data=stream.read(MAX_BYTES+1)
    m.need(len(data)<=MAX_BYTES,'input exceeds size bound')
    return data


def parsed(path):return parse_json(read(path).decode('utf-8'))


def local_evidence(node, *, monotonic=time.monotonic, clock=None):
    """One 29-second budget for the full/CRI pair; no lease or state mutation."""
    m.need(os.geteuid()==0,'collect requires sudo/root; check does not')
    m.need(hashlib.sha256(Path(node.__file__).read_bytes()).hexdigest()==a.PINS['node-firewall.py'],
           'collector helper source differs')
    end=monotonic()+29
    result={'schema':'fds.cp1-cni-activation-evidence.v1','mode':'READ_ONLY','complete':False,
            'errors':[],'authorizes_apply':False}
    try:
        result['capture']=collect(node,protection,compatibility=True,delta_api=True,deadline=end,receipt_link=True)
        m.need(result['capture']['complete'] is True,'whole capture incomplete')
        result['peers']=peer_capture(node,end)
        result['clock']=(clock() if clock else {'boot_id':Path('/proc/sys/kernel/random/boot_id').read_text().strip(),
            'wall':time.time(),'boottime':time.clock_gettime(time.CLOCK_BOOTTIME),'monotonic':monotonic()})
        m.need(monotonic()<end,'combined capture deadline exhausted')
        result['complete']=True
    except (ValueError,OSError,KeyError,TypeError) as error:
        result['errors'].append({'type':type(error).__name__,'message':str(error)})
    return result


def prepare(original,original_sha,request,request_sha,manifest,source_sha,evidence):
    m.need(evidence.get('schema')=='fds.cp1-cni-activation-evidence.v1' and
        evidence.get('mode')=='READ_ONLY' and evidence.get('complete') is True and evidence.get('errors')==[] and
        evidence.get('authorizes_apply') is False, 'activation evidence incomplete')
    return a.build(original,original_sha,evidence['capture'],evidence['peers'],request,request_sha,
                   manifest,source_sha,evidence['clock'])


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('mode',choices=('check','collect'))
    for name in ('original','original-sha256','request','request-sha256','manifest','source-sha256'):
        parser.add_argument('--'+name,required=True)
    parser.add_argument('--evidence');parser.add_argument('--evidence-sha256')
    args=parser.parse_args(argv)
    if args.mode=='check' and (not args.evidence or not args.evidence_sha256):
        parser.error('check requires --evidence and --evidence-sha256')
    if args.mode=='collect' and (args.evidence or args.evidence_sha256):
        parser.error('collect accepts only fresh local evidence')
    result={'schema':'fds.cp1-cni-activation-preparation.v1','mode':'READ_ONLY',
            'status':'STOP','authorizes_apply':False,'authorizes_join':False}
    try:
        manifest=parsed(args.manifest);a.validate_manifest(manifest,args.source_sha256)
        m.need(m.same(a.source_manifest(),manifest),'source manifest missing/extra/different dependency')
        original=read(args.original);request=parsed(args.request)
        # Reject malformed original/request before any privileged command.
        a.original_receipt(original,args.original_sha256)
        m.need(m.fingerprint(request)==args.request_sha256,'independent request hash differs')
        a.request_lease(request,{key:'0'*64 for key in ('source_sha256','policy_sha256',
            'original_input_sha256','activation_sha256','fixed_state_sha256')})
        if args.mode=='check':
            raw=read(args.evidence);m.digest(args.evidence_sha256)
            m.need(hashlib.sha256(raw).hexdigest()==args.evidence_sha256,'evidence byte hash differs')
            evidence=parse_json(raw.decode('utf-8'))
        else:
            spec=importlib.util.spec_from_file_location('managed_prepare_node',SCRIPTS/'node-firewall.py')
            node=importlib.util.module_from_spec(spec);spec.loader.exec_module(node)
            evidence=local_evidence(node)
        result['evidence']=evidence
        result['activation']=prepare(original,args.original_sha256,request,args.request_sha256,
                                      manifest,args.source_sha256,evidence)
        result['status']='CHECKED_ACTIVATION_INPUT_REVIEW_REQUIRED'
    except (ValueError,OSError,KeyError,TypeError,UnicodeError) as error:
        result['error']={'type':type(error).__name__,'message':str(error)}
    print(json.dumps(result,ensure_ascii=False,indent=2,allow_nan=False))
    return 0 if result['status']!='STOP' else 2


if __name__=='__main__':raise SystemExit(main())
