# Genestack Monitoring Plugin

## Status

- Service type: `genestack_monitoring`
- Tier: `community`
- External services: GitHub, Kubernetes, and Prometheus helpers

## Purpose

`genestack_monitoring` syncs Genestack monitoring alert catalog content into
PoundCake recipes through a scheduled service-execution ingredient. It composes
helper capabilities from other plugins and the registered plugin capability
catalog instead of owning direct provider credentials or fabricating provider
workflow names. The plugin does not run an application bootstrap hook; GitHub
reads, capability resolution, and Kubernetes reconciliation happen only when
the `content_sync` ingredient/order runs.

For deterministic remediation composition, `genestack_monitoring` treats the
registered capability catalog as provider-neutral input. Native bounded
Kubernetes actions stay on the `k8s` plugin, StackStorm is preferred for
blackbox and etcd workflow orchestration, and ambiguous/node-style alerts stay
in evidence plus manual-review mode until a safer provider policy is defined.

## Requirements

- `github` plugin enabled with helper capabilities `repo.read` and `repo.list`.
- `k8s` plugin enabled with helper capability `k8s.prometheusrules.manage`.
- `prometheus` plugin enabled with helper capability `alert_rules.parse`.
- GitHub access to the Genestack monitoring rule source repository.

## Credentials

No direct `genestack_monitoring` credential row is required.

If the source repository is private or rate-limited, configure the `github`
plugin credential:

- `credential_type=github_token`
- `credential_key_id=default`

## Enabled behavior

- `health_check`
- `content_sync` with operation `sync_content`
- `repo_sync` with operation `export_alert_updates`

## Payload contracts

Operation-level `payload_schema` validation is authoritative. Invalid payloads
are rejected before adapter execution.

- `content_sync.sync_content` and `health_check` accept no payload fields.
- `repo_sync.export_alert_updates` requires `crd_name`, `group_name`, and
  `rule_name`, and accepts optional `namespace`.

## Dependency note

The Prometheus HTTP adapter is used for parsing helper behavior. PrometheusRule
CRD reconciliation is helper-mediated through the Kubernetes plugin, so
`genestack_monitoring` does not own Kubernetes credentials or direct Kubernetes
client access. The Prometheus Operator CRD chart is required when synced alert
rules are registered as Kubernetes `PrometheusRule` resources.
