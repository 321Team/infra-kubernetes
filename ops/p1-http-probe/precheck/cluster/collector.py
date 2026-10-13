"""One-off HA shape read, no Secret/ConfigMap values, no API mutation."""
import datetime as dt,hashlib,json,os,socket,subprocess,time
def now():return dt.datetime.now(dt.timezone.utc).isoformat()
def identity():return {'host':socket.gethostname(),'uid':os.geteuid(),'boot':open('/proc/sys/kernel/random/boot_id').read().strip(),'product_uuid':open('/sys/class/dmi/id/product_uuid').read().strip()}
before=identity()
assert before['host']=='k8s-control01' and before['uid']==0,'STOP_HOST'
KC=['/usr/bin/kubectl','--kubeconfig=/etc/kubernetes/admin.conf','--request-timeout=6s']
queries=[('cluster',KC+['get','namespace','kube-system','-o','json']),('deployments',KC+['get','deploy','-n','fds-app','transaction-api','fds-engine','-o','json']),('pods',KC+['get','pods','-n','fds-app','-o','json']),('services',KC+['get','svc','-n','fds-app','-o','json']),('pdb',KC+['get','pdb','-n','fds-app','-o','json']),('endpoints',KC+['get','endpointslices','-n','fds-app','-o','json']),('nodes',KC+['get','nodes','-o','json']),('pvc',KC+['get','pvc','-n','fds-app','-o','json']),('pv',KC+['get','pv','-o','json']),('hpa',KC+['get','hpa','-n','fds-app','-o','json']),('worker02pods',KC+['get','pods','-A','--field-selector=spec.nodeName=k8s-worker02','-o','json'])]
def meta(x):return {k:x.get('metadata',{}).get(k) for k in ('name','namespace','uid','resourceVersion','generation','labels')}
def subset(x,keys):return {k:x.get(k) for k in keys}
def container(c):
    d=subset(c,('name','image','imagePullPolicy','ports','resources'))
    d['env_names']=[v.get('name') for v in c.get('env',[])]
    d['env_value_sources']=[{'name':v.get('name'),'valueFrom':v['valueFrom']} for v in c.get('env',[]) if 'valueFrom' in v]
    for key in ('readinessProbe','livenessProbe','startupProbe'):
        if key in c:
            p=subset(c[key],('initialDelaySeconds','periodSeconds','timeoutSeconds','successThreshold','failureThreshold','terminationGracePeriodSeconds','tcpSocket','grpc'))
            if 'httpGet' in c[key]:p['httpGet']=subset(c[key]['httpGet'],('path','port','scheme'))
            if 'exec' in c[key]:p['exec_present']=True
            d[key]=p
    return d
def podspec(s):
    d=subset(s,('nodeName','affinity','topologySpreadConstraints','tolerations','nodeSelector','terminationGracePeriodSeconds','serviceAccountName','hostNetwork','schedulerName','priorityClassName'))
    d['containers']=[container(c) for c in s.get('containers',[])];d['initContainers']=[container(c) for c in s.get('initContainers',[])]
    d['storage']=[subset(v,('name','persistentVolumeClaim','emptyDir','hostPath')) for v in s.get('volumes',[]) if any(k in v for k in ('persistentVolumeClaim','emptyDir','hostPath'))]
    return d
def project(kind,x):
    d={'metadata':meta(x)};s=x.get('spec',{});status=x.get('status',{})
    if kind=='cluster':return d
    if kind=='deployments':d.update(spec=subset(s,('replicas','selector','strategy','minReadySeconds','progressDeadlineSeconds')),template=podspec(s.get('template',{}).get('spec',{})),status=subset(status,('observedGeneration','replicas','updatedReplicas','readyReplicas','availableReplicas','unavailableReplicas')))
    elif kind in ('pods','worker02pods'):d.update(spec=podspec(s),status=subset(status,('phase','podIP','conditions')),containers=[subset(c,('name','image','imageID','containerID','ready','restartCount','started')) for c in status.get('containerStatuses',[])])
    elif kind=='nodes':d.update(spec=subset(s,('unschedulable','taints','podCIDR')),status={'conditions':[subset(c,('type','status','lastTransitionTime')) for c in status.get('conditions',[])],'allocatable':status.get('allocatable'),'nodeInfo':subset(status.get('nodeInfo',{}),('kernelVersion','containerRuntimeVersion','kubeletVersion'))})
    elif kind=='endpoints':d.update(addressType=x.get('addressType'),ports=x.get('ports'),endpoints=x.get('endpoints'))
    elif kind=='pv':d.update(spec=subset(s,('claimRef','capacity','accessModes','storageClassName','persistentVolumeReclaimPolicy','local','nodeAffinity')),status=subset(status,('phase',)))
    else:d.update(spec=s,status=subset(status,('phase','currentHealthy','desiredHealthy','disruptionsAllowed','expectedPods')))
    return d
out={'started_at_utc':now(),'scope':'HA_APP_READ_ONLY_PROJECTION','identity_before':before,'queries':[],'api_mutations':0,'credential_exports':0,'deliberate_guest_writes':0};end=time.monotonic()+70
for name,argv in queries:
    row={'name':name,'argv':argv,'started_at_utc':now()}
    try:
        if time.monotonic()>=end:raise TimeoutError('TOTAL_BUDGET')
        p=subprocess.run(argv,capture_output=True,timeout=min(8,end-time.monotonic()),env=dict(os.environ,LC_ALL='C'))
        row.update(rc=p.returncode,raw_response_sha256=hashlib.sha256(p.stdout).hexdigest(),stderr_sha256=hashlib.sha256(p.stderr).hexdigest(),stderr_bytes=len(p.stderr))
        if p.returncode==0 and len(p.stdout)<=2*1024*1024:
            x=json.loads(p.stdout);row['items']=[project(name,item) for item in (x['items'] if 'items' in x else [x])]
        else:row['error']='QUERY_FAILED_OR_SIZE_LIMIT'
    except (OSError,ValueError,TimeoutError,subprocess.TimeoutExpired) as e:row['error']=type(e).__name__
    row['finished_at_utc']=now();out['queries'].append(row)
p=subprocess.run(['/usr/bin/chronyc','tracking'],capture_output=True,timeout=5);out['chronyc']={'rc':p.returncode,'stdout':p.stdout.decode(),'stderr':p.stderr.decode()}
out.update(identity_after=identity(),finished_at_utc=now());out['identity_unchanged']=before==out['identity_after']
out['status']='COLLECTED_REVIEW_REQUIRED' if out['identity_unchanged'] and all(r.get('rc')==0 and 'error' not in r for r in out['queries']) else 'INCOMPLETE'
print(json.dumps(out));raise SystemExit(0 if out['status']=='COLLECTED_REVIEW_REQUIRED' else 2)
