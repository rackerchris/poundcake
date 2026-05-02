# Genestack Monitoring Plugin

## Status

- Service type: `genestack_monitoring`
- Tier: `community`
- External services: GitHub, Kubernetes, and Prometheus helpers

## Purpose

`genestack_monitoring` syncs Genestack monitoring alert catalog content into
PoundCake recipes through a scheduled service-execution ingredient. It composes
helper capabilities from other plugins instead of owning direct provider
credentials. The plugin does not run an application bootstrap hook; GitHub
reads and Kubernetes reconciliation happen only when the `content_sync`
ingredient/order runs.

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

## Dependency note

The Prometheus HTTP adapter is used for parsing helper behavior. PrometheusRule
CRD reconciliation is helper-mediated through the Kubernetes plugin, so
`genestack_monitoring` does not own Kubernetes credentials or direct Kubernetes
client access. The Prometheus Operator CRD chart is required when synced alert
rules are registered as Kubernetes `PrometheusRule` resources.
