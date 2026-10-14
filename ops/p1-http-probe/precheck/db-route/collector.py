import os,socket,shutil,json,subprocess,datetime
out={'host':socket.gethostname(),'uid':os.geteuid(),'at_utc':datetime.datetime.now(datetime.timezone.utc).isoformat(),'mutation':False,'paths':{x:shutil.which(x) for x in ['psql','runuser','sudo']}}
cmd=['/usr/sbin/runuser','-u','postgres','--','/usr/bin/psql','-X','-v','ON_ERROR_STOP=1','-A','-t','-d','postgres','-c',"BEGIN READ ONLY; SET LOCAL statement_timeout='5s'; SELECT current_user,current_database(),current_setting('server_version'),current_setting('TimeZone'); SELECT datname FROM pg_database WHERE NOT datistemplate ORDER BY datname; COMMIT;"]
p=subprocess.run(cmd,capture_output=True,text=True,timeout=15)
out.update(argv=cmd,rc=p.returncode,stdout=p.stdout,stderr=p.stderr);print(json.dumps(out))
