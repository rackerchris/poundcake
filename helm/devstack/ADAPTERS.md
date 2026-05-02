# Devstack Adapter Wiring

Built-in plugin support tiers and production requirements are documented in
[`docs/plugins/README.md`](../../docs/plugins/README.md). This file only covers
the local Helm devstack wiring.

The Helm devstack enables the external adapter plugins with:

```yaml
config:
  enabledPlugins: k8s,git,github,prometheus,alertmanager,bakery,stackstorm,genestack_monitoring
```

The production chart does not inject adapter URLs, usernames, passwords, bearer
tokens, repository URLs, or kubeconfigs into PoundCake workloads. Those values
belong to operator-managed adapter connection records and
`adapter_credentials`, written only through the credential-manager
boundary.

Local credentials required for full plugin exercise:

| Adapter | Credential type | Key | Notes |
| --- | --- | --- | --- |
| `git` | `git_repository_auth` | `default` | Required for write operations; optional for public reads. |
| `github` | `github_token` | `default` | Optional for public repositories; useful for private repositories or higher rate limits. |
| `k8s` | `kubernetes_kubeconfig` | `default` | Optional in-cluster; useful when PoundCake manages another cluster. |
| `prometheus` | `prometheus_http_auth` | `default` | Optional HTTP auth for authenticated Prometheus endpoints. |
| `alertmanager` | `alertmanager_http_auth` | `default` | Optional HTTP auth for authenticated Alertmanager endpoints. |
| `bakery` | `bakery_monitor_hmac` | `default` | Required for remote Bakery registration and communication; write through credential-manager/UI. |
| `stackstorm` | `stackstorm_api_key` | `default` | Required for StackStorm action/workflow execution. |
| `genestack_monitoring` | n/a | n/a | Uses the GitHub and Prometheus helpers; no direct credential row. |

Until the operator UI/API lands, do not seed these with Helm hooks or direct SQL
fixtures. A missing credential should leave the relevant adapter failed or
degraded instead of bypassing credential-manager.
