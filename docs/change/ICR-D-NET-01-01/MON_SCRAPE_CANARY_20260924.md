# MON scrape canary and kubelet serving TLS follow-up — 2026-09-24 KST

Status: REVIEW READY / INDEPENDENT REVIEW PENDING / APPLY HOLD / RUNTIME NOT RUN.
Classification: operational manifest + execution proposal, NOT a pure Pre-check exception.

Responsibility: ICR #14 (source/return path and host-policy validation support), SEC #19 and K8S #10 (policy and kubelet configuration), MON #17 (scrape and service acceptance). Existing owners and AC remain unchanged.

## Observations and limits

The operator supplied Ansible output in the active troubleshooting conversation. These are operator observations, not direct VM execution by the author. This PR contains a sanitized analysis, not an original evidence archive.

| Time (KST) | Observation |
|---|---|
| 00:54 policy snapshot | Six ens160 HEPs use v4 bootstrap/node labels. Six host GNPs are in default tier, order 100, applyOnForward false, with terminal Deny. Worker ingress permits TCP10250 from control-plane .102/.103/.104 only; TCP9100 is absent. |
| 01:07 | Existing Harbor Compose start recovered 9/9 healthy containers and TCP443. Reboot recovery prevention remains separate. |
| 01:20 | Three node-exporter Pods Ready. Prometheus targets: 6 UP / 14 DOWN. Twelve timeout targets; worker03 kubelet and cadvisor fail IP SAN verification. |
| 01:24 | All six nodes listen on TCP10250; all workers listen on TCP9100. Return route toward 10.244.39.219 uses worker03 .108; this is route evidence, not complete return-path proof. |
| 01:28:14 | All six firewalld units disabled/inactive. Serving leaf certificates all have DNS-only SAN, no IP SAN. Filtered kubelet config prints rotateCertificates:true but not serverTLSBootstrap or tlsCertFile. No CSR objects reported. |
| 01:28:15–17 | worker01 ens160 receives SYNs from 10.1.93.108 to .105:9100 and .105:10250, including repeated SYNs. |
| 02:05 Source collection | Matched 64 SYN observations across worker03 Pod veth, worker03 ens160 and worker01 ens160, covering 19 connections. Pod10.244.39.219 is translated to node10.1.93.108 with source-port translation. The counter reads in this older archive failed and retain PARTIAL classification. |
| 02:40 counter supplement | All 9 reads succeeded. Over 20 seconds, worker03 egress allow counters toward .105 increased by14 packets on9100 and32 on10250. Worker01 host-GNP source .108 match and terminal DROP each increased by46 packets/2,760 bytes. |
| 05:20 negative candidate inventory | Real Ansible syntax-check and all3 reads succeeded. Four general workload candidates were observed on worker03. Grafana is now the preparation target following the user's instruction to proceed with the recommendation; this is not an operational approval or an executed negative test. |

The older01:28 SYN-only capture cannot establish absence of SYN-ACK. The02:05 capture also matched SYN-ACK/RST, but only SYNs were observed before the64-packet limit ended the approximately28.7-second window. It establishes the Pod network path and pre/post-NAT correspondence, not PID/socket attribution or successful return traffic.

The02:40 filter chain `cali-pi-_IZ_dpYgvbSNCZTvi1r-` is explicitly identified by a comment as `GlobalNetworkPolicy fds-k8s-host-k8s-worker01 ingress`. Its final DROP, comment `cali:wYUSqCe2q6dO_hSD`, increased by46 packets. The INPUT/ens160 dispatch path and source .108 match were verified in the same snapshots. This is direct evidence of activity at a named host-policy DROP rule and strongly supports it as the cross-node scrape blocker. Aggregate counters do not identify individual flows, and the earlier64 SYNs are not the later46 drops. The policy does not allow TCP9100; TCP10250 allows only CP .102/.103/.104. Its .108 source mark is used for UDP4789, not an allow for all ports.

