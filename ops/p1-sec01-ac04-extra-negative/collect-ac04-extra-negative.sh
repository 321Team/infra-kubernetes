#!/usr/bin/env bash
# P1-SEC-01 AC-04 보완: 관리/승인 Source -> Worker 3대 9100/9101/10250 차단 확인 (TCP 접속 시도만, 설정 변경·Pod 생성 없음)
# REV=v3-20261005  (v1 에 대한 GWONUK 딥리뷰 #19 comment 5986838826 B-EXTRA-1·B-EXTRA-2 반영)
#
#   ansible01 에서: bash collect-ac04-extra-negative.sh mgmt   (관리 SSH Source, 경로 src 10.250.10.10)
#   edge01 에서:    bash collect-ac04-extra-negative.sh edge   (승인 Source, 경로 src 10.1.93.50)
#
# 실행 범위는 코드에 고정되어 있다(환경변수로 바꿀 수 없음). 대상 Worker 10.1.93.105/.107/.108,
# 차단 기대 포트 9100·9101·10250, 대조 포트 mgmt=22 / edge=30080, 접속 제한 3초.
# Source 결속: hostname 과 대상별 route src(ip route get)가 예상값이 아니면 **접속 전에 STOP**(rc=2).
#
# 두 결과를 분리해 표현한다.
#   COLLECTOR_STATUS = COMPLETE | INCOMPLETE   수집 자체가 정확한 행 집합(12행)으로 끝나고 파일이 봉인(seal)됐는가
#   AC_VERDICT       = BLOCKED_VALID | FAIL | INCONCLUSIVE | NOT_EVALUATED   차단 판정(AC 판정이 아님)
# 종료 코드(0 은 유효한 차단 확인뿐):
#   0  COMPLETE + BLOCKED_VALID(차단 기대 9행 전부, 해당 Worker 의 대조가 CONNECTED 인 TIMEOUT)
#   10 COMPLETE + INCONCLUSIVE(대조 불충족·REFUSED·ERROR 가 있음. 관측값은 보존하되 유효한 차단 PASS 로 세지 않음)
#   11 COMPLETE + FAIL(차단 기대인데 CONNECTED)
#   12 INCOMPLETE(행 누락·중복·실행 오류·파일 쓰기/봉인 실패)
#   2  STOP(사용법·Source 불일치·도구 부재, 접속 전 중단)
# TIMEOUT 은 Calico 정책과 상위 방화벽(mgmt-gw01 등) 중 차단 주체를 구분하지 못한다. 구분은 주장하지 않는다.
# worker03 의 edge 대조(30080)는 Local endpoint 부재로 실패할 수 있다(알려진 한계, #15). 다른 Worker 의 성공으로 승격하지 않는다.
set -uo pipefail
REV="v3-20261005"
ROLE="${1:-}"
case "$ROLE" in mgmt|edge) ;; *) echo "사용: $0 mgmt|edge" >&2; exit 2;; esac

