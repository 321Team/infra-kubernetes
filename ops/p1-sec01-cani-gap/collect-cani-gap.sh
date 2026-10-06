#!/usr/bin/env bash
# P1-SEC-01 can-i 보완 수집기 v3 (읽기 전용). "kubectl auth can-i --as" 만 실행한다. 아무것도 만들거나 바꾸지 않는다.
# 대상: 기존 증적에 "종료코드(RC)로 검증된" 동등 행이 없는 행만 (#107 의 90행 중 #49 verify-rbac 와 겹치는 9행 제외 = 81행).
#   TIER=A  (기본) Runner 25행 + monitoring-node:default 28행 = 53행
#   TIER=AB          위 + node-exporter-sa 28행 = 81행
# v1(collect-cani-core36b.sh) 결함: grep/head 로 yes/no 만 뽑고 RC·stderr 를 버려 오류가 PASS 로 기록될 수 있었다. 기존 증적은 수정하지 않는다.
# 판정: yes<->rc0, no<->rc1 인 쌍만 응답. 그 외는 ERROR, rc124 는 TIMEOUT. 모든 행에 RC 를 기록한다.
# 사용(control01):  sudo -v 후  KUBECTL='sudo kubectl --kubeconfig=/etc/kubernetes/admin.conf' [TIER=AB] bash collect-cani-gap.sh
# 종료코드: 0 전부 기대와 일치 / 10 FAIL 있음 / 11 ERROR·TIMEOUT 있음 / 12 행 누락·중복 / 13 봉인 실패 / 2 사전 점검 실패 / 3 파일 쓰기 실패
set -uo pipefail
REV=v3-20261006
TIER="${TIER:-A}"
KUBECTL="${KUBECTL:-sudo kubectl --kubeconfig=/etc/kubernetes/admin.conf}"
CHECK_TIMEOUT_SEC="${CHECK_TIMEOUT_SEC:-10}"
OUT="${OUT:-p1-sec01-cani-gap-v3-$(date -u +%Y%m%dT%H%M%SZ)}"
die() { echo "ERROR: $*" >&2; exit 3; }
mkdir -p "$OUT/raw" || die "출력 폴더 생성 실패: $OUT"
R="$OUT/results.tsv"
printf 'id\tresult\texpect\tactual\trc\tsubject\tns\tverb\tresource\tsub\tstderr_summary\n' > "$R" || die "TSV 초기화 실패"
PROM=system:serviceaccount:monitoring-api:prometheus-sa

