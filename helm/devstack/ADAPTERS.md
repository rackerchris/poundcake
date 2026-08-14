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
| `github` | `github_token` | `default` | Optional for public reads; required for PR creation and other write operations. |
| `k8s` | `kubernetes_kubeconfig` | `default` | Optional in-cluster; useful when PoundCake manages another cluster. |
| `prometheus` | `prometheus_http_auth` | `default` | Optional HTTP auth for authenticated Prometheus endpoints. |
| `alertmanager` | `alertmanager_http_auth` | `default` | Optional HTTP auth for authenticated Alertmanager endpoints. |
| `bakery` | `bakery_monitor_hmac` | `default` | Issued by remote Bakery registration. Provide a bootstrap HMAC Secret or write the issued monitor HMAC through credential-manager. |
| `stackstorm` | `stackstorm_api_key` | `default` | Required for StackStorm action/workflow execution. |
| `genestack_monitoring` | n/a | n/a | Uses the GitHub and Prometheus helpers; no direct credential row. |

Until the operator UI/API lands, do not seed these with Helm hooks or direct SQL
fixtures. A missing credential should leave the relevant adapter failed or
degraded instead of bypassing credential-manager.

For Helm devstack bootstrap, `helm/devstack/configure-github-adapter.sh`
defaults to `allow_public_read=true` with an empty token so Genestack catalog
reads work out of the box. If `GITHUB_TOKEN` is present, the same bootstrap
path writes a real `github_token` credential and enables write-capable
`commit_and_pr` testing.

When `helm/devstack/configure-bakery-adapter.sh` is used, it also disables the
`dummy` plugin in the live devstack so Genestack-managed recipes only see one
active communication provider.

For the current remote devstack integration, the Bakery adapter defaults to the
explicit monitor identity `rackspace/kronos-poundcake` and region `ord`. Prefer
`BAKERY_BOOTSTRAP_HMAC_KEY_ID` and `BAKERY_BOOTSTRAP_HMAC_KEY` so the bakery
plugin can register itself. Issued monitor HMAC env vars remain a recovery
path. If Bakery minted the bootstrap credential for another monitor ID,
override `BAKERY_MONITOR_ID` before running the configurator.
