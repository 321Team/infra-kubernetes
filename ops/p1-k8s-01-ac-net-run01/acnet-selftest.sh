#!/bin/bash
# LOCAL self-test (mock kubectl, mock sleep). Never run on servers. No API access, no cluster changes.
# Covers the failure paths PMO/QA (GWONUK) and supreme33-kr asked to be regression-tested:
#  create: namespace create timeout / AlreadyExists (race) / fixtures create failure / wait failure / happy path
#  cleanup: UID mismatch must exit non-zero / still-Terminating after the wait / non-NotFound error / leftover / listing failure
# Usage: bash acnet-selftest.sh      (exit 0 only if every scenario behaves as expected)
set -u
HERE=$(cd "$(dirname "$0")" && pwd)
T=$(mktemp -d); trap 'rm -rf "$T"' EXIT
BIN="$T/bin"; mkdir -p "$BIN"
printf '#!/bin/bash\nexit 0\n' > "$BIN/sleep"; chmod +x "$BIN/sleep"
cat > "$BIN/kubectl" <<'MOCK'
#!/bin/bash
a="$*"; echo "KUBECTL $a" >> "${MOCK_LOG:?}"
UIDV=${MOCK_UID:-11111111-2222-3333-4444-555555555555}
# The mock really PARSES the -f file: it counts documents and requires exactly one `kind:` per document (B57-NEW1).
fileof(){ echo "$a" | sed -E 's/.*-f ([^ ]+).*/\1/'; }
docs(){ awk 'BEGIN{d=1} /^---[ \t]*$/{d++; next} /^kind: /{k[d]++} END{n=0; bad=0; for(i in k){n++; if(k[i]!=1) bad=1} print n" "bad}' "$1"; }
structure_ok(){ [ "$(docs "$(fileof)")" = "$1" ]; }
case "$a" in
  *"delete --raw"*) cat >/dev/null; [ "${MOCK_DELETE_RC:-0}" = "0" ] || echo "Error from server (Conflict): precondition failed" >&2; exit "${MOCK_DELETE_RC:-0}";;
  *"create --dry-run=server -f "*ns.yaml*) structure_ok "1 0" || { echo "error: the namespace file is not exactly one document" >&2; exit 1; }; exit "${MOCK_NS_DRYRUN_RC:-0}";;
  *"create --dry-run=server -f "*rest.yaml*) structure_ok "6 0" || { echo "error: fixtures file is not exactly 6 separate documents" >&2; exit 1; }; exit "${MOCK_REST_DRYRUN_RC:-0}";;
  *"create -f "*ns.yaml*)
    structure_ok "1 0" || { echo "error: the namespace file is not exactly one document" >&2; exit 1; }
    case "${MOCK_NS_CREATE:-ok}" in
      ok) echo "$UIDV"; exit 0;;
      exists) echo 'Error from server (AlreadyExists): namespaces "ac-net-run01" already exists' >&2; exit 1;;
      timeout) echo 'Unable to connect to the server: net/http: TLS handshake timeout' >&2; exit 1;;
    esac;;
  *"create -f "*rest.yaml*) structure_ok "6 0" || { echo "error: fixtures file is not exactly 6 separate documents" >&2; exit 1; }; [ "${MOCK_REST_CREATE_RC:-0}" = "0" ] && echo "pod/probe-a created" || { echo "Error from server (Forbidden)" >&2; echo "pod/probe-a created"; }; exit "${MOCK_REST_CREATE_RC:-0}";;
  *" wait "*) exit "${MOCK_WAIT_RC:-0}";;
  *"get ns ac-net-run01 -o jsonpath={.metadata.uid}"*) [ "${MOCK_GETUID_RC:-0}" = "0" ] && echo "${MOCK_CUR_UID:-$UIDV}" || echo "Unable to connect" >&2; exit "${MOCK_GETUID_RC:-0}";;
  *"get ns ac-net-run01 -o jsonpath={.metadata.labels"*) echo -n "${MOCK_LABEL:-ac-net-run01}"; exit 0;;
  *"get ns ac-net-run01"*)
    case "${MOCK_NS_STATE:-gone}" in
      gone) echo 'Error from server (NotFound): namespaces "ac-net-run01" not found' >&2; exit 1;;
      terminating) echo "ac-net-run01   Terminating   1m"; exit 0;;
      error) echo "Unable to connect to the server: i/o timeout" >&2; exit 1;;
    esac;;
  *"get pods,svc -A -l"*) [ "${MOCK_LEFT_RC:-0}" = "0" ] && echo -n "${MOCK_LEFT_OUT:-}" || echo "Unable to connect" >&2; exit "${MOCK_LEFT_RC:-0}";;
  *"-n ac-net-run01 get pods,svc -o name"*) if [ "${MOCK_SET_RC:-0}" != "0" ]; then echo "Unable to connect" >&2; exit "$MOCK_SET_RC"; fi
     if [ "${MOCK_SET_EMPTY:-0}" = "1" ]; then echo "No resources found in ac-net-run01 namespace." >&2; exit 0; fi
     printf '%b' "${MOCK_SET:-pod/probe-a\npod/probe-b\npod/probe-c\nservice/probe-a-svc\nservice/probe-b-svc\nservice/probe-c-svc\n}"; exit 0;;
  *"-n ac-net-run01 get deploy,sts"*) [ "${MOCK_ALL_RC:-0}" = "0" ] && printf 'configmap/kube-root-ca.crt\nserviceaccount/default\n' || echo "Unable to connect" >&2; exit "${MOCK_ALL_RC:-0}";;
  *"-n ac-net-run01 get pods,svc -o wide"*|*"-n ac-net-run01 describe"*) echo "stub"; exit 0;;
  *"get nodes --no-headers"*) echo "k8s-control01 Ready"; exit 0;;
  *"-n fds-app get pods"*) echo "pod-a 1/1 Running 3 1d"; exit 0;;
  *"get --raw"*) echo "readyz check passed"; exit 0;;
  *) echo "stub"; exit 0;;