ROWS_ALL=$(cat <<'ROWS'
R001|A|no|monitoring-node:default|monitoring-node|create|deployments|
R002|A|no|monitoring-node:default|monitoring-node|create|statefulsets|
R003|A|no|monitoring-node:default|monitoring-node|create|daemonsets|
R004|A|no|monitoring-node:default|monitoring-node|create|jobs|
R005|A|no|monitoring-node:default|monitoring-node|create|cronjobs|
R006|A|no|monitoring-node:default|monitoring-node|create|replicasets|
R007|A|no|monitoring-node:default|monitoring-node|create|pods|
R008|A|no|monitoring-node:default|monitoring-node|create|pods|exec
R009|A|no|monitoring-node:default|monitoring-node|create|pods|attach
R010|A|no|monitoring-node:default|monitoring-node|create|pods|portforward
R011|A|no|monitoring-node:default|monitoring-node|update|daemonsets|
R012|A|no|monitoring-node:default|monitoring-node|update|deployments|
R013|A|no|monitoring-node:default|monitoring-node|patch|daemonsets|
R014|A|no|monitoring-node:default|monitoring-node|patch|deployments|
R015|A|no|monitoring-node:default|monitoring-node|delete|daemonsets|
R016|A|no|monitoring-node:default|monitoring-node|delete|deployments|
R017|A|no|monitoring-node:default|monitoring-node|create|serviceaccounts|token
R018|A|no|monitoring-node:default|monitoring-node|create|rolebindings|
R019|A|no|monitoring-node:default|monitoring-node|create|roles|
R020|A|no|monitoring-node:default|monitoring-node|get|secrets|
R021|A|no|monitoring-node:default|monitoring-node|list|secrets|
R022|A|no|monitoring-node:default|monitoring-node|*|*|
R023|A|no|monitoring-node:default|-|create|clusterrolebindings|
R024|A|no|monitoring-node:default|-|bind|clusterroles|
R025|A|no|monitoring-node:default|-|escalate|clusterroles|
R026|A|no|monitoring-node:default|-|impersonate|users|
R027|A|no|monitoring-node:default|-|impersonate|groups|
R028|A|no|monitoring-node:default|-|impersonate|serviceaccounts|
R029|A|yes|ci-cd:cicd-runner-sa|fds-app|get|deployment/transaction-api|
R030|A|yes|ci-cd:cicd-runner-sa|fds-app|patch|deployment/transaction-api|
R031|A|no|ci-cd:cicd-runner-sa|fds-app|delete|deployment/transaction-api|
R032|A|yes|ci-cd:cicd-runner-sa|fds-app|get|deployment/fds-engine|
R033|A|yes|ci-cd:cicd-runner-sa|fds-app|update|deployment/fds-engine|
R034|A|yes|ci-cd:cicd-runner-sa|fds-app|patch|deployment/fds-engine|
R035|A|no|ci-cd:cicd-runner-sa|fds-app|delete|deployment/fds-engine|
R036|A|no|ci-cd:cicd-runner-sa|fds-app|get|deployment/other-app|
R037|A|no|ci-cd:cicd-runner-sa|fds-app|patch|deployment/other-app|
R038|A|no|ci-cd:cicd-runner-sa|fds-app|delete|deployment/other-app|
R039|A|no|ci-cd:cicd-runner-sa|fds-app|list|deployments|
R040|A|no|ci-cd:cicd-runner-sa|fds-app|create|statefulsets|
R041|A|no|ci-cd:cicd-runner-sa|fds-app|create|daemonsets|
R042|A|no|ci-cd:cicd-runner-sa|fds-app|create|jobs|
R043|A|no|ci-cd:cicd-runner-sa|fds-app|create|replicasets|
R044|A|no|ci-cd:cicd-runner-sa|fds-app|create|pods|
R045|A|no|ci-cd:cicd-runner-sa|fds-app|update|daemonsets|
R046|A|no|ci-cd:cicd-runner-sa|fds-app|update|statefulsets|
R047|A|no|ci-cd:cicd-runner-sa|-|create|clusterrolebindings|
R048|A|no|ci-cd:cicd-runner-sa|-|bind|clusterroles|
R049|A|no|ci-cd:cicd-runner-sa|-|escalate|clusterroles|
R050|A|no|ci-cd:cicd-runner-sa|-|impersonate|users|
R051|A|no|ci-cd:cicd-runner-sa|-|impersonate|serviceaccounts|
R052|A|no|ci-cd:cicd-runner-sa|kube-system|get|deployments|
R053|A|no|ci-cd:cicd-runner-sa|monitoring-api|get|deployments|
R054|B|no|monitoring-node:node-exporter-sa|monitoring-node|create|deployments|
R055|B|no|monitoring-node:node-exporter-sa|monitoring-node|create|statefulsets|
R056|B|no|monitoring-node:node-exporter-sa|monitoring-node|create|daemonsets|
R057|B|no|monitoring-node:node-exporter-sa|monitoring-node|create|jobs|
R058|B|no|monitoring-node:node-exporter-sa|monitoring-node|create|cronjobs|
R059|B|no|monitoring-node:node-exporter-sa|monitoring-node|create|replicasets|
R060|B|no|monitoring-node:node-exporter-sa|monitoring-node|create|pods|
R061|B|no|monitoring-node:node-exporter-sa|monitoring-node|create|pods|exec
R062|B|no|monitoring-node:node-exporter-sa|monitoring-node|create|pods|attach
R063|B|no|monitoring-node:node-exporter-sa|monitoring-node|create|pods|portforward
R064|B|no|monitoring-node:node-exporter-sa|monitoring-node|update|daemonsets|
R065|B|no|monitoring-node:node-exporter-sa|monitoring-node|update|deployments|
R066|B|no|monitoring-node:node-exporter-sa|monitoring-node|patch|daemonsets|
R067|B|no|monitoring-node:node-exporter-sa|monitoring-node|patch|deployments|
R068|B|no|monitoring-node:node-exporter-sa|monitoring-node|delete|daemonsets|
R069|B|no|monitoring-node:node-exporter-sa|monitoring-node|delete|deployments|
R070|B|no|monitoring-node:node-exporter-sa|monitoring-node|create|serviceaccounts|token
R071|B|no|monitoring-node:node-exporter-sa|monitoring-node|create|rolebindings|
R072|B|no|monitoring-node:node-exporter-sa|monitoring-node|create|roles|
R073|B|no|monitoring-node:node-exporter-sa|monitoring-node|get|secrets|
R074|B|no|monitoring-node:node-exporter-sa|monitoring-node|list|secrets|
R075|B|no|monitoring-node:node-exporter-sa|monitoring-node|*|*|
R076|B|no|monitoring-node:node-exporter-sa|-|create|clusterrolebindings|
R077|B|no|monitoring-node:node-exporter-sa|-|bind|clusterroles|
R078|B|no|monitoring-node:node-exporter-sa|-|escalate|clusterroles|
R079|B|no|monitoring-node:node-exporter-sa|-|impersonate|users|
R080|B|no|monitoring-node:node-exporter-sa|-|impersonate|groups|
R081|B|no|monitoring-node:node-exporter-sa|-|impersonate|serviceaccounts|
ROWS
)
case "$TIER" in
  A)  ROWS=$(printf '%s\n' "$ROWS_ALL" | awk -F'|' '$2=="A"') ;;
  AB) ROWS="$ROWS_ALL" ;;
  *)  die "TIER 는 A 또는 AB" ;;
