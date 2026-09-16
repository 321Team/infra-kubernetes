"""Receipt-linked activation input producer and managed whole-state bridge.

This proves consistency of supplied/local evidence, not approval or its origin.
A root entrypoint must authenticate source, inputs and live collectors, persist
the produced payload digest once and retain the original bytes. No host writes.
"""
import copy
import hashlib
import importlib.util
from pathlib import Path

import cp1_cni_membership as m
import cp1_cni_transition as t
import cp1_cni_transition_observer as o
from cp1_cni_observer import parse_json, success
from cp1_cni_staging import validate_manifest
from cp1_cni_guard import verified_guard

ROOT = Path(__file__).resolve().parents[1]
SCHEMA = 'fds.cp1-cni-managed-activation.v1'
PROJECTION = 'fds.cp1-cni-managed-transition.v1'
PINS = {
    'node-firewall.py':'93b8ede35524a11c92e302d40d91f3b3034584e74a364b2f58a03f50ff8438fa',
    'run-node-firewall.py':'cb49cc6403b74c20f10d948c582da7b606209b1201b0eba740eb673ba1e7adb3',
    'cp-canary-node.py':'a09efb38dbbc313124f6d42858c3ee3e7d676e933abe8ed0550bf8937fb18dcc',
    'check-cp1-cni-handoff.py':'8fbc4d03fd76fecd27bd6f82a3672f4d0220ed1db8c331c61d9310c98d7db1d3',
}
# Explicit dependency closure; never infer trusted code from directory contents.
SOURCE_NAMES = tuple(sorted({*PINS,
    'cp1_cni_activation.py','prepare-cp1-cni-managed.py',
    'collect-cp1-cni-dataplane.py','collect-cp1-cni-membership.py','cp1-cni-calico-shapes.json',
    'cp1_cni_dataplane.py','cp1_cni_delta.py','cp1_cni_guard.py','cp1_cni_journal.py',
    'cp1_cni_membership.py','cp1_cni_membership_io.py','cp1_cni_observer.py','cp1_cni_recovery.py',
    'cp1_cni_staging.py','cp1_cni_transition.py','cp1_cni_transition_io.py',
    'cp1_cni_transition_observer.py','cp1_cni_unit_cleanup.py','cp1_cni_unit_files.py',
    'cp1_cni_withdrawal.py'}))
WINDOW = ('run_id','boot_id','wall_start','wall_end','boot_start','boot_end')
FALSE_FLAGS = ('authorizes_apply','authorizes_cp1_acceptance','authorizes_cp2_change','authorizes_join')


def source_manifest(root=ROOT):
    result = {}
    for name in SOURCE_NAMES:
        path=Path(root)/'scripts'/name
        m.need(path.is_file() and not path.is_symlink(), 'source absent/symlink: '+name)
        raw=path.read_bytes();m.need(len(raw)<=2*1024*1024, 'source too large')
        result['scripts/'+name]=hashlib.sha256(raw).hexdigest()
    for name,pin in PINS.items():m.need(result['scripts/'+name]==pin, 'original source pin differs: '+name)
    return result


def normalize(snapshot):
    t.validate_snapshot(snapshot)
    value=copy.deepcopy(snapshot)
    for plane in ('runtime','permanent'):
        data=value['inventory'][plane]
        m.need(t.NAME in data['ipsets'] and t.NAME in data['policies'], 'managed objects absent')
    # Only these two validated Runtime fields may change after activation.
    value['inventory']['runtime']['ipsets'][t.NAME]['entries']=''
    value['inventory']['runtime']['policies'][t.NAME]['disable']='yes'
    return value


def request_lease(request, bindings):
    m.need(set(request)=={'schema','scope',*WINDOW} and
           request['schema']=='fds.cp1-cni-activation-request.v1' and m.same(request['scope'],m.SCOPE),
           'activation request fields/scope differ')
    value={'schema':'fds.cp1-cni-membership-lease.v1','scope':copy.deepcopy(m.SCOPE),
           **{key:request[key] for key in WINDOW}, **bindings}
    m.validate_lease(value)
    return value


