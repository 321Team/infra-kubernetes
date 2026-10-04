#!/bin/bash
# P1-MON-01 IF-06B 캡처 종료 코드 보존 모의 시험 (로컬, 실제 tcpdump·sudo 없음)
# 문서 3절 캡처 블록과 같은 구조(sudo timeout --signal=INT --preserve-status ... & / PID 보존 / 해당 PID만 wait)를
# 가짜 sudo·tcpdump로 돌려 정상 SIGINT 종료와 비정상 종료의 rc가 그대로 남는지 확인한다.
T=$(mktemp -d); mkdir -p "$T/bin"
printf '#!/bin/bash\nexec "$@"\n' > "$T/bin/sudo"
cat > "$T/bin/tcpdump" <<'EOT'
#!/bin/bash
echo "listening on ens160, link-type EN10MB (Ethernet), snapshot length 262144 bytes" >&2
case "$MOCK_MODE" in
  normal) trap 'echo "3 packets captured" >&2; echo "3 packets received by filter" >&2; echo "0 packets dropped by kernel" >&2; exit 0' INT
          while :; do sleep 0.2; done ;;
  crash)  sleep 1; echo "tcpdump: pcap_loop: The interface disappeared" >&2; exit 1 ;;
  nohandler) while :; do sleep 0.2; done ;;
esac
EOT
chmod +x "$T/bin/sudo" "$T/bin/tcpdump"
export PATH="$T/bin:$PATH"
run_case() {
  MOCK_MODE=$1; export MOCK_MODE
  sudo timeout --signal=INT --preserve-status 2 tcpdump -nn -tttt -l -i ens160 'host a and host b' > "$T/cap.txt" 2> "$T/cap-stats.txt" &
  CAP_PID=$!
  if wait "$CAP_PID"; then CAP_RC=0; else CAP_RC=$?; fi
  printf 'case=%-10s rc=%s listening=%s dropped0=%s\n' "$1" "$CAP_RC" \
    "$(grep -c '^listening on' "$T/cap-stats.txt")" "$(grep -c '^0 packets dropped by kernel' "$T/cap-stats.txt")"
}
echo "# 기존 방식 재현: 인자 없는 wait"
( false & wait; echo "bare_wait_rc=$?" )
( false & p=$!; wait "$p"; echo "pid_wait_rc=$?" )
echo "# 새 방식"
run_case normal
run_case crash
run_case nohandler
rm -rf "$T"
