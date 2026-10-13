"""Four namespace-scoped GETs for pre/post-deploy identity and readiness."""
import datetime,hashlib,json,os,socket,subprocess
assert socket.gethostname()=='k8s-control01' and os.geteuid()==0
def now():return datetime.datetime.now(datetime.timezone.utc).isoformat()
def meta(x):return {k:x.get('metadata',{}).get(k) for k in ('name','namespace','uid','resourceVersion','generation','deletionTimestamp')}
def digest(x):return hashlib.sha256(json.dumps(x,sort_keys=True,separators=(',',':')).encode()).hexdigest()
out={'host':socket.gethostname(),'started_at_utc':now(),'mutation':False,'secret_or_env_values_collected':False,'queries':[]}
for kind,path in [('deployments','/apis/apps/v1/namespaces/fds-app/deployments'),('pods','/api/v1/namespaces/fds-app/pods'),('services','/api/v1/namespaces/fds-app/services'),('endpointslices','/apis/discovery.k8s.io/v1/namespaces/fds-app/endpointslices')]:
    argv=['/usr/bin/kubectl','--kubeconfig=/etc/kubernetes/admin.conf','--request-timeout=10s','get','--raw='+path]
    r=subprocess.run(argv,capture_output=True,timeout=15)
    q={'kind':kind,'at_utc':now(),'argv':argv,'rc':r.returncode,'raw_stdout_sha256':hashlib.sha256(r.stdout).hexdigest(),'stderr_sha256':hashlib.sha256(r.stderr).hexdigest(),'items':[]}
    assert r.returncode==0 and len(r.stdout)<4*1024*1024
    for x in json.loads(r.stdout)['items']:
        s=x.get('spec',{});status=x.get('status',{});item={'metadata':meta(x)}
        if kind=='deployments':
            item.update(spec_sha256=digest(s),replicas=s.get('replicas'),containers=[{k:c.get(k) for k in ('name','image','imagePullPolicy')} for c in s['template']['spec']['containers']],status={k:status.get(k) for k in ('observedGeneration','readyReplicas','updatedReplicas','availableReplicas')})
        elif kind=='pods':
            item.update(node=s.get('nodeName'),owners=x['metadata'].get('ownerReferences',[]),phase=status.get('phase'),containers=[{k:c.get(k) for k in ('name','image','imagePullPolicy')} for c in s['containers']],statuses=[{k:c.get(k) for k in ('name','ready','started','restartCount','image','imageID','containerID')} for c in status.get('containerStatuses',[])])
        elif kind=='services':item['spec']={k:s.get(k) for k in ('type','selector','clusterIP','ports','externalTrafficPolicy')}
        else:item.update(labels=x['metadata'].get('labels',{}),ports=x.get('ports'),endpoints=[{k:e.get(k) for k in ('addresses','conditions','nodeName','targetRef')} for e in x.get('endpoints',[])])
        q['items'].append(item)
    out['queries'].append(q)
out['finished_at_utc']=now();print(json.dumps(out,sort_keys=True))