def original_receipt(raw, expected):
    m.digest(expected)
    m.need(isinstance(raw,bytes) and len(raw)<=8*1024*1024 and hashlib.sha256(raw).hexdigest()==expected,
           'original input byte hash differs')
    # Validate with the unchanged producer contract, not a reimplementation.
    for name,pin in PINS.items():m.need(hashlib.sha256((ROOT/'scripts'/name).read_bytes()).hexdigest()==pin,
                                     'original validator source differs')
    spec=importlib.util.spec_from_file_location('activation_original_controller',ROOT/'scripts/run-node-firewall.py')
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    wrapper=parse_json(raw.decode('utf-8'))
    m.need(isinstance(wrapper,dict) and isinstance(wrapper.get('result',wrapper),dict),
           'original receipt wrapper must be an object')
    return module.validate_cp1_receipt(wrapper)


def lease_for(payload):
    return request_lease(payload['request'],{
        'source_sha256':payload['source_sha256'], 'policy_sha256':m.fingerprint(t.documents(payload['request']['run_id'])),
        'original_input_sha256':payload['original_input_sha256'], 'activation_sha256':m.fingerprint(payload),
        'fixed_state_sha256':m.fingerprint(normalize(payload['expected_empty_active']))})


def build(raw_original, original_sha256, capture, peers, request, request_sha256,
          manifest, source_sha256, clock):
    """All digests supplied independently by the caller; none grants authority.

    A proposal is time-bound and cannot be replayed after review as a new lease.
    An operational caller must collect fresh input under its approved window.
    """
    validate_manifest(manifest,source_sha256)
    m.need(m.same(source_manifest(),manifest), 'source manifest missing/extra/different dependency')
    m.digest(request_sha256)
    m.need(m.fingerprint(request)==request_sha256, 'independent request hash differs')
    receipt=original_receipt(raw_original,original_sha256)
    baseline=o.observe(capture,peers,clock,receipt_link=True);t.validate_baseline(baseline)
    captured=parse_json(success(capture['queries']['original/firewall']))
    m.need(m.same(receipt['after'],captured), 'live firewall no longer equals original receipt after')
    plan=t.steps(baseline,request['run_id'])
    payload={'schema':'fds.cp1-cni-activation-payload.v1','request':copy.deepcopy(request),
        'request_sha256':request_sha256,'source_sha256':source_sha256,'manifest':copy.deepcopy(manifest),
        'original_input_sha256':original_sha256,'original_receipt_sha256':m.fingerprint(receipt),
        'original_after_sha256':m.fingerprint(captured),'capture_sha256':m.fingerprint(capture),
        'peer_capture_sha256':m.fingerprint(peers),'captured_clock':copy.deepcopy(clock),
        'baseline':baseline,'plan':plan,'plan_sha256':m.fingerprint(plan),
        'documents':t.documents(request['run_id']),'expected_empty_active':copy.deepcopy(plan[-1]['after']),
        'status':'CHECKED_ACTIVATION_INPUT_REVIEW_REQUIRED',**{key:False for key in FALSE_FLAGS}}
    lease=lease_for(payload)
    m.remaining(lease,clock,m.RESERVE)
    for field,start in (('wall','wall_start'),('boottime','boot_start')):
        m.need(0<=clock[field]-lease[start]<30, 'activation preparation window is stale')
    result={'schema':SCHEMA,'projection_schema':PROJECTION,'payload':payload,'lease':lease}
    validate(result,lease['activation_sha256'])
    return result