# ---- 고정 범위(환경변수 무시) ----
WORKERS=(10.1.93.105 10.1.93.107 10.1.93.108)
NEG_PORTS=(9100 9101 10250)
TMO=3
if [ "$ROLE" = mgmt ]; then CTRL_PORT=22;    EXP_HOST=ansible01; EXP_SRC=10.250.10.10
else                         CTRL_PORT=30080; EXP_HOST=edge01;    EXP_SRC=10.1.93.50; fi
EXPECT_ROWS=$(( ${#WORKERS[@]} * (1 + ${#NEG_PORTS[@]}) ))   # 3 * (1 + 3) = 12

stop() { echo "STOP: $*" >&2; exit 2; }
TS="$(date -u +%Y%m%dT%H%M%SZ)"
umask 077
OUT="$(mktemp -d "p1-sec01-ac04-extra-neg-$ROLE-$TS.XXXXXX")" || stop "배타적 출력 폴더를 만들 수 없음"
INC=0     # 수집 불완전 사유 수
incomplete() { echo "INCOMPLETE: $*" >&2; echo "$*" >> "$OUT/incomplete-reasons.txt" 2>/dev/null; INC=$((INC+1)); }
w() { local file="$1"; shift; printf '%s\n' "$*" >> "$file" 2>/dev/null || incomplete "쓰기 실패: $file"; }   # w <file> <text>

# ---- 도구·Source 결속 확인(접속 전) ----
command -v timeout >/dev/null 2>&1 || stop "timeout 명령 없음"
command -v ip >/dev/null 2>&1      || stop "ip 명령 없음(route src 확인 불가)"
command -v sha256sum >/dev/null 2>&1 || stop "sha256sum 없음"
command -v tar >/dev/null 2>&1       || stop "tar 없음"
HN="$(hostname -s 2>/dev/null)"
[ "$HN" = "$EXP_HOST" ] || stop "hostname=$HN 이 예상($EXP_HOST)과 다름 - 잘못된 Source 에서 실행"
# bash /dev/tcp 지원 확인: 닫힌 로컬 포트에서 'No such file or directory' 가 나오면 지원하지 않는 bash
SELF_ERR="$(timeout 2 bash -c 'exec 3<>"/dev/tcp/127.0.0.1/1"' 2>&1)"
case "$SELF_ERR" in *"No such file or directory"*) stop "이 bash 는 /dev/tcp 를 지원하지 않음";; esac
: > "$OUT/00-route-source.txt" 2>/dev/null || stop "출력 파일을 쓸 수 없음($OUT)"
for ip in "${WORKERS[@]}"; do
  route_rc=0
  ip -4 route get "$ip" > "$OUT/route-$ip.stdout" 2> "$OUT/route-$ip.stderr" || route_rc=$?
  printf '%s\n' "$route_rc" > "$OUT/route-$ip.rc" || stop "route rc 기록 실패"
  [ "$route_rc" -eq 0 ] || stop "$ip route 조회 실패(rc=$route_rc)"
  # Parse only successful output. A second src (even the same value) is ambiguous.
  src="$(awk '{for (i=1;i<=NF;i++) if ($i=="src") {n++; value=$(i+1)}} END {if(n==1 && value!="") print value; else exit 1}' "$OUT/route-$ip.stdout")" || stop "$ip route src 비어 있음/복수/모호"
  w "$OUT/00-route-source.txt" "$ip route_src=$src expected=$EXP_SRC rc=$route_rc"
  [ "$INC" -eq 0 ] || stop "route 증적 쓰기 실패"
  [ "$src" = "$EXP_SRC" ] || stop "$ip 로 가는 route src='$src' 이 예상($EXP_SRC)과 다름 - Source 불일치, 접속 전 중단"
done
{ echo "# rev: $REV"; echo "# collected_at_utc: $(date -u +%Y-%m-%dT%H:%M:%SZ)"; echo "# host: $HN  role: $ROLE  expected_src: $EXP_SRC";
  echo "# source_ips: $(hostname -I 2>/dev/null)"; echo "# workers: ${WORKERS[*]}  neg_ports: ${NEG_PORTS[*]}  control_port: $CTRL_PORT  timeout_s: $TMO"; } > "$OUT/00-environment.txt" || incomplete "00-environment.txt 쓰기 실패"
w "$OUT/00-environment.txt" "# script_sha256: $(sha256sum "$0" 2>/dev/null | awk '{print $1}')"

RES="$OUT/results.tsv"
printf 'row_id\tkind\ttarget\tport\tactual\trc\telapsed_ms\tstderr\n' > "$RES" || incomplete "results.tsv 쓰기 실패"

# 접속 1건. 인자는 bash -c 에 문자열로 합치지 않고 위치 인자로 넘긴다. 결과: ACT, RC, EL, ERRTXT
probe() {  # host port
  local s e errf="$OUT/.stderr.$$"
  s=$(date +%s%N)
  timeout "$TMO" bash -c 'exec 3<>"/dev/tcp/$1/$2"' _ "$1" "$2" 2> "$errf"
  RC=$?
  e=$(date +%s%N); EL=$(( (e - s) / 1000000 ))
  ERRTXT="$(head -c 160 "$errf" 2>/dev/null | tr '\n\t' '  ')"; rm -f "$errf"
  case "$RC" in
    0)   ACT=CONNECTED ;;
    124) ACT=TIMEOUT ;;
    *)   if echo "$ERRTXT" | grep -qi 'refused'; then ACT=REFUSED; else ACT=ERROR; fi ;;
  esac
}
declare -A CTRL_OK    # worker -> 1 if control CONNECTED
declare -A ROWS       # row_id -> actual (중복 검출)
DUP=0
record() {  # kind target port actual
  local id="$ROLE|$2|$1|$3"
  if [ -n "${ROWS[$id]+x}" ]; then DUP=$((DUP+1)); incomplete "중복 행: $id"; fi
  ROWS[$id]="$4"
  printf '%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\n' "$id" "$1" "$2" "$3" "$4" "$RC" "$EL" "$ERRTXT" >> "$RES" 2>/dev/null || incomplete "results.tsv 기록 실패: $id"
}

