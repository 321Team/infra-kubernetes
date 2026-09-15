# PR #9 JSON masking review follow-up — 2026-09-15

Review: https://github.com/321Team/infra-kubernetes/pull/9#pullrequestreview-5197580718

The previous JSON branch lost credential-key context. This change masks the
accessToken/refreshToken/dbPassword regressions and the previously uncovered
clientSecret/client-key/tls.key variants. Case and separators are normalized.
Credential context follows nested dictionaries/lists. Sensitive literal values
in Kubernetes env arrays and arbitrary data/stringData values in Secret objects
are also masked. Existing plaintext redaction remains unchanged.

Projected serviceAccountToken configuration is preserved only under the
projected.sources path with the known configuration fields. secretKeyRef
references, selector keys, ordinary environment values, imagePullSecrets names
and the boolean automountServiceAccountToken setting are preserved. Credential
containers retain their JSON structure while their scalar values are masked.
This is defined-pattern redaction, not a guarantee of detecting every possible
secret in arbitrary application text or unfamiliar field names.

Validation on 2026-09-15: Node 71 PASS (including 10 redaction tests), Worker
21 PASS, locale 2 PASS including real ansible-inventory integration. All use
synthetic credentials; no server operations were executed. The Node source
checksum manifest is regenerated and checked. Firewall transaction, policy and
approval behavior are unchanged by this follow-up.

Independent review of the new commit is required. These local results do not
close the reviewer finding, approve a merge, establish CNI Runtime results or
grant CP1 final acceptance.
