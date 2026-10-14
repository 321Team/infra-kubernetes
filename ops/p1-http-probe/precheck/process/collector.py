"""Read exact container identity and host process metadata, never environments/signals."""
import datetime,hashlib,json,os,pathlib,select,signal,socket,subprocess,sys
assert socket.gethostname()=='k8s-worker02' and os.geteuid()==0
cid='7b24cf1f8132da141a186eb00d7cbd02e30b9d1e28a2dfdb1147376caf544a45'
uid='e349b045-870f-4061-a677-3007efe479e9'
argv=['/usr/bin/crictl','--runtime-endpoint','unix:///run/containerd/containerd.sock','inspect',cid]
p=subprocess.run(argv,capture_output=True,timeout=10);assert p.returncode==0
x=json.loads(p.stdout);s=x['status'];pid=x['info']['pid']
assert s['id']==cid and s['labels']['io.kubernetes.pod.uid']==uid and s['state']=='CONTAINER_RUNNING'
proc=pathlib.Path('/proc')/str(pid)
stat=proc.joinpath('stat').read_text();start=stat[stat.rfind(')')+2:].split()[19]
status={k:v.strip() for k,v in (line.split(':',1) for line in proc.joinpath('status').read_text().splitlines()) if k in ('Name','State','Pid','PPid','Uid','Gid','NSpid','Threads')}
out={'at_utc':datetime.datetime.now(datetime.timezone.utc).isoformat(),'host':socket.gethostname(),'uid':os.geteuid(),'python':sys.version,'boot_id':pathlib.Path('/proc/sys/kernel/random/boot_id').read_text().strip(),'container_id':cid,'container_status':{k:s.get(k) for k in ['metadata','state','labels','imageRef','createdAt','startedAt']},'pid':pid,'start_ticks':start,'process':status,'exe':os.readlink(proc/'exe'),'cmdline':proc.joinpath('cmdline').read_bytes().replace(b'\0',b' ').decode(),'cgroup':proc.joinpath('cgroup').read_text(),'pid_namespace':os.readlink(proc/'ns/pid'),'pidfd_open_available':hasattr(os,'pidfd_open'),'pidfd_send_signal_available':hasattr(signal,'pidfd_send_signal'),'signals_sent':0,'environment_read':False}
if hasattr(os,'pidfd_open'):
 fd=os.pidfd_open(pid)
 try:out['pidfd_open_ok']=True;out['pidfd_exited']=bool(select.select([fd],[],[],0)[0])
 finally:os.close(fd)
out['cri_raw_sha256']=hashlib.sha256(p.stdout).hexdigest()
print(json.dumps(out,sort_keys=True))