esac
EXPECTED_N=$(printf '%s\n' "$ROWS" | grep -c .)
{
  echo "# rev: $REV"; echo "# tier: $TIER expected_rows: $EXPECTED_N (+2 record-only: M001·M002)"
  echo "# collected_at_utc: $(date -u +%FT%TZ)"; echo "# host: $(hostname)"
  echo "# script_sha256: $(sha256sum "$0" | cut -d' ' -f1)"
  echo "# check_timeout_sec: $CHECK_TIMEOUT_SEC"
  $KUBECTL version 2>&1 || echo "(kubectl version rc=$?)"
} > "$OUT/00-environment.txt" || die "환경 파일 쓰기 실패"

# 사전 점검: 관리자 yes/rc0, 존재하지 않는 SA 의 no/rc1 이 각각 정상으로 나와야 한다
pre_yes="$(timeout "${CHECK_TIMEOUT_SEC}s" $KUBECTL auth can-i get pods -n kube-system 2>"$OUT/00-pre-yes.stderr")"; pre_yes_rc=$?
pre_no="$(timeout "${CHECK_TIMEOUT_SEC}s" $KUBECTL auth can-i get pods -n kube-system --as=system:serviceaccount:default:nonexistent-sa-for-precheck 2>"$OUT/00-pre-no.stderr")"; pre_no_rc=$?
printf 'pre_yes=%s rc=%s\npre_no=%s rc=%s\n' "$pre_yes" "$pre_yes_rc" "$pre_no" "$pre_no_rc" > "$OUT/00-precheck.txt" || die "사전 점검 기록 실패"
if [ "$pre_yes" != yes ] || [ "$pre_yes_rc" != 0 ] || [ "$pre_no" != no ] || [ "$pre_no_rc" != 1 ]; then
  echo "사전 점검 실패 (00-precheck.txt 참조). 시험을 시작하지 않는다."; exit 2
fi

write_row() { printf '%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\n' "$@" >> "$R" || die "TSV 쓰기 실패($R): 이 시점까지의 기록을 신뢰할 수 없어 즉시 중단"; }

check() {  # id exp sa ns verb res sub   (ns "-" => -A, "NONRES" => 비리소스 URL)
  local id="$1" exp="$2" sa="system:serviceaccount:$3" ns="$4" verb="$5" res="$6" sub="$7" args out rc act r err
  args=(auth can-i "$verb" "$res" --as="$sa")
  if [ "$ns" = "-" ]; then args+=(-A); elif [ "$ns" != "NONRES" ]; then args+=(-n "$ns"); fi
  [ -n "$sub" ] && args+=(--subresource="$sub")
  timeout "${CHECK_TIMEOUT_SEC}s" $KUBECTL "${args[@]}" >"$OUT/raw/$id.stdout" 2>"$OUT/raw/$id.stderr"; rc=$?
  [ -f "$OUT/raw/$id.stdout" ] && [ -f "$OUT/raw/$id.stderr" ] || die "raw 출력 파일 생성 실패 $id"
  printf '%s\n' "$rc" > "$OUT/raw/$id.rc" || die "raw rc 쓰기 실패 $id"
  out="$(cat "$OUT/raw/$id.stdout")" || die "raw 읽기 실패 $id"
  err="$(head -c 200 "$OUT/raw/$id.stderr" | tr '\n\t' '  ')"
  if [ "$rc" = 124 ]; then act=TIMEOUT
  elif [ "$out" = yes ] && [ "$rc" = 0 ]; then act=yes
  elif [ "$out" = no ] && [ "$rc" = 1 ]; then act=no
  else act=ERROR; [ -z "$err" ] && err="(stdout='${out}' rc=${rc}: yes/rc0 또는 no/rc1 아님)"; fi
  if [ "$act" = TIMEOUT ] || [ "$act" = ERROR ]; then r=$act
  elif [ "$exp" = "-" ]; then r=RECORD
  elif [ "$act" = "$exp" ]; then r=PASS
  else r=FAIL; fi
  write_row "$id" "$r" "$exp" "$act" "$rc" "$sa" "$ns" "$verb" "$res" "$sub" "$err"
}

