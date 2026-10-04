#!/bin/bash
# ac-net-run01 step 6: T14 (UDP4789 unapproved + route/encapsulation evidence) and T15 (TCP179 / IPIP unapproved).
# Run on ansible01 from the reviewed checkout. Target = worker01 (HEP present, firewalld OFF). Source = edge01 10.1.93.50,
# which is NOT an approved source for these ports. One attempt per protocol. This script declares NO PASS.
# T15_IPIP: if raw sending is unavailable the result is NOT_VERIFIED; config evidence is supporting only (PMO/QA B4).
# T14/T15 counter guard: a NOISE window (no test traffic) is measured first so the test-window delta can be compared.
set -u
HERE=$(cd "$(dirname "$0")" && pwd)
source ~/.config/ansible/p1-ans-01.env && export ANSIBLE_PRIVATE_KEY_FILE
cd ~/infra-ansible || exit 1
EDGE=10.1.93.50; TGT=10.1.93.105; D=/var/log/fds-evidence/P1-K8S-01/ac-net-run01
OUT=~/ac-net-run01-edge.txt
cnt(){ ansible -i inventory/hosts.yml k8s-worker01 -b -m script -a "$HERE/acnet-worker-counters.sh" 2>&1 | grep -o 'DROP_PKTS=[0-9]*' | head -1 | cut -d= -f2; }
{
echo "# AC-NET-RUN01 T14/T15 EDGE (from ansible01)"; date -Is; hostname
echo "## route / encapsulation evidence (pod CIDR routes per node) T14"
ansible -i inventory/hosts.yml k8s-control01,k8s-control02,k8s-control03,k8s-worker01,k8s-worker02,k8s-worker03 -m shell -a "hostname; ip route show | grep -E '10\.244\.' | head -12; ip -d link show vxlan.calico | grep -E 'vxlan|mtu' | head -2"
echo "## edge01 capabilities (raw send needs sudo -n)"
ansible -i "$EDGE," all -u svc_ansible -m shell -a "hostname; python3 --version; sudo -n true && echo SUDO_OK || echo SUDO_NO"
echo "## prepare evidence dir (worker01)"
ansible -i inventory/hosts.yml k8s-worker01 -b -m shell -a "mkdir -p $D && iptables-save -c > $D/t15-ipt-before.txt 2>&1; nft -a list ruleset > $D/t15-nft-before.txt 2>&1; echo saved"
echo "## start capture on worker01 (async 90s, tcpdump timeout 80s)"
ansible -i inventory/hosts.yml k8s-worker01 -b -B 90 -P 0 -m shell -a "timeout 80 tcpdump -nn -tttt -i ens160 'host $EDGE and (tcp port 179 or udp port 4789 or ip proto 4)' > $D/t15-edge-capture.txt 2>&1"
sleep 6
echo "## ssh control (ansible01 -> worker01)"; ansible -i inventory/hosts.yml k8s-worker01 -m ping
C0=$(cnt); echo "C0 $(date -Is) DROP_PKTS=$C0"
echo "## NOISE window 30s (no test traffic)"; sleep 30
C1=$(cnt); echo "C1 $(date -Is) DROP_PKTS=$C1"
echo "## TEST attempts from edge01 (one per protocol)"
ansible -i "$EDGE," all -u svc_ansible -m shell -a "date -Is; echo --tcp179; timeout 5 bash -c '</dev/tcp/$TGT/179' && echo 'TCP179 OPEN' || echo 'TCP179 FAIL'; echo --udp4789; timeout 5 bash -c 'echo -n t14 > /dev/udp/$TGT/4789' && echo 'UDP4789 SENT(no ack expected)' || echo 'UDP4789 SEND_ERROR'; echo --ipip; sudo -n python3 -c \"import socket;s=socket.socket(socket.AF_INET,socket.SOCK_RAW,4);s.sendto(b'\\x45'+b'\\x00'*19,('$TGT',0));print('IPIP_SENT')\" 2>&1 | tail -1"
sleep 6
C2=$(cnt); echo "C2 $(date -Is) DROP_PKTS=$C2"
echo "## wait for capture to finish"; sleep 25
echo "## counters after (worker01)"
ansible -i inventory/hosts.yml k8s-worker01 -b -m shell -a "iptables-save -c > $D/t15-ipt-after.txt 2>&1; nft -a list ruleset > $D/t15-nft-after.txt 2>&1; echo saved"
echo "## capture summary"
SUM=$(ansible -i inventory/hosts.yml k8s-worker01 -b -m shell -a "f=$D/t15-edge-capture.txt; echo total=\$(wc -l < \$f); echo tcp179_syn=\$(grep -cE '$EDGE\\.[0-9]+ > $TGT\\.179: Flags \\[S\\],' \$f); echo tcp179_synack=\$(grep -cE '$TGT\\.179 > $EDGE\\.[0-9]+: Flags \\[S\\.\\]' \$f); echo udp4789_arrived=\$(grep -cE '$EDGE\\.[0-9]+ > $TGT\\.4789: UDP' \$f); echo ipip_proto4_arrived=\$(grep -ciE 'IPIP|ip-proto-4' \$f); grep -E '179|4789|IPIP|proto-4' \$f | head -12" 2>&1)
echo "$SUM"
SYN=$(echo "$SUM" | grep -o 'tcp179_syn=[0-9]*' | head -1 | cut -d= -f2); UDP=$(echo "$SUM" | grep -o 'udp4789_arrived=[0-9]*' | head -1 | cut -d= -f2); IPIP=$(echo "$SUM" | grep -o 'ipip_proto4_arrived=[0-9]*' | head -1 | cut -d= -f2)
ARR=$(( ${SYN:-0} + ${UDP:-0} + ${IPIP:-0} ))
NOISE=$(( ${C1:-0} - ${C0:-0} )); TEST=$(( ${C2:-0} - ${C1:-0} )); EXTRA=$(( TEST - NOISE ))
echo "## RESULT LINES (observations only; no PASS declared)"
echo "ARRIVED tcp179_syn=${SYN:-0} udp4789=${UDP:-0} ipip_proto4=${IPIP:-0} total=$ARR"
echo "DROP_COUNTER noise_delta=$NOISE test_delta=$TEST extra=$EXTRA"
if [ "$EXTRA" -ge "$ARR" ] && [ "$ARR" -gt 0 ]; then echo "COUNTER_ATTRIBUTION=CONSISTENT_WITH_CALICO_DROP (extra >= arrived; heuristic, not proof)"; else echo "COUNTER_ATTRIBUTION=NOT_SEPARATED (record as PARTIAL/LIMITATION)"; fi
if [ "${IPIP:-0}" -gt 0 ]; then echo "T15_IPIP=ARRIVED (judge with counter evidence)"; else echo "T15_IPIP=NOT_VERIFIED (raw send unavailable or no arrival; config evidence is supporting only)"; fi
} 2>&1 | tee $OUT; sha256sum $OUT