# ---- 시험(Worker 별로 대조 먼저, 이어서 차단 기대 포트) ----
for ip in "${WORKERS[@]}"; do
  probe "$ip" "$CTRL_PORT"; record control "$ip" "$CTRL_PORT" "$ACT"
  [ "$ACT" = CONNECTED ] && CTRL_OK[$ip]=1 || CTRL_OK[$ip]=0
  for port in "${NEG_PORTS[@]}"; do
    probe "$ip" "$port"; record negative "$ip" "$port" "$ACT"
  done
done

# ---- 행 집합 검증(정확히 12행: 대조 3 + 차단 9, 누락·중복 없음) ----
NROWS=$(awk 'NR>1' "$RES" 2>/dev/null | wc -l)
[ "$NROWS" -eq "$EXPECT_ROWS" ] || incomplete "행 수 $NROWS != 기대 $EXPECT_ROWS"
for ip in "${WORKERS[@]}"; do
  [ -n "${ROWS[$ROLE|$ip|control|$CTRL_PORT]+x}" ] || incomplete "대조 행 누락: $ip:$CTRL_PORT"
  for port in "${NEG_PORTS[@]}"; do [ -n "${ROWS[$ROLE|$ip|negative|$port]+x}" ] || incomplete "차단 행 누락: $ip:$port"; done