while IFS='|' read -r id tier exp sa ns verb res sub; do
  [ -z "$id" ] && continue
  check "$id" "$exp" "$sa" "$ns" "$verb" "$res" "$sub"
done <<< "$ROWS"

# B19-RBAC-01: prometheus-sa 의 현재 /metrics (기대값 미지정 = 응답만 기록) 와 적용된 Binding/Role 연결 원본
check M001 "-" "monitoring-api:prometheus-sa" NONRES get /metrics ""
check M002 "-" "monitoring-api:prometheus-sa" - get nodes metrics
for k in clusterrolebindings rolebindings clusterroles roles; do
  a=(get "$k"); [[ "$k" == rolebindings || "$k" == roles ]] && a+=(-A); a+=(-o wide)
  timeout "${CHECK_TIMEOUT_SEC}s" $KUBECTL "${a[@]}" > "$OUT/20-$k.txt" 2> "$OUT/20-$k.stderr"; echo $? > "$OUT/20-$k.rc"
done
timeout "${CHECK_TIMEOUT_SEC}s" $KUBECTL get clusterrolebindings -o json > "$OUT/21-clusterrolebindings.json" 2> "$OUT/21-clusterrolebindings.stderr"; echo $? > "$OUT/21-clusterrolebindings.rc"

# 보조 수집(Binding/Role) 의 비0 RC 개수: 판정에는 쓰지 않지만 summary·completion 에 노출한다
AUX=0; for f in "$OUT"/20-*.rc "$OUT"/21-*.rc; do [ "$(cat "$f" 2>/dev/null)" = 0 ] || AUX=$((AUX+1)); done
# 행 누락·중복 검사
GOT=$(awk -F'\t' 'NR>1&&$1 ~ /^R/' "$R" | wc -l)
UNIQ=$(awk -F'\t' 'NR>1{print $1}' "$R" | sort | uniq | wc -l)
TOTAL=$(awk -F'\t' 'NR>1' "$R" | wc -l)
P=$(awk -F'\t' 'NR>1&&$2=="PASS"' "$R" | wc -l); F=$(awk -F'\t' 'NR>1&&$2=="FAIL"' "$R" | wc -l)
E=$(awk -F'\t' 'NR>1&&($2=="ERROR"||$2=="TIMEOUT")' "$R" | wc -l); O=$(awk -F'\t' 'NR>1&&$2=="RECORD"' "$R" | wc -l)
RC=0; STATUS=COMPLETE
[ "$F" -gt 0 ] && RC=10
[ "$E" -gt 0 ] && RC=11
if [ "$GOT" != "$EXPECTED_N" ] || [ "$UNIQ" != "$TOTAL" ]; then RC=12; STATUS=INCOMPLETE; fi
{
  echo "EXPECTED=$EXPECTED_N GOT=$GOT TOTAL=$TOTAL UNIQUE_IDS=$UNIQ PASS=$P FAIL=$F ERROR_OR_TIMEOUT=$E RECORD_ONLY=$O AUX_RC_NONZERO=$AUX"
  awk -F'\t' 'NR>1&&($2=="FAIL"||$2=="ERROR"||$2=="TIMEOUT"){print $2 ": " $1 " " $6 " " $8 " " $9 " " $10 " (expect " $3 ", actual " $4 ", rc " $5 ")"}' "$R"
} | tee "$OUT/99-summary-provisional.txt" || die "요약 쓰기 실패"

# 봉인: 실패하면 완료로 보고하지 않는다
SEAL=COMPLETE
( cd "$OUT" && find . -type f ! -name SHA256SUMS | sort | xargs sha256sum > SHA256SUMS ) || SEAL=FAILED
tar czf "$OUT.tar.gz" "$OUT" 2>"$OUT.tar.err" || SEAL=FAILED
sha256sum "$OUT.tar.gz" > "$OUT.tar.gz.sha256" 2>/dev/null || SEAL=FAILED
[ "$SEAL" = FAILED ] && { RC=13; echo "봉인 실패: 이 실행을 완료로 보고하지 않는다" >&2; }
{ echo "COLLECTOR_STATUS=$STATUS"; echo "SEAL_STATUS=$SEAL"; echo "EXIT_CODE=$RC"; echo "AUX_RC_NONZERO=$AUX"; echo "ARCHIVE=$OUT.tar.gz"; } > "$OUT.completion.txt" || die "완료 기록 쓰기 실패"
cat "$OUT.completion.txt" || die "완료 기록 출력 실패"
if [ "$SEAL" = COMPLETE ]; then
  cat "$OUT.tar.gz.sha256" || die "archive sidecar 출력 실패"
fi
exit $RC
