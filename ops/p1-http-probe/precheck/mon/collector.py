import subprocess,socket,time,json,urllib.request,urllib.parse,select
kc=['/usr/bin/kubectl','--kubeconfig=/etc/kubernetes/admin.conf','--request-timeout=10s']
# Fail if occupied; the bind in kubectl still arbitrates any intervening race.
s=socket.socket();s.bind(('127.0.0.1',19095));s.close()
p=subprocess.Popen(kc+['-n','monitoring-api','port-forward','--address=127.0.0.1','svc/prometheus','19095:9090'],stdout=subprocess.PIPE,stderr=subprocess.PIPE)
rows=[];ready=False;first=b''
try:
 deadline=time.monotonic()+10
 while time.monotonic()<deadline and p.poll() is None:
  if select.select([p.stdout],[],[],.2)[0]:
   first=p.stdout.readline()
   ready=first.startswith(b'Forwarding from 127.0.0.1:19095 -> 9090')
   break
 if not ready:raise RuntimeError('PORT_FORWARD_NOT_READY')
 opener=urllib.request.build_opener(urllib.request.ProxyHandler({}))
 for name,query in [('all_up', 'up'), ('save_success', 'sum(transaction_save_success_total)'), ('save_failure', 'sum(transaction_save_failure_total)'), ('missing_client_ip', 'sum(missing_client_ip_total)'), ('evaluated', 'sum(fds_evaluated_total)'), ('detected', 'sum by(rule_id)(fds_detected_total)'), ('app_up', 'count(up{job=~"transaction-api|fds-engine"}==1)')]:
  url='http://127.0.0.1:19095/api/v1/query?query='+urllib.parse.quote(query,safe='')
  try:
   with opener.open(url,timeout=5) as res: data=res.read(65537)
   if len(data)>65536:raise RuntimeError('OUTPUT_LIMIT')
   rows.append({'name':name,'query':query,'raw':data.decode()})
  except Exception as e:rows.append({'name':name,'error':type(e).__name__+':'+str(e)})
finally:
 if p.poll() is None:p.terminate()
 try:stdout,stderr=p.communicate(timeout=3)
 except subprocess.TimeoutExpired:p.kill();stdout,stderr=p.communicate(timeout=2)
 print(json.dumps({'queries':rows,'forwarder_pid':p.pid,'forwarder_stopped':p.poll() is not None,'forward_stdout':(first+stdout).decode(),'forward_stderr':stderr.decode(),'scope':'baseline API read only; not IF06B network-path evidence'}))