esac
MOCK
chmod +x "$BIN/kubectl"
MAN="$HERE/manifests/ac-net-run01.yaml"   # the REAL manifest (Namespace + 3 Pods + 3 Services)
fails=0; n=0
# run <name> <script> <expect: zero|nonzero> <must_not_log_regex|-> <must_have_out_regex|-> [VAR=val ...]
run(){
  local name=$1 script=$2 expect=$3 notlog=$4 haveout=$5; shift 5
  n=$((n+1)); local d="$T/s$n"; mkdir -p "$d"; : > "$d/log"
  local out rc
  out=$(env AC_NET_R="$d/r" AC_NET_K="$BIN/kubectl" AC_NET_WAIT_TRIES=3 MOCK_LOG="$d/log" PATH="$BIN:$PATH" "$@" bash "$HERE/$script" ${ARGS:-} 2>&1); rc=$?
  local ok=1
  if [ "$expect" = zero ]; then [ "$rc" = 0 ] || ok=0; else [ "$rc" != 0 ] || ok=0; fi
  if [ "$notlog" != "-" ] && grep -Eq "$notlog" "$d/log"; then ok=0; echo "   (mock log unexpectedly matches: $notlog)"; fi
  if [ "$haveout" != "-" ] && ! echo "$out" | grep -Eq "$haveout"; then ok=0; echo "   (output missing: $haveout)"; fi
  if [ "$ok" = 1 ]; then echo "PASS  $name (rc=$rc)"; else echo "FAIL  $name (rc=$rc, expected $expect)"; fails=$((fails+1)); echo "$out" | tail -5 | sed 's/^/      /'; fi
  LASTDIR="$d"
}
echo "== acnet-1-gate-create.sh"
ARGS="$MAN"
run "create: namespace create TIMEOUT -> stop, nothing else created" acnet-1-gate-create.sh nonzero 'create -f .*rest.yaml|apply' 'STOP: namespace create failed' MOCK_NS_CREATE=timeout
[ ! -e "$LASTDIR/r/ns-uid.txt" ] && echo "PASS  create: timeout recorded no UID" || { echo "FAIL  create: timeout recorded a UID"; fails=$((fails+1)); }
run "create: namespace AlreadyExists (race/pre-existing) -> stop, never touched" acnet-1-gate-create.sh nonzero 'create -f .*rest.yaml|apply' 'STOP: namespace create failed' MOCK_NS_CREATE=exists
[ ! -e "$LASTDIR/r/ns-uid.txt" ] && echo "PASS  create: AlreadyExists recorded no UID" || { echo "FAIL  create: AlreadyExists recorded a UID"; fails=$((fails+1)); }
run "create: namespace ok, fixtures create FAILS -> nonzero, UID recorded, cleanup hint" acnet-1-gate-create.sh nonzero 'apply' 'run acnet-9-cleanup.sh' MOCK_REST_CREATE_RC=1
[ "$(cat "$LASTDIR/r/ns-uid.txt" 2>/dev/null)" = "11111111-2222-3333-4444-555555555555" ] && echo "PASS  create: partial failure recorded this run's UID" || { echo "FAIL  create: UID not recorded after partial failure"; fails=$((fails+1)); }
run "create: fixtures dry-run FAILS -> nonzero" acnet-1-gate-create.sh nonzero 'create -f .*rest.yaml' 'dry-run failed' MOCK_REST_DRYRUN_RC=1
run "create: pods not Ready (wait fails) -> nonzero, no CREATE_COMPLETE" acnet-1-gate-create.sh nonzero 'apply' 'STOP: test Pods are not all Ready' MOCK_WAIT_RC=1
run "create: happy path -> rc 0 and CREATE_COMPLETE" acnet-1-gate-create.sh zero 'apply' 'CREATE_COMPLETE'
[ "$(cat "$LASTDIR/r/ns-uid.txt" 2>/dev/null)" = "11111111-2222-3333-4444-555555555555" ] && echo "PASS  create: happy path recorded the create-response UID" || { echo "FAIL  create: UID mismatch"; fails=$((fails+1)); }
# B57-NEW1: the REAL manifest must split into exactly 1 Namespace document + 6 separate fixture documents
run "create: real manifest splits into 1 Namespace + 6 fixtures (mock parses the files)" acnet-1-gate-create.sh zero 'apply' 'CREATE_COMPLETE'
D1="$LASTDIR/r"
echo "split check: ns=$(awk 'BEGIN{d=1} /^---[ 	]*$/{d++; next} /^kind: /{k[d]++} END{n=0;for(i in k)n++;print n}' "$D1/ns.yaml") rest=$(awk 'BEGIN{d=1} /^---[ 	]*$/{d++; next} /^kind: /{k[d]++} END{n=0;for(i in k)n++;print n}' "$D1/rest.yaml") pods=$(grep -c '^kind: Pod$' "$D1/rest.yaml") svcs=$(grep -c '^kind: Service$' "$D1/rest.yaml") seps=$(grep -c '^---' "$D1/rest.yaml")"
[ "$(grep -c '^kind: Pod$' "$D1/rest.yaml")" = 3 ] && [ "$(grep -c '^kind: Service$' "$D1/rest.yaml")" = 3 ] && [ "$(grep -c '^---' "$D1/rest.yaml")" = 5 ] && [ "$(grep -c '^kind: Namespace$' "$D1/rest.yaml")" = 0 ] && echo "PASS  create: rest.yaml = Pod3 + Service3 with 5 separators, no Namespace" || { echo "FAIL  create: split structure wrong"; fails=$((fails+1)); }
run "create: unexpected UID format from create -> stop, not recorded" acnet-1-gate-create.sh nonzero 'create -f .*rest.yaml' 'unexpected UID format' MOCK_UID=garbage
echo "== acnet-9-cleanup.sh"
ARGS=""
pre(){ mkdir -p "$1/r"; echo -n "11111111-2222-3333-4444-555555555555" > "$1/r/ns-uid.txt"; }
# prepare the UID file by running through a wrapper dir: run() creates $d, so pre-seed via AC_NET_R sibling
runc(){ local name=$1; shift; n=$((n+1)); local d="$T/s$n"; mkdir -p "$d/r"; echo -n "11111111-2222-3333-4444-555555555555" > "$d/r/ns-uid.txt"; : > "$d/log"
  local expect=$1 notlog=$2 haveout=$3; shift 3
  local out rc; out=$(env AC_NET_R="$d/r" AC_NET_K="$BIN/kubectl" AC_NET_WAIT_TRIES=3 MOCK_LOG="$d/log" PATH="$BIN:$PATH" "$@" bash "$HERE/acnet-9-cleanup.sh" 2>&1); rc=$?
  local ok=1; if [ "$expect" = zero ]; then [ "$rc" = 0 ] || ok=0; else [ "$rc" != 0 ] || ok=0; fi
  if [ "$notlog" != "-" ] && grep -Eq "$notlog" "$d/log"; then ok=0; echo "   (mock log unexpectedly matches: $notlog)"; fi
  if [ "$haveout" != "-" ] && ! echo "$out" | grep -Eq "$haveout"; then ok=0; echo "   (output missing: $haveout)"; fi
  if [ "$ok" = 1 ]; then echo "PASS  $name (rc=$rc)"; else echo "FAIL  $name (rc=$rc, expected $expect)"; fails=$((fails+1)); echo "$out" | tail -5 | sed 's/^/      /'; fi; }
