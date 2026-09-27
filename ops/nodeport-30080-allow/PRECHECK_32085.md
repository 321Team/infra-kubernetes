# P1-SEC-01 NodePort 30080 / metrics 32085 read-only precheck

Status: **READ-ONLY execution source. No Runtime mutation.**

Related:
- infra-kubernetes #45
- PR #36 / merge `bac062d9e764dc8f519a70b8e1aaf95ff3f27c8c`
- baseline #19
- DEC-P1-ENTRY-01

## Expected source state

The approved repository source is:

- `fds-app/transaction-api`: existing ClusterIP service, ports 80/9091.
- `fds-app/transaction-api-nodeport`: separate NodePort service, HTTP 80→8000,
  `nodePort: 30080`, `externalTrafficPolicy: Local`.
- no NodePort for transaction-api metrics 9091.
- `nodeport-30080-allow`: edge01 `10.1.93.50/32` may reach TCP30080;
  other sources are denied on TCP30080.

The 2026-09-26 Runtime record instead patched the existing `transaction-api`
service to NodePort and observed `9091:32085/TCP`. This precheck determines the
**current** state without assuming that record is still current.

## Execution

Run on ansible01:

```bash
cd /home/server01_ansible/p1-ans-01
bash /path/to/infra-kubernetes/ops/nodeport-30080-allow/field-collect-32085.sh
```

The script:
- reads both Service objects independently;
- reads EndpointSlices, transaction-api Pod placement, and the 30080 GNP;
- probes Worker TCP30080 and TCP32085 from ansible01;
- if existing SSH access to edge01 is available, probes the same ports from
  edge01;
- creates a SHA256SUMS-verified archive and sidecar.

It never applies, patches or deletes Kubernetes/Calico objects and never changes
firewalld or other security controls.

## Interpretation

### 30080

From ansible01 `10.250.10.10`, successful TCP30080 connection is unexpected
under the approved GNP. Timeout/drop is consistent with the policy but is not,
by itself, proof that Calico caused the block.

From edge01 `10.1.93.50`, TCP30080 is expected only on Workers that currently
have a local Ready transaction-api endpoint because
`externalTrafficPolicy: Local` is required. A Worker without a local endpoint
may refuse the connection; that is not a GNP failure.

### 32085

The approved source has **no metrics NodePort**.

- Any successful TCP32085 connection from ansible01 or edge01 is a Phase 1
  blocker.
- REFUSED/TIMEOUT alone does not close the blocker while a live Service still
  declares `nodePort: 32085`.
- Closure requires Service spec reconciliation so 32085 is no longer allocated,
  followed by read-only confirmation on all Workers.

Do **not** add an Allow or Deny rule for 32085 as a shortcut. Remove the
unapproved exposure by reconciling the Service to the approved source.

## STOP criteria

Stop before any Runtime correction when:
- the live GNP differs from PR #36 source;
- the two Service objects do not match the expected source model;
- a different process/change is modifying the Service;
- Pod/Endpoint placement is unstable enough to make 30080 results ambiguous;
- archive/internal hashes fail.

The subsequent correction is a separate APPLY decision with pre-state,
server-side dry-run, diff, rollback and post-change evidence.
