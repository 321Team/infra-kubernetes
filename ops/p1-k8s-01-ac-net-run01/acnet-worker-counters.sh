#!/bin/bash
# Helper (runs on worker01 as root via ansible `script` module): print Calico drop counters and save the per-rule snapshot.
# Usage: acnet-worker-counters.sh <label>     (writes /var/log/fds-evidence/P1-K8S-01/ac-net-run01/t15-drop-<label>.txt)
# The counter is the sum of packets on rules `-m mark --mark 0x40000/0x40000 -j DROP` (Calico "not allowed" mark).
LABEL=${1:-snap}; D=/var/log/fds-evidence/P1-K8S-01/ac-net-run01
mkdir -p "$D"
echo "TS=$(date -Is)"
iptables-save -c 2>/dev/null | grep -E 'mark 0x40000/0x40000 -j DROP' | sed 's/ -m comment.*//' | tee "$D/t15-drop-$LABEL.txt"
TOT=$(iptables-save -c 2>/dev/null | grep -E 'mark 0x40000/0x40000 -j DROP' | sed -E 's/^\[([0-9]+):[0-9]+\].*/\1/' | awk '{s+=$1} END{print s+0}')
echo "DROP_PKTS=$TOT"
