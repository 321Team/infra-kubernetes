import datetime,json,os,platform,socket,subprocess,urllib.request,hashlib
assert socket.gethostname()=='edge01'
out={'host':socket.gethostname(),'uid':os.geteuid(),'started_at_utc':datetime.datetime.now(datetime.timezone.utc).isoformat(),'python':platform.python_version(),'api_mutations':0,'db_writes':0,'http':[]}
p=subprocess.run(['/usr/bin/chronyc','tracking'],capture_output=True,timeout=5);out['chronyc']={'rc':p.returncode,'stdout':p.stdout.decode(),'stderr':p.stderr.decode()}
for host,port,path in [(h,30080,p) for h in ('10.1.93.105','10.1.93.107','10.1.93.108') for p in ('/readyz','/metrics')]+[('127.0.0.1',80,'/readyz')]:
 u='http://%s:%s%s'%(host,port,path)
 row={'url':u,'at_utc':datetime.datetime.now(datetime.timezone.utc).isoformat()}
 try:
  with urllib.request.urlopen(u,timeout=3) as r:body=r.read(10240);row.update(status=r.status,body_bytes=len(body),body_sha256=hashlib.sha256(body).hexdigest())
 except urllib.error.HTTPError as e:body=e.read(10240);row.update(status=e.code,body_bytes=len(body),body_sha256=hashlib.sha256(body).hexdigest())
 except Exception as e:row.update(status=None,error=type(e).__name__)
 out['http'].append(row)
out['finished_at_utc']=datetime.datetime.now(datetime.timezone.utc).isoformat();print(json.dumps(out))
