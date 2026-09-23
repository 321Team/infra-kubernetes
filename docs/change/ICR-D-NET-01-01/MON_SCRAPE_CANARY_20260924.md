# MON scrape canary and kubelet serving TLS follow-up — 2026-09-24 KST

Status: DRAFT / REVIEW PENDING / RUNTIME NOT RUN.
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

The SYN-only capture cannot establish absence of SYN-ACK. It does not uniquely identify the originating process. Its source is consistent with SNAT of traffic from the Prometheus node, but paired sender capture/conntrack attribution has not been performed.

The receiver observation supersedes using an assumed Pod CIDR as the source for this path. The current worker01 GNP does not permit the observed tuple. Kernel/Calico drop counters have not independently proven the exact drop point.

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

## Application gate and bounded execution

The central governance/REVIEW_AUTHORITY_POLICY.md requires independent review of operational manifests. Before execution, the responsible K8S/SEC Lead decides whether this explicit diagnostic scope is acceptable and records the independently reviewed full commit SHA, operator, short work window, positive/negative probes and rollback responsibility. ICR provides source/return-path validation support; no additional standing ICR co-signature is required. Publishing this Draft is not independent code review or an execution decision.

At the reviewed work window, through the existing ansible01 inventory, svc_ansible identity and sudo permissions:

1. Before opening the diagnostic path, identify an existing approved negative-probe workload and confirm its current egress controls and pre-change denied result. If no suitable approved probe exists, keep application on HOLD. Confirm the Prometheus Pod UID/IP/node/SA, worker01 HEP labels/expectedIPs/interface, default tier and all applicable earlier policies still match the recorded premise. Confirm the candidate name is absent. Stop on any mismatch or existing object; do not overwrite another object. Check worker01 node-exporter readiness, approved image and listening port.
2. Preserve the existing HEP/GNP snapshots and current target errors in the operator's result directory. Validate the pinned candidate with the installed CRD/server-side dry-run; inspect the diff for exactly one new resource. No apply on schema rejection, unexpected admission mutation or wider diff.
3. Apply only this resource after the approval above, without restarting nodes or Pods. Observe two completed scrape cycles of worker01 node-exporter. The previous configured interval is 15 seconds; verify the active interval.
4. Correlate the actual Prometheus request with a bounded sender/receiver capture of the exact tuple, including both directions. Use existing tools; do not add packages or privilege.
5. Verify existing approved SSH/API/metrics paths and the specifically agreed unauthorized workload negative probe, including the same source node. A successful unauthorized probe is FAIL, not an accepted AC-07 exception. Do not create an unreviewed test Pod or fabricate a negative result when no approved probe is available.
6. Record target UP/DOWN, HTTP/error and source/return observations. Any management regression, unexpected source, unauthorized access, unavailable negative probe, or failure to improve the target means stop and remove the canary.
7. Remove this one temporary resource at the end of the window even if the target improves. It has no automatic TTL. Retention or expansion requires a separately reviewed durable policy decision.

Rollback: after confirming the live object's identity/spec is still the one created by this work window, delete only icr-mon-worker03-to-worker01-9100-canary. Do not delete by label, replace existing host GNPs, reload firewalld or flush rules. Verify the object is absent, baseline host policies remain and approved management paths still work. Removing an allow does not necessarily terminate an already tracked connection; test a new connection when verifying rollback. Full persistence/reboot/rollback ACs are not awarded from this one window.

Expected positive result, if the HEP rule is the effective blocker: worker01 node-exporter becomes UP while worker02 and kubelet failures remain. This is a hypothesis to test, not a predicted PASS.

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

STATIC: YAML syntax checked and scope manually compared with the supplied HEP/GNP snapshot.
MOCK/UNIT: N/A (one declarative diagnostic rule; no new executor).
CI: not run locally; check the PR's actual workflow status separately.
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

AI assistance: CODEX_ASSISTED.
