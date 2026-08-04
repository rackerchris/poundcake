# PoundCake Plugins

This directory documents the PoundCake-provided plugins and the requirements
operators must satisfy before enabling each one.

`plugin_tier` has a narrow meaning in code:

- `supported`: approved by the core support contract and allowed to register a
  supported plugin log key.
- `community`: shipped with PoundCake, but not yet promoted to the supported
  tier.

The current supported-tier plugins are `bakery` and `dummy`. The current
promotion candidates are `k8s`, `prometheus`, `alertmanager`, `stackstorm`,
`git`, and `github` once their support test coverage is complete.

## Contract Boundary

Plugins declare immutable ingredient contract templates and mutable recipe-step
composition.

- `ingredient_templates` must describe provider execution contracts only:
  service identity, payload schema/defaults, operation metadata, retry/blocking
  defaults, and expected outcome defaults. Plugins must not provide database
  identity or lifecycle fields such as `id`, `is_active`, `deleted`, or
  timestamps. PoundCake owns retirement and revision creation.
- When an adapter changes an active ingredient contract, bootstrap retires the
  old active row and creates a new active revision. Disabled ingredient rows are
  historical revisions; bootstrap does not reuse or reactivate them.
- `recipe_templates[].recipe_ingredients` are mutable composition and may be
  replaced as recipes evolve. They must reference ingredients by template
  identity, not by database IDs such as `ingredient_id`.
- Every advertised operation must have
  `operation_metadata[operation].payload_schema`. PoundCake treats operation
  schemas as authoritative, validates them before adapter dispatch, and fails
  closed on contract violations.
- `service_payload` must be an object when provided. Non-object values are
  rejected with `service_payload must be an object when provided`; adapters keep
  the same guard as defense in depth.

The canonical adapter contract, roles/responsibilities table, and conversion
notes live in [SERVICE_PLUGIN_CONTRACT.md](../SERVICE_PLUGIN_CONTRACT.md).

| Plugin | Tier | Primary purpose | Requirements |
| --- | --- | --- | --- |
| [`dummy`](dummy.md) | supported | Reference implementation and local contract test plugin. | No external service or credentials. |
| [`bakery`](bakery.md) | supported | Remote ticketing and communication provider. | Remote Bakery deployment, bootstrap HMAC, Helm client values. |
| [`k8s`](k8s.md) | community | Kubernetes diagnostics and scoped remediation primitives. | Kubernetes API access; narrow RBAC for enabled k8s ingredients. |
| [`prometheus`](prometheus.md) | community | Prometheus API read/reload operations, rule helpers, and PrometheusRule lifecycle. | Prometheus HTTP endpoint; optional HTTP auth; monitoring rule ownership wiring. |
| [`alertmanager`](alertmanager.md) | community | Alertmanager API inspection and silence sync. | Alertmanager HTTP endpoint; optional HTTP auth. |
| [`stackstorm`](stackstorm.md) | community | StackStorm action/workflow execution and content sync. | StackStorm API URL, API key/auth token, StackStorm pack content. |
| [`git`](git.md) | community | Portable Git repository reads and writes. | Repository URL/default branch; credentials for private or write operations. |
| [`github`](github.md) | community | GitHub repository reads, commits, and pull requests. | GitHub API config; token for private or write operations. |
| [`genestack_monitoring`](genestack_monitoring.md) | community | Genestack monitoring alert catalog bootstrap/sync. | `github` and `prometheus` helpers enabled. |

Devstack enables the external integration set from
`helm/devstack/values/poundcake-plugins-kind.yaml`; see
[`helm/devstack/ADAPTERS.md`](../../helm/devstack/ADAPTERS.md) for local wiring.
