# ICR worker03 negative-test candidate metadata pre-check

Responsibility: ICR-D-NET-01-01 / baseline #14, operating candidate infra-kubernetes #27.
Classification: pure Pre-check under REVIEW_AUTHORITY_POLICY §3.2/4-1. No operational manifest is included.

## Purpose and limits

The 01:21 collection contains Prometheus, node-exporter and Calico Pods, not the full worker03 Pod inventory. The observed node-exporter and Calico Pods use hostNetwork and cannot automatically stand in for a general workload negative test. This one-shot collection supplies the missing inventory only. It does not approve a workload, inspect image tooling, run kubectl exec or connect to .105:9100.

Only three commands run on control01: read the current API URL from the existing admin.conf; GET worker03 identity/Ready; GET at most 100 Pods selected by spec.nodeName=k8s-worker03. API must remain https://10.1.93.109:6443. No CP-direct endpoint fallback, namespace-wide rescan or continuation-page request. A continuation token or oversized list stops collection. Raw GET avoids kubectl discovery-cache writes.

Projected output contains Pod/SA/node/owner/container/imageID, status and the common role labels app, role, app.kubernetes.io/name/component/part-of. Other labels, annotations, env, commands, arguments, volume/Secret contents, credentials and full API response bodies are not emitted. Raw stdout/stderr hashes are recorded; error text is not exported. These hashes do not make the omitted raw bodies available for review.

System namespaces, Prometheus identity, host namespace/privileged/static Pods, terminating/non-ready Pods and incomplete identities are excluded from the general-Pod shortlist. Remaining entries are OWNER_REVIEW_REQUIRED, never approved. An empty shortlist is a valid inventory result, not a failed security control or proof that no separately reviewed test provision is possible. Policy-specific labels and namespace selectors may still need a targeted read after an owner chooses a candidate.

## Execution

Run from ansible01 using the existing /home/server01_ansible/p1-ans-01 checkout, environment and inventory/hosts.yml. The play targets only k8s-control01 at .102 as svc_ansible, with become disabled and existing sudo -n kubectl permission. No tools, accounts or privileges are installed. Remote Python is passed through the existing Ansible command module; no collector is installed or registered. Normal Ansible transient transport files follow the existing access mechanism.

Download collect.py, collect.yml, run.sh and SHA256SUMS from the exact published source commit. Check SHA256SUMS before execution. From the canonical checkout, after loading the existing Ansible environment, invoke:

```bash
bash /absolute/path/to/downloaded/run.sh "$PWD/inventory/hosts.yml" FULL_SOURCE_COMMIT_SHA
```

The runner checks its runtime-file manifest, canonical inventory and ansible.cfg, then performs an Ansible syntax check before the query. It creates a fresh ICR_Negative_Candidates_* directory, archive and sidecar on ansible01. It does not change tracked inventory/config or remove an existing directory. Source SHA, controller checkout/config context, source hashes, target/account, KST timestamps and return codes are retained. No automatic upload occurs.

## Verdict and stop behavior

- RC=0 and COLLECTED_PENDING_OWNER_SELECTION: metadata collected. Read pod_count, owner_review_count and exclusion reasons; approval remains NOT_ESTABLISHED, probe NOT_RUN and runtime_ac NOT_ASSESSED.
- Query timeout/Forbidden/malformed response, node mismatch/not Ready, account/host mismatch, unexpected API or truncated list: STOP/RC nonzero. No endpoint/privilege/policy workaround or active probe. Preserve the archive/sidecar when produced, including failure output.
- A successful Ansible process with no collected envelope is an error. Syntax/transport failure remains an error; the runner still packages its logs where possible.
- There is no operational change to roll back. Do not restart Pods/services or modify labels, CNI, GNP, NetworkPolicy or firewall while resolving a collection failure.

After collection, K8S/SEC selects an existing approved general workload and exact container. Record namespace/name/UID/SA/imageID, its actual permitted probe tool/command and applicable egress denial. The positive endpoint/control and policy attribution are still required for the later negative test. Missing tooling or a generic timeout is not a negative-test PASS. PR27 independent review and reviewed change-window inputs remain separate.

## Verification record

2026-09-24 KST preparation: Python AST, Bash syntax, YAML parsing/embedded Python launcher and runtime-file SHA256 checks PASS. Local synthetic collector/controller tests 19/19 PASS. Controller tests use a fake ansible-playbook and do not count as Ansible integration or field execution. Ansible is unavailable in the authoring workspace; the real ansible-playbook --syntax-check runs on ansible01 before any remote query. FIELD COLLECTION NOT RUN; probe/operating changes NOT RUN.

The PR records the exact STATIC / local synthetic tests / CI / field collection results. Local tests never access a server. CI zero means NOT RUN. Source review under the Pre-check exception is NOT PERFORMED (Pre-check), not independent approval. Any later extension to active probes or operating changes requires reclassification under the central policy.

References: baseline #14, infra-kubernetes #27; https://kubernetes.io/docs/concepts/overview/working-with-objects/field-selectors/ (Pod spec.nodeName selector).
AI assistance: CODEX_ASSISTED.
