#!/bin/bash
# ac-net-run01 step 6: T14 (UDP4789 unapproved + route/encapsulation evidence) and T15 (TCP179 / IPIP unapproved).
# Run on ansible01 from the reviewed checkout. Target = worker01 (HEP present, firewalld OFF). Source = edge01 10.1.93.50,
# which is NOT an approved source for these ports. One attempt per protocol. This script declares NO PASS.
# T15_IPIP: if raw sending is unavailable the result is NOT_VERIFIED; config evidence is supporting only (PMO/QA B4).
# Review fixes (supreme33-kr):
#  B-57-01 a TCP RST / SYN-ACK / ICMP reply means the packet was REACHED/REJECTED, not dropped by Calico -> recorded separately.
#  B-57-02 the capture is validated (listening line, kernel drop 0, clean exit, liveness control packets) or the arrival
#          observations are recorded as NOT_VERIFIED (CAPTURE=INVALID).
# edge01 sudo use: SUDO_OK means this script sends ONE raw IP protocol-4 packet as root from edge01; state this in the start comment.
set -u
HERE=$(cd "$(dirname "$0")" && pwd)
source ~/.config/ansible/p1-ans-01.env && export ANSIBLE_PRIVATE_KEY_FILE
cd ~/infra-ansible || exit 1
EDGE=10.1.93.50; TGT=10.1.93.105; ANS=10.250.10.10; D=/var/log/fds-evidence/P1-K8S-01/ac-net-run01
OUT=~/ac-net-run01-edge.txt
cnt(){ ansible -i inventory/hosts.yml k8s-worker01 -b -m script -a "$HERE/acnet-worker-counters.sh $1" 2>&1 | grep -o 'DROP_PKTS=[0-9]*' | head -1 | cut -d= -f2; }
{
echo "# AC-NET-RUN01 T14/T15 EDGE (from ansible01)"; date -Is; hostname
echo "## route / encapsulation evidence (pod CIDR routes per node) T14"
ansible -i inventory/hosts.yml k8s-control01,k8s-control02,k8s-control03,k8s-worker01,k8s-worker02,k8s-worker03 -m shell -a "hostname; ip route show | grep -E '10\.244\.' | head -12; ip -d link show vxlan.calico | grep -E 'vxlan|mtu' | head -2"
echo "## edge01 capabilities (raw send needs sudo -n)"
ansible -i "$EDGE," all -u svc_ansible -m shell -a "hostname; python3 --version; sudo -n true && echo SUDO_OK || echo SUDO_NO"
echo "## prepare evidence dir (worker01)"
ansible -i inventory/hosts.yml k8s-worker01 -b -m shell -a "mkdir -p $D && iptables-save -c > $D/t15-ipt-before.txt 2>&1; nft -a list ruleset > $D/t15-nft-before.txt 2>&1; echo saved"
echo "## start capture on worker01 (async, tcpdump 85s, clean INT exit; includes ICMP and liveness control packets)"
CAPOUT=$(ansible -i inventory/hosts.yml k8s-worker01 -b -B 100 -P 0 -m shell -a "timeout --signal=INT --preserve-status 85 tcpdump -nn -tttt -i ens160 '(host $EDGE and (tcp port 179 or udp port 4789 or ip proto 4 or icmp)) or (host $ANS and tcp port 22)' > $D/t15-edge-capture.txt 2>&1")
echo "$CAPOUT"
JID=$(echo "$CAPOUT" | grep -o '"ansible_job_id": "[^"]*"' | head -1 | cut -d'"' -f4); echo "capture_job_id=$JID"
sleep 6
echo "## ssh control (ansible01 -> worker01)"; ansible -i inventory/hosts.yml k8s-worker01 -m ping
C0=$(cnt c0); echo "C0 $(date -Is) DROP_PKTS=$C0"
echo "## NOISE window 30s (no test traffic)"; sleep 30
C1=$(cnt c1); echo "C1 $(date -Is) DROP_PKTS=$C1"
echo "## TEST attempts from edge01 (one per protocol)"
ansible -i "$EDGE," all -u svc_ansible -m shell -a "date -Is; echo --tcp179; timeout 5 bash -c '</dev/tcp/$TGT/179' && echo 'TCP179 OPEN' || echo 'TCP179 FAIL'; echo --udp4789; timeout 5 bash -c 'echo -n t14 > /dev/udp/$TGT/4789' && echo 'UDP4789 SENT(no ack expected)' || echo 'UDP4789 SEND_ERROR'; echo --ipip; sudo -n python3 -c \"import socket;s=socket.socket(socket.AF_INET,socket.SOCK_RAW,4);s.sendto(b'\\x45'+b'\\x00'*19,('$TGT',0));print('IPIP_SENT')\" 2>&1 | tail -1"
sleep 6
echo "## ssh control after attempts"; ansible -i inventory/hosts.yml k8s-worker01 -m ping
C2=$(cnt c2); echo "C2 $(date -Is) DROP_PKTS=$C2"
echo "## wait for capture to finish (tcpdump timeout 85s)"; sleep 25
echo "## capture job status"
ST=$(ansible -i inventory/hosts.yml k8s-worker01 -b -m async_status -a "jid=$JID" 2>&1); echo "$ST" | head -12
FIN=$(echo "$ST" | grep -o '"finished": [01]' | head -1 | awk '{print $2}'); RC=$(echo "$ST" | grep -o '"rc": [0-9]*' | head -1 | awk '{print $2}')
echo "## counters after (worker01)"
ansible -i inventory/hosts.yml k8s-worker01 -b -m shell -a "iptables-save -c > $D/t15-ipt-after.txt 2>&1; nft -a list ruleset > $D/t15-nft-after.txt 2>&1; echo saved"
echo "## per-rule DROP counter diff (c0 -> c1 noise, c1 -> c2 test)"
ansible -i inventory/hosts.yml k8s-worker01 -b -m shell -a "echo --noise c0-c1; diff $D/t15-drop-c0.txt $D/t15-drop-c1.txt | head -10; echo --test c1-c2; diff $D/t15-drop-c1.txt $D/t15-drop-c2.txt | head -10"
echo "## capture summary and validity"
SUM=$(ansible -i inventory/hosts.yml k8s-worker01 -b -m shell -a "f=$D/t15-edge-capture.txt; echo listening=\$(grep -c 'listening on ens160' \$f); echo kernel_dropped_zero=\$(grep -c '^0 packets dropped by kernel' \$f); echo stats_line=\$(grep -c 'packets captured' \$f); echo ssh_ctl=\$(grep -cE '$ANS\\.[0-9]+ > $TGT\\.22' \$f); echo total=\$(wc -l < \$f); echo tcp179_syn=\$(grep -cE '$EDGE\\.[0-9]+ > $TGT\\.179: Flags \\[S\\],' \$f); echo tcp179_synack=\$(grep -cE '$TGT\\.179 > $EDGE\\.[0-9]+: Flags \\[S\\.\\]' \$f); echo tcp179_rst=\$(grep -cE '$TGT\\.179 > $EDGE\\.[0-9]+: Flags \\[R' \$f); echo icmp_from_tgt=\$(grep -cE '$TGT > $EDGE: ICMP' \$f); echo udp4789_arrived=\$(grep -cE '$EDGE\\.[0-9]+ > $TGT\\.4789: UDP' \$f); echo ipip_proto4_arrived=\$(grep -ciE 'IPIP|ip-proto-4' \$f); grep -E '179|4789|IPIP|proto-4|ICMP' \$f | head -14" 2>&1)
echo "$SUM"
g(){ echo "$SUM" | grep -o "$1=[0-9]*" | head -1 | cut -d= -f2; }
LIS=$(g listening); KD=$(g kernel_dropped_zero); SL=$(g stats_line); SC=$(g ssh_ctl); SYN=$(g tcp179_syn); SA=$(g tcp179_synack); RST=$(g tcp179_rst); ICMP=$(g icmp_from_tgt); UDP=$(g udp4789_arrived); IPIP=$(g ipip_proto4_arrived)
ARR=$(( ${SYN:-0} + ${UDP:-0} + ${IPIP:-0} ))
NOISE=$(( ${C1:-0} - ${C0:-0} )); TEST=$(( ${C2:-0} - ${C1:-0} )); EXTRA=$(( TEST - NOISE ))
echo "## RESULT LINES (observations only; no PASS declared)"
if [ "${LIS:-0}" -ge 1 ] && [ "${KD:-0}" -ge 1 ] && [ "${SL:-0}" -ge 1 ] && [ "${SC:-0}" -ge 1 ] && [ "${FIN:-0}" = "1" ] && [ "${RC:-1}" = "0" ]; then CAP=VALID; else CAP=INVALID; fi
echo "CAPTURE=$CAP (listening=${LIS:-0} kernel_dropped_zero=${KD:-0} stats_line=${SL:-0} ssh_control_pkts=${SC:-0} job_finished=${FIN:-?} job_rc=${RC:-?})"
if [ "$CAP" = "VALID" ]; then
  echo "ARRIVED tcp179_syn=${SYN:-0} udp4789=${UDP:-0} ipip_proto4=${IPIP:-0} total=$ARR"
  echo "TCP179_REPLIES synack=${SA:-0} rst=${RST:-0} icmp_from_target=${ICMP:-0}"
  if [ "${SYN:-0}" -eq 0 ]; then echo "T15_TCP179=NOT_ARRIVED (no SYN seen; cannot judge)";
  elif [ "${SA:-0}" -gt 0 ] || [ "${RST:-0}" -gt 0 ] || [ "${ICMP:-0}" -gt 0 ]; then echo "T15_TCP179=REACHABLE_OR_REJECTED (a reply came back; this is NOT a Calico-drop shape)";
  else echo "T15_TCP179=SYN_ARRIVED_NO_REPLY (drop-shaped; judge with counter evidence)"; fi
  echo "DROP_COUNTER noise_delta=$NOISE test_delta=$TEST extra=$EXTRA"
  if [ "$EXTRA" -ge "$ARR" ] && [ "$ARR" -gt 0 ]; then echo "COUNTER_ATTRIBUTION=CONSISTENT_WITH_CALICO_DROP (extra >= arrived; heuristic, not proof)"; else echo "COUNTER_ATTRIBUTION=NOT_SEPARATED (record as PARTIAL/LIMITATION)"; fi
  if [ "${IPIP:-0}" -gt 0 ]; then echo "T15_IPIP=ARRIVED (judge with counter evidence)"; else echo "T15_IPIP=NOT_VERIFIED (raw send unavailable or no arrival; config evidence is supporting only)"; fi
  echo "T14_UDP4789 arrived=${UDP:-0} (judge with the per-rule counter diff above; silence alone is not a deny)"
else
  echo "T14_UDP4789=NOT_VERIFIED (capture invalid)"; echo "T15_TCP179=NOT_VERIFIED (capture invalid)"; echo "T15_IPIP=NOT_VERIFIED (capture invalid)"
  echo "COUNTER_ATTRIBUTION=NOT_VERIFIED (capture invalid)"
fi
} 2>&1 | tee $OUT; sha256sum $OUT