done
NNEG=$(awk -F'\t' 'NR>1&&$2=="negative"' "$RES" 2>/dev/null | wc -l)
[ "$NNEG" -eq $(( ${#WORKERS[@]} * ${#NEG_PORTS[@]} )) ] || incomplete "차단 기대 행 수 $NNEG != $(( ${#WORKERS[@]} * ${#NEG_PORTS[@]} ))"

# ---- 판정(차단 기대 행별) ----
VALID=0 FAILN=0 INCONC=0
: > "$OUT/verdict-rows.tsv" 2>/dev/null || incomplete "verdict-rows.tsv 쓰기 실패"
for ip in "${WORKERS[@]}"; do
  for port in "${NEG_PORTS[@]}"; do
    act="${ROWS[$ROLE|$ip|negative|$port]:-MISSING}"
    note=""
    if [ "$act" = CONNECTED ]; then v=FAIL; FAILN=$((FAILN+1))
    elif [ "$act" = TIMEOUT ] && [ "${CTRL_OK[$ip]:-0}" = 1 ]; then v=VALID_BLOCK; VALID=$((VALID+1))
    else
      v=INCONCLUSIVE; INCONC=$((INCONC+1))
      if [ "$act" = TIMEOUT ]; then
        note="대조 불충족: 같은 Worker 의 대조($CTRL_PORT)가 CONNECTED 가 아님 - TIMEOUT 은 관측값으로만 보존"
        [ "$ROLE" = edge ] && [ "$ip" = 10.1.93.108 ] && note="$note; worker03 edge 대조는 Local endpoint 부재(알려진 한계, #15), 다른 Worker 성공으로 승격하지 않음"
      else note="$act 는 차단 PASS 로 읽지 않음"; fi
    fi
    printf '%s\t%s\t%s\t%s\t%s\n' "$ROLE|$ip|negative|$port" "$act" "$v" "control=${CTRL_OK[$ip]:-0}" "$note" >> "$OUT/verdict-rows.tsv" 2>/dev/null || incomplete "verdict 기록 실패"
  done
done

if [ "$INC" -gt 0 ]; then STATUS=INCOMPLETE; else STATUS=COMPLETE; fi
if [ "$STATUS" = INCOMPLETE ]; then VERDICT=NOT_EVALUATED
elif [ "$FAILN" -gt 0 ]; then VERDICT=FAIL
elif [ "$INCONC" -gt 0 ]; then VERDICT=INCONCLUSIVE
else VERDICT=BLOCKED_VALID; fi
{ echo "PROVISIONAL=true SEAL_STATUS=PENDING"
  echo "COLLECTOR_STATUS=$STATUS rows=$NROWS/$EXPECT_ROWS incomplete_reasons=$INC"
  echo "AC_VERDICT=$VERDICT valid_block=$VALID fail=$FAILN inconclusive=$INCONC (차단 기대 9행)"
  echo "scope: source=$ROLE($EXP_SRC) workers=${WORKERS[*]} neg_ports=${NEG_PORTS[*]} control=$CTRL_PORT rev=$REV"; } > "$OUT/99-summary-provisional.txt" || incomplete "99-summary-provisional.txt 쓰기 실패"
cat "$OUT/99-summary-provisional.txt" 2>/dev/null
awk -F'\t' '$3!="VALID_BLOCK"{print $3 ": " $1 " actual=" $2 " " $5}' "$OUT/verdict-rows.tsv" 2>/dev/null

# ---- Seal once; completion is external, so archive members never change after seal. ----
[ "$INC" -eq 0 ] || { STATUS=INCOMPLETE; VERDICT=NOT_EVALUATED; }
SEALOK=1
( cd "$OUT" && files=(); for f in *; do [ "$f" = SHA256SUMS ] || files+=("$f"); done
  sha256sum -- "${files[@]}" > SHA256SUMS && sha256sum -c SHA256SUMS >/dev/null ) || SEALOK=0
if [ "$SEALOK" = 1 ]; then
  tar czf "$OUT.tar.gz" "$OUT" && [ -s "$OUT.tar.gz" ] || SEALOK=0
fi
if [ "$SEALOK" = 1 ]; then
  sha256sum "$OUT.tar.gz" > "$OUT.tar.gz.sha256" && [ -s "$OUT.tar.gz.sha256" ] && sha256sum -c "$OUT.tar.gz.sha256" >/dev/null || SEALOK=0
fi
if [ "$SEALOK" != 1 ]; then
  echo "INCOMPLETE: 파일 봉인(SHA256SUMS/tar/sha256) 실패" >&2
  STATUS=INCOMPLETE
fi
FINAL_RC=12
if [ "$STATUS" = COMPLETE ]; then
  case "$VERDICT" in BLOCKED_VALID) FINAL_RC=0;; INCONCLUSIVE) FINAL_RC=10;; FAIL) FINAL_RC=11;; esac
fi
SEAL_STATUS=COMPLETE; [ "$SEALOK" = 1 ] || SEAL_STATUS=INCOMPLETE
if ! printf 'COLLECTOR_STATUS=%s\nAC_VERDICT=%s\nSEAL_STATUS=%s\nEXIT_CODE=%s\nARCHIVE=%s\n' "$STATUS" "$VERDICT" "$SEAL_STATUS" "$FINAL_RC" "$OUT.tar.gz" > "$OUT.completion.txt"; then
  STATUS=INCOMPLETE; FINAL_RC=12
  echo "INCOMPLETE: completion 기록 실패" >&2
fi
echo "FINAL COLLECTOR_STATUS=$STATUS AC_VERDICT=$VERDICT EXIT_CODE=$FINAL_RC"
echo "COMPLETION=$OUT.completion.txt"
exit "$FINAL_RC"
