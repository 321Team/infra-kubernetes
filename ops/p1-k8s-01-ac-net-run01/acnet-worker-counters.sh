#!/bin/bash
# Helper (runs on worker01 as root via ansible `script` module): print Calico drop counters.
# The counter is the sum of packets on rules `-m mark --mark 0x40000/0x40000 -j DROP` (Calico "not allowed" mark).
echo "TS=$(date -Is)"
iptables-save -c 2>/dev/null | grep -E 'mark 0x40000/0x40000 -j DROP' | sed 's/ -m comment.*//'
TOT=$(iptables-save -c 2>/dev/null | grep -E 'mark 0x40000/0x40000 -j DROP' | sed -E 's/^\[([0-9]+):[0-9]+\].*/\1/' | awk '{s+=$1} END{print s+0}')
echo "DROP_PKTS=$TOT"