def validate(value, expected_activation_sha256):
    """Caller must read expected digest from its immutable authenticated stage."""
    m.digest(expected_activation_sha256)
    m.need(set(value)=={'schema','projection_schema','payload','lease'} and
           value['schema']==SCHEMA and value['projection_schema']==PROJECTION, 'unknown managed activation schema')
    p=value['payload'];l=value['lease']
    m.need(set(p)=={'schema','request','request_sha256','source_sha256','manifest','original_input_sha256',
        'original_receipt_sha256','original_after_sha256','capture_sha256','peer_capture_sha256','captured_clock',
        'baseline','plan','plan_sha256','documents','expected_empty_active','status',*FALSE_FLAGS}, 'activation payload fields differ')
    m.need(p['schema']=='fds.cp1-cni-activation-payload.v1' and
           p['status']=='CHECKED_ACTIVATION_INPUT_REVIEW_REQUIRED' and all(p[k] is False for k in FALSE_FLAGS),
           'activation input cannot grant approval')
    m.need(m.fingerprint(p)==expected_activation_sha256, 'immutable activation payload differs')
    validate_manifest(p['manifest'],p['source_sha256'])
    m.need(set(p['manifest'])=={'scripts/'+n for n in SOURCE_NAMES} and
           all(p['manifest']['scripts/'+n]==h for n,h in PINS.items()),'activation dependency closure differs')
    for k in ('original_input_sha256','original_receipt_sha256','original_after_sha256','capture_sha256','peer_capture_sha256'):
        m.digest(p[k])
    m.need(m.fingerprint(p['request'])==p['request_sha256'], 'request binding differs')
    t.validate_baseline(p['baseline']);plan=t.steps(p['baseline'],p['request']['run_id'])
    m.need(m.same(plan,p['plan']) and m.fingerprint(plan)==p['plan_sha256'] and
           m.same(plan[-1]['after'],p['expected_empty_active']) and
           m.same(p['documents'],t.documents(p['request']['run_id'])), 'compiled activation plan differs')
    m.need(m.same(l,lease_for(p)), 'lease changed/renewed after preparation')
    m.remaining(l,p['captured_clock'],m.RESERVE)
    for field,start in (('wall','wall_start'),('boottime','boot_start')):
        m.need(0<=p['captured_clock'][field]-l[start]<30, 'activation preparation window is stale')
    return l


def transition_state(activation, expected_activation_sha256, capture, peers, clock):
    """Create a write-ahead initial state only from a fresh unchanged baseline.

    The caller exclusively creates/fsyncs this state before arming/launching.
    No existing journal may be replaced by invoking this helper again.
    """
    lease=validate(activation,expected_activation_sha256)
    m.remaining(lease,clock,m.RESERVE)
    for field,start in (('wall','wall_start'),('boottime','boot_start')):
        m.need(0<=clock[field]-lease[start]<30, 'activation handoff too late: no lease renewal')
    actual=o.observe(capture,peers,clock,receipt_link=True)
    m.need(clock['boot_id']==lease['boot_id'] and m.same(actual,activation['payload']['baseline']),
           'activation baseline changed before handoff')
    fw=parse_json(success(capture['queries']['original/firewall']))
    m.need(m.fingerprint(fw)==activation['payload']['original_after_sha256'], 'original state changed before handoff')
    return t.initial(lease,actual)


def snapshot_from_records(capture, peers, activation, expected_activation_sha256, units, clock):
    """Ordinary grant observer: no unarmed bootstrap or legacy-schema fallback."""
    lease=validate(activation,expected_activation_sha256);m.remaining(lease,clock,m.RESERVE)
    actual=o.observe(capture,peers,clock,receipt_link=True)
    m.need(capture['boot_id']==lease['boot_id'],'managed observer boot differs')
    fixed=normalize(actual)
    m.need(m.same(fixed,normalize(activation['payload']['expected_empty_active'])) and
           m.fingerprint(fixed)==lease['fixed_state_sha256'], 'managed fixed state drift')
    guard=verified_guard(units,lease,clock)
    data=actual['inventory']['runtime'];values=m.entries(data['ipsets'][t.NAME]['entries'].split())
    result={'boot_id':lease['boot_id'],'run_id':lease['run_id'],'owned':True,
            'fixed_state_sha256':m.fingerprint(fixed),'policy_enabled':data['policies'][t.NAME]['disable']=='no',
            'native_entries':values,'daemon_entries':values,'guard_armed':guard['armed'],'guard_deadline':guard['deadline']}
    observed=m.observation_from_capture(peers,lease,result,clock)
    # A peer-only refresh must not make the older full-state observation fresh.
    for key in ('wall','boottime'):observed[key]=min(observed[key],capture[key])
    m.observation(observed,lease,clock);result['eligible_observation']=observed
    return result
