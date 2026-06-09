# Kubernetes Plugin

## Status

- Service type: `k8s`
- Tier: `community`
- External service: Kubernetes API

## Purpose

`k8s` owns bounded Kubernetes API diagnostics and remediation primitives for
PoundCake recipes. Its primary surface is cluster, pod, workload, deployment,
node, service, storage, and scheduling inspection through plugin-owned
ingredients with operation-specific payload validation. Under the capability
catalog contract, this plugin is the preferred provider for exact bounded
single-step Kubernetes mutations that do not need workflow orchestration.

## Requirements

- Kubernetes API access from the PoundCake API and workers.
- RBAC allowing only the Kubernetes resources required by enabled k8s
  ingredients. Read-only diagnostics require pod, event, workload, node,
  service, endpoint, PVC/PV, PDB, and HPA reads as configured. Remediation
  operations require explicit, narrow grants such as exact pod delete or
  deployment scale/restart permissions.
- No generic `kubectl`, exec, host shell, selector-based bulk delete, or
  unbounded Kubernetes mutation permission is exposed by this adapter.

## Credentials

The optional credential is:

- `credential_type=kubernetes_kubeconfig`
- `credential_key_id=default`

If no kubeconfig credential exists, the adapter uses in-cluster service account
authentication.

Credential payloads must include either `kubeconfig` or `server` plus `token`.

## Operator configuration

The operator config supports a default namespace.
Devstack uses in-cluster service account authentication and grants only the
service-plugin RBAC needed by the enabled k8s ingredients.

## Enabled behavior

- `health_check`
- `pod_action` operations: `list`, `get`, `logs`, `events`, and exact-name
  `delete`; `logs` supports `previous=true` for crash-loop diagnostics
- `deployment_action` operations: `get`, `scale`, `rollout_restart`, and
  `rollout_status`
- `workload_action` operations: `get`, `rollout_restart`, and `rollout_status`
  for exact Deployment, StatefulSet, or DaemonSet targets
- `workload_triage` read-only diagnostics:
  - `pod_diagnostics`
  - `workload_status`
  - `logs`
  - `events`
  - `job_diagnostics`
  - `node_diagnostics`
  - `pvc_diagnostics`
  - `service_diagnostics`
  - `config_diagnostics`
  - `pdb_hpa_diagnostics`
  - `certificate_diagnostics`
- `node_triage` read-only node/host diagnostics:
  - `list_nodes`
  - `node_diagnostics`
  - `node_capacity`
  - `node_pressure`
  - `node_pods`
  - `node_events`
- `failed_job_cleanup` remediation:
  - `delete` for one exact failed Job by namespace/name after bounded diagnostics
- `resource_pressure_remediation` remediation:
  - `scale_deployment`
  - `patch_hpa_bounds`
- `service_probe` read-only active probes:
  - `dns`
  - `tcp`
  - `http`

`pod_action`, `deployment_action`, and `workload_action` use
operation-specific payload schemas: the recipe chooses the operation from the
ingredient menu, then supplies only the payload values allowed for that
operation. Destructive pod actions and workload rollout actions require exact
names; this plugin does not expose a generic `kubectl` operation or bulk
destructive selector operation.

`workload_triage` is intentionally read-only. It accepts alert-shaped labels
such as namespace/pod/container, namespace/deployment, namespace/job_name,
node, persistentvolumeclaim, persistentvolume, service, configmap,
poddisruptionbudget, and horizontalpodautoscaler, then returns bounded
Kubernetes API diagnostics.
Workload logs are available here for Deployments, StatefulSets, DaemonSets,
ReplicaSets, Jobs, CronJobs, Services, Pods, and label selectors; the lower
level `pod_action/logs` primitive remains available for exact pod workflows.
PVC/PV, Service, PDB, and HPA diagnostics return compact summaries for ticket
evidence: binding phase, storage class, mounted pods, endpoint readiness,
selected pod phases, replica status, target rollout status, and warning-event
reasons. `config_diagnostics` returns ConfigMap metadata, key names, value
lengths, and hashes without returning raw ConfigMap values.
`certificate_diagnostics` reads Secret metadata plus public certificate fields
from `tls.crt`, `ca.crt`, or `certificate.crt`. It reports expiry, issuer,
subject, SANs, and whether a private key exists, but never returns private key
material or raw certificate PEM.

`node_triage` is also read-only and deliberately stops at Kubernetes API
inspection. It can list nodes, read node conditions/capacity/taints/system
info, list pods scheduled to a node, and return node events. `node_pressure`
and `node_diagnostics` also return compact evidence summaries for Bakery:
scheduled pod count versus pod capacity, pod phases, waiting reasons, restart
totals, event counts, warning reasons, non-running pod summaries, and warning
event summaries. It does not expose cordon, drain, delete, exec, host shell, or
raw `kubectl`.

`failed_job_cleanup` is a bounded remediation surface for exact Job cleanup
only. It first reads Job diagnostics, verifies the Job is in a failed terminal
state, and then deletes that exact Job with no selector or bulk delete path.

`resource_pressure_remediation` is limited to exact-name scaling actions. It can
set Deployment replicas or patch HPA min/max bounds, but it does not expose
free-form patches, quota mutation, node mutation, or generic autoscaling writes.

`service_probe` is read-only and bounded to exact namespace/service targets. It
resolves in-cluster Service DNS and discovered Service endpoints, then performs
DNS, TCP, or HTTP GET probes with a short timeout and narrow request shape. It
does not accept arbitrary external hosts or general-purpose HTTP requests.

## Devstack

The devstack installs PoundCake with the k8s plugin enabled and service-plugin
RBAC for the local kind cluster. Prometheus and Alertmanager are installed by
the monitoring devstack path.
