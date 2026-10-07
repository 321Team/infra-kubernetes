#!/usr/bin/env bash
# Source only. Uses backup.sh's bounded timeout, OUT and checked writer w.
tool_capture() {
  local name="$1" rc
  shift
  printf '%q ' "$@" > "$OUT/$name.command" || { FAIL=1; return 12; }
  printf '\n' >> "$OUT/$name.command" || { FAIL=1; return 12; }
  timeout 10 "$@" > "$OUT/$name.stdout" 2> "$OUT/$name.stderr"; rc=$?
  w "$OUT/$name.rc" "$rc" || return 12
  # Nonzero command status is evidence, not a reason to skip the other tools.
}

capture_backup_tools() {
  local root="$1" exe="$2" b p arg h after prefix bad=0
  tool_capture 12-prod-exe sha256sum "$exe" || return 12
  for b in etcd etcdutl etcdctl; do
    p="$root/$b"; arg=version; [ "$b" = etcd ] && arg=--version
    tool_capture "12-$b-sha-before" sha256sum "$p" || return 12
    tool_capture "12-$b-version" "$p" "$arg" || return 12
    tool_capture "12-$b-sha-after" sha256sum "$p" || return 12
  done
  tool_capture 12-prod-exe-after sha256sum "$exe" || return 12
  # All raw stdout/stderr/rc are now recorded, even for mismatch/loader/timeout.
  : > "$OUT/12-binaries-sha256.txt" || { FAIL=1; return 12; }
  : > "$OUT/12-binaries-version.txt" || { FAIL=1; return 12; }
  for b in etcd etcdutl etcdctl; do
    h=$(cut -d ' ' -f1 < "$OUT/12-$b-sha-before.stdout")
    after=$(cut -d ' ' -f1 < "$OUT/12-$b-sha-after.stdout")
    [ "$(cat "$OUT/12-$b-sha-before.rc")" = 0 ] &&
      [ "$(cat "$OUT/12-$b-sha-after.rc")" = 0 ] &&
      [[ "$h" =~ ^[0-9a-f]{64}$ ]] && [ "$h" = "$after" ] || bad=1
    # Keep available hash information even when the version command failed.
    [[ "$h" =~ ^[0-9a-f]{64}$ ]] && w "$OUT/12-binaries-sha256.txt" "$h  $b"
    prefix="$b version:"; [ "$b" = etcd ] && prefix='etcd Version:'
    if [ "$(cat "$OUT/12-$b-version.rc")" = 0 ] &&
       [ "$(grep -c "^$prefix " "$OUT/12-$b-version.stdout")" = 1 ] &&
       grep -Fxq "$prefix $EXPECT_BINARY_VERSION" "$OUT/12-$b-version.stdout"; then
      w "$OUT/12-binaries-version.txt" "$b=$EXPECT_BINARY_VERSION"
    else
      bad=1
      w "$OUT/12-validation.txt" "$b: version rejected; see stdout/stderr/rc"
    fi
  done
  PROD_EXE_SHA=$(cut -d ' ' -f1 < "$OUT/12-prod-exe.stdout")
  [ "$(cat "$OUT/12-prod-exe.rc")" = 0 ] &&
    [ "$(cat "$OUT/12-prod-exe-after.rc")" = 0 ] &&
    [[ "$PROD_EXE_SHA" =~ ^[0-9a-f]{64}$ ]] &&
    [ "$PROD_EXE_SHA" = "$(cut -d ' ' -f1 < "$OUT/12-prod-exe-after.stdout")" ] &&
    [ "$PROD_EXE_SHA" = "$(awk '$2=="etcd"{print $1}' "$OUT/12-binaries-sha256.txt")" ] || bad=1
  diff -q "$LIB_DIR/backup-binaries.sha256" "$OUT/12-binaries-sha256.txt" > "$OUT/12-pinned-hashes-diff.txt" 2>&1 || bad=1
  w "$OUT/12-validation.txt" "binary_version=$EXPECT_BINARY_VERSION snapshot_storage_version=$EXPECT_STORAGE_VERSION rejected=$bad"
  [ "$FAIL" = 0 ] || return 12
  [ "$bad" = 0 ] || return 2
}