runc "cleanup: UID MISMATCH -> STOP with NONZERO exit, no delete call" nonzero 'delete --raw' 'STOP: namespace UID mismatch' MOCK_CUR_UID=99999999-0000-0000-0000-000000000000
runc "cleanup: delete ok but namespace stays Terminating -> NONZERO CLEANUP_INCOMPLETE" nonzero '-' 'CLEANUP_INCOMPLETE: namespace still present' MOCK_NS_STATE=terminating
runc "cleanup: lookup error that is NOT NotFound -> NONZERO, not treated as absent" nonzero '-' 'something other than NotFound' MOCK_NS_STATE=error
runc "cleanup: clean path (NotFound, leftover 0) -> rc 0 CLEANUP_COMPLETE" zero '-' 'CLEANUP_COMPLETE' MOCK_NS_STATE=gone
runc "cleanup: leftover query FAILS -> NONZERO" nonzero '-' 'leftover query failed' MOCK_NS_STATE=gone MOCK_LEFT_RC=1
runc "cleanup: leftover resources exist -> NONZERO" nonzero '-' 'leftover resources still exist' MOCK_NS_STATE=gone 'MOCK_LEFT_OUT=pod/x'
runc "cleanup: PARTIAL creation - empty namespace (no pods/services) -> cleanable, rc 0 COMPLETE" zero '-' 'CLEANUP_COMPLETE' MOCK_NS_STATE=gone MOCK_SET_EMPTY=1
runc "cleanup: PARTIAL creation - only probe-a exists -> cleanable, rc 0 COMPLETE" zero '-' 'CLEANUP_COMPLETE' MOCK_NS_STATE=gone 'MOCK_SET=pod/probe-a
'
runc "cleanup: subset + one FOREIGN object -> refused, no delete" nonzero 'delete --raw' 'unexpected object in namespace: pod/foreign' 'MOCK_SET=pod/probe-a
pod/foreign
'
runc "cleanup: only a FOREIGN object -> refused, no delete" nonzero 'delete --raw' 'unexpected object in namespace: service/other' 'MOCK_SET=service/other
'
runc "cleanup: unexpected pod/service set -> STOP, no delete" nonzero 'delete --raw' 'unexpected object in namespace: pod/extra' 'MOCK_SET=pod/probe-a\npod/extra\n'
runc "cleanup: other-resource listing FAILS -> STOP, no delete" nonzero 'delete --raw' 'resource listing failed' MOCK_ALL_RC=1
runc "cleanup: UID read fails (timeout) -> STOP, no delete" nonzero 'delete --raw' 'cannot read the namespace UID' MOCK_GETUID_RC=1
runc "cleanup: UID-precondition delete refused -> NONZERO" nonzero '-' 'precondition delete refused' MOCK_DELETE_RC=1
n=$((n+1)); d="$T/s$n"; mkdir -p "$d/r"; : > "$d/log"
out=$(env AC_NET_R="$d/r" AC_NET_K="$BIN/kubectl" MOCK_LOG="$d/log" PATH="$BIN:$PATH" bash "$HERE/acnet-9-cleanup.sh" 2>&1); rc=$?
if [ "$rc" != 0 ] && echo "$out" | grep -q 'no recorded namespace UID'; then echo "PASS  cleanup: no recorded UID -> NONZERO (rc=$rc)"; else echo "FAIL  cleanup: no recorded UID (rc=$rc)"; fails=$((fails+1)); fi
echo "== result: $fails failure(s)"
exit $((fails>0))