Pinned evidence: [01:21 sender/policy](https://github.com/321Team/fds-project-evidence/blob/2f8a238142ad669a35800bb3e8ecc6d372354d60/evidence/ICR-D-NET-01-01/monitoring-precheck-20260924T012110/README.md), [02:05 Source/SNAT](https://github.com/321Team/fds-project-evidence/blob/2f8a238142ad669a35800bb3e8ecc6d372354d60/evidence/ICR-D-NET-01-01/scrape-source-20260924T020500/README.md), [02:40 counter analysis and originals](https://github.com/321Team/fds-project-evidence/blob/2f8a238142ad669a35800bb3e8ecc6d372354d60/evidence/ICR-D-NET-01-01/scrape-counters-20260924T024027/README.md). These are in evidence Draft PR23 pending independent intake review. Source/counter collection is complete within its stated scope; do not repeat it merely to request code review.

Node-specific issuer names are not proof of a verified certificate chain. IP SAN absence is confirmed. rotateCertificates is not proof of serving certificate bootstrapping; effective command-line/drop-in overrides have not been inspected.

Local source attachments used for analysis (do not treat these names as repository files):
- 붙여넣은 텍스트 (1)(20260923-155506).txt: policy snapshot.
- 붙여넣은 텍스트 (1)(20260923-162034).txt: full active target results.
- 01:28 operator text in the troubleshooting conversation: certificate/service/capture results; no original byte hash available.

## Narrow network candidate

Companion file: MON_WORKER01_9100_CANARY_20260924.yaml.

Only worker03 10.1.93.108/32 -> worker01 10.1.93.105/32 TCP9100 is added at default-tier order 90, ahead of the observed order-100 host deny. It is a single temporary diagnostic exception. It has no Egress rule, no catch-all Allow/Deny, and no change to existing GNPs, HEPs, NodePort30080, NAT, firewalld, IPPools or workload policies. No production render/apply entrypoint references this proposal.

SECURITY LIMIT: source IP .108 is a node identity, not Prometheus identity. Other traffic translated to .108, and potentially host-originated traffic, can match the same rule if other controls permit it. Workload egress controls remain necessary. This candidate cannot establish ICR-AC-07 compliance and is not a permanent production fix. If this temporary loss of source identity is unacceptable, reject this candidate and design an identity-preserving path under a separate reviewed change. Do not broaden to all workers or the Pod /16.

The worker03 -> worker01 edge is chosen because it is observed. Worker02 and cross-node kubelet paths remain outside this canary. Prometheus relocation invalidates the source premise; do not automatically expand the source set.

## Review readiness and remaining runtime inputs

This revision changes this document only. The26-line companion manifest is byte-identical to Git blob `71544281b433a2161b2f97d4f3ae22f0aafa915c`. The complete operational manifest and this execution proposal still require independent review; there is no earlier independent approval for PR27 to inherit. Ready for review does not release APPLY HOLD.

| Input | Existing evidence / actual remaining work |
|---|---|
| Source and blocker | Source/SNAT and worker01 host-policy DROP evidence are available above. No new diagnostic repetition is needed for review. |
| Sender identity | Last confirmed UID `46c00f91-2073-440a-800c-6cedba89d291`, worker03, PodIP10.244.39.219, hostIP10.1.93.108, SA prometheus. Refresh the relevant identity immediately before a reviewed change window; stop if it changed. |
| Host endpoint and policy | The01:21 snapshot identifies worker01 ens160/v4 labels, expectedIP10.1.93.105, default tier/order100. Reconfirm the relevant live HEP, applicable policies and candidate absence at the change window; do not rerun all historical Gate2 work. |
| Independent code review | Not yet received. An AI self-check or another PR's approval does not satisfy this input. |
| Negative workload | Grafana identity is available and fixed below as the preparation target. Current policy inputs, installed tool capability, K8S/SEC Lead execution acceptance and the executable probe choice remain pending. Keep APPLY HOLD until these are resolved; do not repeat the entire candidate inventory. |
| Previous security tests | [Gate2 report9/9](https://github.com/321Team/fds-project-baseline/issues/10#issuecomment-5772355948) covers management/DNS/NTP/SELinux/Cockpit/VXLAN scenarios; it does not identify this same-node unauthorized workload test. [verify-probe-failover.sh](https://github.com/321Team/infra-kubernetes/blob/2d35809313c70731c58d210b37ef29ed0a300883/scripts/verify-probe-failover.sh) tests DB access, not this endpoint. Neither replaces the required negative evidence. |
| Change window | Responsible K8S/SEC Lead records acceptance of this node-source diagnostic scope, operator, reviewed SHA, start/end and recovery access in the existing PR/ticket execution record. No additional standing ICR co-signature or separate approval form. |

The negative workload must be able to execute the agreed probe in its existing image; missing tools, failed kubectl exec, connection refusal from an unhealthy listener, and generic timeout do not establish a successful security control. Pair the denied observation with a healthy positive endpoint and evidence of the applicable blocking policy. Preserve the workload's existing identity/policies; do not change labels, create a test Pod, install tools or weaken egress to make this run possible. If a suitable approved workload does not exist, the responsible Lead must prepare a separately reviewed test provision or choose another policy design before applying this candidate.

## Grafana preparation target and reviewed probe proposal

Evidence: [05:20 identity receipt](https://github.com/321Team/fds-project-evidence/blob/5c13e7c2393df260e0356e5f2b160573624ded64/evidence/ICR-D-NET-01-01/negative-candidates-20260924T052009/README.md). The user's instruction to proceed with the recommendation selects Grafana for preparation. Responsible K8S/SEC Lead acceptance for the operational window and independent review of this PR remain required under the existing governance policy; no extra ICR co-signature is introduced.

| Field | Pinned observation |
|---|---|
| Namespace / Pod | `monitoring-api/grafana-7dc57885d4-sk8h2` |
| UID | `00396c87-640c-4704-ac6c-dcbacf6fbc90` |
| Node / hostIP / PodIP | `k8s-worker03` / `10.1.93.108` / `10.244.39.217` |
| SA / container / label | `default` / `grafana` / `app=grafana` |
| imageID | `10.1.93.54/fds/grafana@sha256:7a1ff54408c90357ff1c76189e5bbc24f898531cb771fbbfe6a477f00de37d5b` |

The SA is shared with Alertmanager. Do not identify the test by SA alone. Read the exact Pod immediately before and after each reviewed exec and preserve UID, node, IP, labels, container imageID, readiness, containerID/restartCount and execution timestamps. A replacement, restart, unexpected container or uncertain identity invalidates that trial. kubectl exec uses the Pod name and has no UID precondition; these observations do not remove that race. Stop on mismatch instead of adopting the new Pod. Never substitute a deployment-wide exec, ephemeral container or newly created test Pod.

The01:21 snapshot shows Grafana-selected egress to Prometheus TCP9090 and DNS, with no observed .105:9100 allow. This is an older policy observation, not a current DENY result. The separate `ops/icr-grafana-policy-precheck/` source on main reads the pinned Pod, namespace/SA labels and bounded current K8s/Calico policies, tiers and network sets through LB .109. Use its separately published immutable SHA; do not mix it into this operational PR's Pre-check classification. Its successful collection is input for policy review, not authorization or a dataplane verdict. Consider every selecting K8s egress allow and all applicable Calico order/tier actions; unresolved selectors, endpoint/profile or native admin-policy dependencies keep HOLD.

The following **operational proposal is for independent review and is NOT a command to run now**. Do not infer curl or /bin/sh availability from the image name. The Lead first accepts a single bounded tool introspection through the existing control01/svc_ansible access, with the pinned Pod identity checks above. Retain stdout/stderr/rc; do not install a tool or switch images if it fails.

```bash
/usr/bin/sudo -n /usr/bin/kubectl \
  --kubeconfig=/etc/kubernetes/admin.conf --request-timeout=10s \
  -n monitoring-api exec grafana-7dc57885d4-sk8h2 -c grafana -- \
  /bin/sh -c 'for p in /usr/bin/curl /bin/curl; do if [ -x "$p" ]; then printf "CURL_PATH=%s\n" "$p"; "$p" --disable --version; exit $?; fi; done; exit 127'
```

No network request is made by that introspection, but it is a Pod exec and is deliberately outside the pure read-only policy collector. A missing shell/curl or access error means capability NOT VERIFIED. If `/usr/bin/curl` is actually observed and its installed version supports the options, the exact proposed request is:

```bash
/usr/bin/sudo -n /usr/bin/kubectl \
  --kubeconfig=/etc/kubernetes/admin.conf --request-timeout=10s \
  -n monitoring-api exec grafana-7dc57885d4-sk8h2 -c grafana -- \
  /usr/bin/curl --disable --noproxy '*' --proto '=http' \
  --connect-timeout 2 --max-time 5 --retry 0 --max-redirs 0 \
  --silent --show-error --output /dev/null \
  --write-out 'http_code=%{http_code} remote_ip=%{remote_ip} time_connect=%{time_connect}\n' \
  http://10.1.93.105:9100/metrics
```

This is one direct HTTP request, no redirects/retries/proxy, with response body discarded. curl config loading is disabled by placing `--disable` first. If only `/bin/curl` exists, record that exact absolute-path substitution against the reviewed command in the existing Lead execution record. If neither path or required option works, retain HOLD and choose a separately reviewed alternative; do not fall back to wget/nc automatically. An outer kubectl timeout must not be treated as evidence of a denied remote request or proof that the remote process finished.

Before allowing the canary, establish installed capability, the exact invocation and an applicable blocking-policy basis. Run at most one pre-change trial and one during-canary trial in the agreed short window, each with its own identity checks and bounded attributable dataplane observation. During-canary denial must be paired with the actual Prometheus .105:9100 positive result in that same window. Do not repeatedly retry to obtain a preferred result.

Any demonstrated unauthorized TCP/HTTP reachability is FAIL, including an HTTP error response; an HTTP status such as404 is still reachability. curl success alone does not prove application acceptance. Generic timeout/HTTP000, refusal, failed exec, missing tool, identity uncertainty or loss of the stream is INCONCLUSIVE unless separately resolved by the correlated policy and endpoint evidence. A pre-change timeout at the destination host deny does not prove Grafana's workload egress would still protect it after the host canary is added. Preserve enough evidence to identify the applicable blocking enforcement point during the canary. All four phases below remain mandatory, and any failure/inconclusive result triggers the reviewed stop/recovery path.

Reference for proposed options: https://curl.se/docs/manpage.html. Actual installed capabilities remain NOT VERIFIED.

## Application gate and bounded execution

The central governance/REVIEW_AUTHORITY_POLICY.md requires independent review of operational manifests. Before execution, the responsible K8S/SEC Lead decides whether this explicit diagnostic scope is acceptable and records the independently reviewed full commit SHA, operator, short work window, positive/negative probes and rollback responsibility. ICR provides source/return-path validation support; no additional standing ICR co-signature is required. Publishing this proposal or marking the PR ready is not independent code review or an execution decision.

At the reviewed work window, through the existing ansible01 inventory, svc_ansible identity and sudo permissions:

1. Before opening the diagnostic path, identify an existing approved negative-probe workload and confirm its current egress controls and pre-change denied result. If no suitable approved probe exists, keep application on HOLD. Confirm the Prometheus Pod UID/IP/node/SA, worker01 HEP labels/expectedIPs/interface, default tier and all applicable earlier policies still match the recorded premise. Confirm the candidate name is absent. Stop on any mismatch or existing object; do not overwrite another object. Check worker01 node-exporter readiness, approved image and listening port.
2. Preserve the relevant current HEP/GNP, sender/negative-workload identities and target errors in the operator's result directory. Validate the pinned candidate with a create-mode server-side dry-run against the installed CRD and compare the returned spec with this manifest. A candidate-absence GET must distinguish explicit NotFound from Forbidden/timeout/transport error. No mutation on schema rejection, unexpected admission mutation or a resource other than the single proposed GNP.
3. After the prerequisites above, create only this previously absent resource using create semantics that fail on AlreadyExists. Do not use apply/replace to adopt or overwrite an object that appeared concurrently. Preserve the creation response UID/resourceVersion/spec and KST timestamp. Without restarting nodes or Pods, observe two completed scrape cycles of worker01 node-exporter. Verify the active interval rather than assuming the historical15-second setting.
4. Correlate the actual Prometheus request with a bounded sender/receiver capture of the exact tuple, including both directions. Use existing tools; do not add packages or privilege.
5. Verify existing approved SSH/API/metrics paths and the specifically agreed unauthorized workload negative probe, including the same source node. A successful unauthorized probe is FAIL, not an accepted AC-07 exception. Do not create an unreviewed test Pod or fabricate a negative result when no approved probe is available.
6. Record target UP/DOWN, HTTP/error and source/return observations. Any management regression, unexpected source, unauthorized access, unavailable negative probe, or failure to improve the target means stop and remove the canary.
7. Remove this one temporary resource at the end of the window even if the target improves. It has no automatic TTL. Retention or expansion requires a separately reviewed durable policy decision.

Rollback: after confirming the live object's UID/spec is still the one created by this work window, remove only icr-mon-worker03-to-worker01-9100-canary using the UID/resourceVersion-preconditioned method below. A name-only get-then-delete is not a race-safe ownership check. Check the existing kubectl's support before creation; if unavailable, keep HOLD without installing tools. Do not delete by label, adopt a pre-existing object, replace existing host GNPs, reload firewalld or flush rules. If creation times out and its outcome is unknown, inspect the live resource and establish ownership from retained API/audit evidence; do not blindly retry creation or delete a same-name object. Verify the owned object is absent, baseline host policies remain and approved management paths still work. Removing an allow does not necessarily terminate an already tracked connection; test a new connection when verifying rollback. Do not reset conntrack or restart Prometheus to manufacture that condition. Full persistence/reboot/rollback ACs are not awarded from this one window.

Reviewed removal method: through the same existing svc_ansible/control01 access, send a DeleteOptions JSON body on stdin to the existing kubectl raw DELETE. The body uses the UID from the retained successful creation response and resourceVersion from the latest read after UID/spec verification. Do not substitute a UID merely discovered by object name. This is an operational command for independent review, not a current execution instruction.

```bash
/usr/bin/sudo -n /usr/bin/kubectl \
  --kubeconfig=/etc/kubernetes/admin.conf --request-timeout=10s \
  delete --raw=/apis/crd.projectcalico.org/v1/globalnetworkpolicies/icr-mon-worker03-to-worker01-9100-canary -f -
```

Stdin shape (placeholders are deliberately not an executable deletion input):

```json
{"apiVersion":"v1","kind":"DeleteOptions","preconditions":{"uid":"RECORDED_CREATED_UID","resourceVersion":"LATEST_VERIFIED_RESOURCE_VERSION"}}
```

Retain stdout/stderr/rc on ansible01. A409 means no deletion under those preconditions: re-read and investigate; never remove the precondition or use force as a fallback. The raw DELETE response is not proof of final disappearance, so follow it with a bounded GET and require explicit NotFound. Keep finalizers and evidence intact if cleanup does not complete, record cleanup FAIL and use the existing recovery/Lead escalation path. Kubernetes [DeleteOptions](https://kubernetes.io/docs/reference/kubernetes-api/definitions/delete-options-v1-meta/) defines these preconditions; upstream [kubectl delete implementation](https://github.com/kubernetes/kubectl/blob/master/pkg/cmd/delete/delete.go) passes a single file/stdin body to raw DELETE. Installed-client/server execution remains NOT RUN.

Expected positive result, if the HEP rule is the effective blocker: worker01 node-exporter becomes UP while worker02 and kubelet failures remain. This is a hypothesis to test, not a predicted PASS.

## Evidence and verdict for the reviewed window

Use one existing PR/ticket execution record with links to original files and hashes. The fields below are required observations, not a new approval workflow or prefilled results.

| Phase | Record and verdict |
|---|---|
| Before | Reviewed full SHA, manifest hash, operator/window, sender UID and approved negative workload identity, relevant HEP/GNP specs, candidate NotFound, listener/positive-control health, target identity and lastScrape/error, original negative probe stdout/stderr/rc and policy attribution. Missing or contradictory input means HOLD. |
| Creation | Dry-run returned spec, create response UID/resourceVersion/spec and time. Exactly one new GNP; no other object adoption or change. Ambiguous creation outcome means STOP and ownership investigation. |
| Positive | From the existing approved Prometheus access path, retain the exact active target matching .105:9100, including job/scrapePool/scrapeUrl, health, lastError, lastScrape and scrapeInterval. Require two distinct post-creation lastScrape values, increasing and consistent with successive active scrape cycles, both health=up and lastError empty. Repeated polling of the same lastScrape is one observation. Missing/duplicate target, API failure or stale timestamp is NOT VERIFIED, not PASS. |
| Negative | Same approved workload UID/container on worker03, same .105:9100 destination and same test command before/after. A functioning probe plus healthy positive control and policy-attributed denial is required. Unauthorized successful access is FAIL and immediate recovery; tool/exec/listener ambiguity is NOT VERIFIED and requires recovery. |
| Regression | Reuse existing approved management tests for ansible01→worker01 SSH and affected CP/worker01 kubelet management paths. Retain commands/rc and actual outcomes. Do not manufacture a broader6-node or reboot PASS. |
| Recovery | Record why/when recovery started, verify live UID/spec, use recorded UID-preconditioned deletion, confirm absence and baseline HEP/GNP/management health. Check a new connection for baseline denial; existing keep-alive/conntrack state alone cannot prove failure of policy removal. |
| Close window | Positive/Negative/Regression/Recovery each get PASS/FAIL/NOT_VERIFIED plus original references. Report overall diagnostic success only if all required checks pass and the temporary object is removed. No AC05 across3workers, AC06 TLS, AC07 across unauthorized workloads, or ICR closure is awarded from this one window. |

No new remote execution tool is introduced by this revision. The exact negative command remains an input to be fixed for the approved workload; the removal method is defined above and must be supported by the installed client under existing access. This document is a reviewable procedure; do not turn an unreviewed ad-hoc helper into an execution shortcut.

## Kubelet TLS follow-up (design; no configuration patch in this PR)

All six leaves lack IP SANs, so merely opening TCP10250 cannot complete IP-based scrape verification.

K8S/SEC must inspect effective kubelet settings, the signing/controller configuration and Prometheus trust/SA/RBAC before preparing the certificate change. Preferred candidate is managed kubelet serving bootstrap under the approved cluster signer, with a one-worker canary:
- Confirm serverTLSBootstrap support, existing certificate paths/ownership and restore assets without exporting private keys.
- Review the exact local kubelet configuration change and the kubeadm kubelet-config source-of-truth plan. Plan one kubelet restart and its availability/rollback impact; do not restart all six together.
- Validate each resulting CSR's signerName kubernetes.io/kubelet-serving, authenticated node requester, server-auth usages, DNS/IP SANs and node identity. Approve only the verified CSR; never approve all pending CSRs.
- Verify the presented leaf's IP SAN and trust chain with Prometheus's actual CA, then check /metrics and /metrics/cadvisor using its existing SA. Distinguish TLS verification from HTTP401/403 and RBAC.
- Retain TLS verification. No insecure_skip_verify, anonymous access, blanket CSR auto-approval or kubeadm renew-all.
- Review remaining-node rollout and renewal approval/monitoring ownership after the canary succeeds. Future serving CSR renewal handling is part of the durable change.

worker03 is a useful TLS canary because its existing local scrape reaches the TLS layer; this avoids coupling the first certificate check to an unproven cross-node policy change. Effective configuration and signer inputs are still required before writing an executable certificate patch.

## Validation and acceptance

STATIC: YAML syntax and exact single-resource/source/destination/port/tier/order/selector scope checked against the supplied HEP/GNP evidence. This document revision does not alter the manifest bytes.
MOCK/UNIT: N/A (one declarative diagnostic rule; no new executor).
CI: record the latest HEAD's actual check/workflow status in the PR; zero runs means NOT RUN.
SERVER-SIDE DRY-RUN: NOT RUN.
RUNTIME: NOT RUN.
REVIEW: PENDING (AI preparation is not independent human review).
MERGE/APPLY: NOT DONE.

No AC PASS, ownership transfer, completion date or ticket closure is introduced. ICR AC05/06 remain failed from target evidence; AC07 remains unverified. Harbor startup recovery does not prove reboot resilience.

References:
- https://github.com/321Team/fds-project-baseline/issues/14
- https://github.com/321Team/fds-project-baseline/issues/19
- https://github.com/321Team/infra-kubernetes/pull/26 (v4 source intake, separate work)
- https://docs.tigera.io/calico/latest/reference/resources/globalnetworkpolicy
- https://kubernetes.io/docs/tasks/administer-cluster/kubeadm/kubeadm-certs/
- https://prometheus.io/docs/prometheus/latest/querying/api/ (target health/lastScrape/error fields; latest documentation is not evidence of the installed version)
- https://kubernetes.io/docs/reference/kubectl/generated/kubectl_create/ (create and server-side dry-run semantics)
- https://kubernetes.io/docs/reference/using-api/api-concepts/ (delete preconditions and optimistic concurrency)

AI assistance: CODEX_ASSISTED.
