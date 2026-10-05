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
  *"-n fds-app get secret harbor-pull-secret -o jsonpath={.type}"*) [ "${MOCK_SRC_SECRET_RC:-0}" = "0" ] && echo -n "${MOCK_SRC_SECRET_TYPE:-kubernetes.io/dockerconfigjson}" || echo "Error from server (Forbidden)" >&2; exit "${MOCK_SRC_SECRET_RC:-0}";;
  *"-n fds-app get secret harbor-pull-secret -o jsonpath={.data"*)
     # call 1 = the copy pipeline (always succeeds); call 2+ = the verification GET, which the MOCK_V* knobs can break (B62-1)
     N=$(grep -cF 'fds-app get secret harbor-pull-secret -o jsonpath={.data' "$MOCK_LOG")
     if [ "$N" -ge 2 ]; then
       [ -z "${MOCK_V_STDOUT_ON_FAIL:-}" ] || [ "${MOCK_VSRC_RC:-0}" = "0" ] || echo -n "${MOCK_SECRET_B64:-U0VDUkVUVkFMVUU=}"
       [ "${MOCK_VSRC_RC:-0}" = "0" ] || { echo "Error from server (Forbidden)" >&2; exit "$MOCK_VSRC_RC"; }
       [ "${MOCK_VSRC_OUT:-}" = "__EMPTY__" ] && exit 0
       echo -n "${MOCK_VSRC_OUT:-${MOCK_SECRET_B64:-U0VDUkVUVkFMVUU=}}"; exit 0
     fi
     echo -n "${MOCK_SECRET_B64:-U0VDUkVUVkFMVUU=}"; exit 0;;
  *"-n ac-net-run01 create secret generic harbor-pull-secret"*) IN=$(cat); [ "$IN" = "SECRETVALUE" ] || { echo "mock: unexpected stdin for the secret copy" >&2; exit 9; }; [ "${MOCK_SECRET_CREATE_RC:-0}" = "0" ] || echo "Error from server (Forbidden)" >&2; exit "${MOCK_SECRET_CREATE_RC:-0}";;
  *"-n ac-net-run01 label secret"*) exit "${MOCK_LABEL_RC:-0}";;
  *"-n ac-net-run01 get secret harbor-pull-secret -o jsonpath={.data"*)
     [ -z "${MOCK_V_STDOUT_ON_FAIL:-}" ] || [ "${MOCK_VCPY_RC:-0}" = "0" ] || echo -n "${MOCK_SECRET_B64:-U0VDUkVUVkFMVUU=}"
     [ "${MOCK_VCPY_RC:-0}" = "0" ] || { echo "Error from server (Forbidden)" >&2; exit "$MOCK_VCPY_RC"; }
     [ "${MOCK_VCPY_OUT:-}" = "__EMPTY__" ] && exit 0
     echo -n "${MOCK_VCPY_OUT:-${MOCK_COPY_B64:-${MOCK_SECRET_B64:-U0VDUkVUVkFMVUU=}}}"; exit 0;;
  *"-n ac-net-run01 get deploy,sts"*) [ "${MOCK_ALL_RC:-0}" = "0" ] && printf 'configmap/kube-root-ca.crt\nserviceaccount/default\n%b' "${MOCK_ALL_EXTRA:-}" || echo "Unable to connect" >&2; exit "${MOCK_ALL_RC:-0}";;
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
  LASTDIR="$d"; LASTOUT="$out"
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
# Pull secret copy (kubelet records the secrets an image was pulled with; Pods without a matching secret get ErrImageNeverPull)
run "secret: copy happens and is verified; the value is never printed" acnet-1-gate-create.sh zero '-' 'secret copy verified: type=kubernetes.io/dockerconfigjson'
if echo "$LASTOUT" | grep -Eq 'SECRETVALUE|U0VDUkVUVkFMVUU='; then echo "FAIL  secret: the value or its base64 appears in the output"; fails=$((fails+1)); else echo "PASS  secret: neither the value nor its base64 appears in the output"; fi
SECL=$(grep -n 'create secret generic' "$LASTDIR/log" | head -1 | cut -d: -f1); FIXL=$(grep -n 'create -f .*rest.yaml' "$LASTDIR/log" | grep -v dry-run | head -1 | cut -d: -f1)
[ -n "$SECL" ] && [ -n "$FIXL" ] && [ "$SECL" -lt "$FIXL" ] && echo "PASS  secret: created BEFORE the fixtures (log lines $SECL < $FIXL)" || { echo "FAIL  secret: not created before the fixtures ($SECL / $FIXL)"; fails=$((fails+1)); }
run "secret: source secret not readable -> STOP before fixtures" acnet-1-gate-create.sh nonzero 'create -f .*rest.yaml' 'source pull secret is not readable' MOCK_SRC_SECRET_RC=1
run "secret: source secret has the wrong type -> STOP before fixtures" acnet-1-gate-create.sh nonzero 'create -f .*rest.yaml' 'unexpected type' MOCK_SRC_SECRET_TYPE=Opaque
run "secret: copy create FAILS -> STOP, cleanup hint, no fixtures" acnet-1-gate-create.sh nonzero 'create -f .*rest.yaml' 'pull secret copy failed' MOCK_SECRET_CREATE_RC=1
run "secret: label fails -> STOP before fixtures" acnet-1-gate-create.sh nonzero 'create -f .*rest.yaml' 'labelling the copied secret failed' MOCK_LABEL_RC=1
run "secret: copied data differs from the source -> STOP before fixtures" acnet-1-gate-create.sh nonzero 'create -f .*rest.yaml' 'secret verification failed' MOCK_COPY_B64=WFhY
# B62-1: a failing verification GET must never be reported as "data identical" (empty strings used to hash equal)
vfail(){ local name=$1; shift
  run "$name" acnet-1-gate-create.sh nonzero 'create -f .*rest.yaml' 'secret verification failed' "$@"
  if echo "$LASTOUT" | grep -Eq 'secret copy verified|CREATE_COMPLETE|SECRETVALUE|U0VDUkVUVkFMVUU='; then echo "FAIL  $name: verified/CREATE_COMPLETE/value appears in the output"; fails=$((fails+1)); else echo "PASS  $name: no 'verified', no CREATE_COMPLETE, no value"; fi
  [ "$(cat "$LASTDIR/r/ns-uid.txt" 2>/dev/null)" = "11111111-2222-3333-4444-555555555555" ] && echo "PASS  $name: namespace UID preserved for cleanup" || { echo "FAIL  $name: namespace UID not preserved"; fails=$((fails+1)); }
}
vfail "verify: source GET fails only (rc 42)" MOCK_VSRC_RC=42
vfail "verify: copy GET fails only (rc 42)" MOCK_VCPY_RC=42
vfail "verify: BOTH GETs fail with empty stdout (the reproduced bug)" MOCK_VSRC_RC=42 MOCK_VCPY_RC=42
grep -q 'secret_verify_source_rc=42 copy_rc=42 match=0' "$LASTDIR/r/2-apply.txt" && echo "PASS  verify: both-fail case recorded rc=42/42 match=0 in 2-apply.txt" || { echo "FAIL  verify: rc/match line missing in 2-apply.txt"; fails=$((fails+1)); }
vfail "verify: source stdout looks fine but rc is nonzero" MOCK_VSRC_RC=42 MOCK_V_STDOUT_ON_FAIL=1
vfail "verify: copy stdout looks fine but rc is nonzero" MOCK_VCPY_RC=42 MOCK_V_STDOUT_ON_FAIL=1
vfail "verify: rc 0 but both data are empty" MOCK_VSRC_OUT=__EMPTY__ MOCK_VCPY_OUT=__EMPTY__
vfail "verify: rc 0 but only the copy is empty" MOCK_VCPY_OUT=__EMPTY__
vfail "verify: rc 0 but only the source is empty" MOCK_VSRC_OUT=__EMPTY__
run "verify: identical non-empty data still passes (rc 0, CREATE_COMPLETE)" acnet-1-gate-create.sh zero '-' 'secret copy verified'
grep -q 'secret_verify_source_rc=0 copy_rc=0 match=1' "$LASTDIR/r/2-apply.txt" && echo "PASS  verify: success recorded rc=0/0 match=1" || { echo "FAIL  verify: success line missing"; fails=$((fails+1)); }
run "secret: the manifest Pods carry imagePullSecrets and IfNotPresent (static)" acnet-1-gate-create.sh zero '-' 'CREATE_COMPLETE'
[ "$(grep -c 'imagePullSecrets' "$LASTDIR/r/rest.yaml")" = 3 ] && [ "$(grep -c 'imagePullPolicy: IfNotPresent' "$LASTDIR/r/rest.yaml")" = 3 ] && [ "$(grep -c 'name: harbor-pull-secret' "$LASTDIR/r/rest.yaml")" = 3 ] && echo "PASS  secret: 3 Pods have imagePullSecrets + IfNotPresent" || { echo "FAIL  secret: Pod pull settings wrong"; fails=$((fails+1)); }
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
runc "cleanup: the copied pull secret is allowed -> rc 0 COMPLETE" zero '-' 'CLEANUP_COMPLETE' MOCK_NS_STATE=gone 'MOCK_ALL_EXTRA=secret/harbor-pull-secret\n'
runc "cleanup: any OTHER secret -> STOP, no delete" nonzero 'delete --raw' 'unexpected resources in namespace' MOCK_NS_STATE=gone 'MOCK_ALL_EXTRA=secret/other\n'
runc "cleanup: unexpected pod/service set -> STOP, no delete" nonzero 'delete --raw' 'unexpected object in namespace: pod/extra' 'MOCK_SET=pod/probe-a\npod/extra\n'
runc "cleanup: other-resource listing FAILS -> STOP, no delete" nonzero 'delete --raw' 'resource listing failed' MOCK_ALL_RC=1
runc "cleanup: UID read fails (timeout) -> STOP, no delete" nonzero 'delete --raw' 'cannot read the namespace UID' MOCK_GETUID_RC=1
runc "cleanup: UID-precondition delete refused -> NONZERO" nonzero '-' 'precondition delete refused' MOCK_DELETE_RC=1
n=$((n+1)); d="$T/s$n"; mkdir -p "$d/r"; : > "$d/log"
out=$(env AC_NET_R="$d/r" AC_NET_K="$BIN/kubectl" MOCK_LOG="$d/log" PATH="$BIN:$PATH" bash "$HERE/acnet-9-cleanup.sh" 2>&1); rc=$?
if [ "$rc" != 0 ] && echo "$out" | grep -q 'no recorded namespace UID'; then echo "PASS  cleanup: no recorded UID -> NONZERO (rc=$rc)"; else echo "FAIL  cleanup: no recorded UID (rc=$rc)"; fails=$((fails+1)); fi
echo "== result: $fails failure(s)"
exit $((fails>0))
