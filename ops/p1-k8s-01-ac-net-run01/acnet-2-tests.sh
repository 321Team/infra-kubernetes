#!/bin/bash
# ac-net-run01 step 3-5: T09(Pod->CoreDNS), T07(Pod IP, 6 directions), T08(Service ClusterIP+FQDN). Run on control01 as root.
set -u
R=/root/ac-net-run01; NS=ac-net-run01; K="kubectl --kubeconfig /etc/kubernetes/admin.conf"
HTTP='import sys,urllib.request
try:
    r=urllib.request.urlopen(sys.argv[1],timeout=5); print("HTTP",r.status)
except Exception as e:
    print("FAIL",type(e).__name__,e)'
DNS='import sys,socket
try:
    print("A",sorted({a[4][0] for a in socket.getaddrinfo(sys.argv[1],None,socket.AF_INET)}))
except socket.gaierror as e:
    print("GAIERROR",e.errno,e)'
{
echo "# AC-NET-RUN01 TESTS"; date -Is; hostname
for p in a b c; do
  echo "probe-$p ip=$($K -n $NS get pod probe-$p -o jsonpath='{.status.podIP}') node=$($K -n $NS get pod probe-$p -o jsonpath='{.spec.nodeName}') svc=$($K -n $NS get svc probe-$p-svc -o jsonpath='{.spec.clusterIP}')"
done
echo "## T09 Pod -> CoreDNS (resolv.conf and names)"
for p in a b c; do
  echo "-- probe-$p"; $K -n $NS exec probe-$p -- cat /etc/resolv.conf | head -3
  for n in kubernetes.default.svc.cluster.local kube-dns.kube-system.svc.cluster.local nonexistent-ac-net.default.svc.cluster.local quay.io; do
    echo "   $n -> $($K -n $NS exec probe-$p -- python3 -c "$DNS" $n 2>&1 | tail -1)"
  done
done
echo "## T07 Pod IP HTTP (6 directions)"
for s in a b c; do for d in a b c; do [ $s = $d ] && continue
  ip=$($K -n $NS get pod probe-$d -o jsonpath='{.status.podIP}')
  echo "probe-$s -> probe-$d http://$ip:8080/ : $($K -n $NS exec probe-$s -- python3 -c "$HTTP" http://$ip:8080/ 2>&1 | tail -1)"
done; done
echo "## T08 Service ClusterIP and FQDN HTTP (6 directions x 2)"
for s in a b c; do for d in a b c; do [ $s = $d ] && continue
  cip=$($K -n $NS get svc probe-$d-svc -o jsonpath='{.spec.clusterIP}')
  echo "probe-$s -> probe-$d-svc ClusterIP $cip:80 : $($K -n $NS exec probe-$s -- python3 -c "$HTTP" http://$cip:80/ 2>&1 | tail -1)"
  echo "probe-$s -> probe-$d-svc FQDN             : $($K -n $NS exec probe-$s -- python3 -c "$HTTP" http://probe-$d-svc.$NS.svc.cluster.local:80/ 2>&1 | tail -1)"
done; done
echo "## fds-app restart counts after tests"; $K -n fds-app get pods -o wide --no-headers | awk '{print $1,$3,$4}'
date -Is
} 2>&1 | tee $R/3-tests.txt
