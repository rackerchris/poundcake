# PoundCake Service Plugin Contract

## Summary

PoundCake 2.0 treats service plugins as the static-at-runtime boundary between the core control plane and external systems. Core services decide what work should run, while plugins translate PoundCake execution requests into provider-native operations.

Plugins are discovered from `api.plugins.*.plugin`. Each enabled plugin module exposes `get_plugin()`, which returns an `api.plugins.manifest.ServicePlugin` descriptor. The catalog validates each manifest, builds the runtime adapter registry, and exposes plugin templates for bootstrap.

The plugin adapter runs inside the PoundCake control plane. It is code, not an independent inbound API principal. A plugin can declare capabilities and credential requirements, but it cannot self-authorize routes, mint internal control-plane HMAC credentials, or grant itself RBAC authority.

Dish planning is not a plugin extension point. Cook owns phase selection, run-condition evaluation, payload hydration, contract validation, runtime row creation, and order/dish rollup semantics through the core dish planner. Plugins may register immutable ingredients, recipes, and scheduled tasks, but those templates only describe executable provider capabilities; they do not decide whether a runtime row is created.

## Roles And Responsibilities

| Component | Owns | Must not own |
|---|---|---|
| PoundCake core control plane | Orchestration, persistence, lifecycle, RBAC, scheduling, recipe planning, dish advancement, and sanitized API surfaces. | Provider-native execution logic or provider credential interpretation outside plugin adapters/helpers. |
| Plugin manifest | Static capability declarations: adapter factory, immutable ingredient templates, mutable recipe templates, scheduled tasks, communication routes, helper capabilities, and credential requirements. | Route authority, database grants, lifecycle fields, runtime state, credential values, or self-granted RBAC. |
| Plugin adapter | Provider-specific validation, dispatch, polling, cancellation, health, credential requirements, credential payload validation, and non-secret operator configuration. | Calling internal workflow routes directly, mutating PoundCake runtime rows, registering recipes/ingredients at execution time, or using `poll` for provider writes. |
| Helper | Narrow reusable provider/catalog functions advertised as capabilities for bootstrap or cross-plugin composition. | Hidden workflow execution, broad provider mutation outside declared capabilities, or bypassing service-execution ingredients for cross-adapter runtime work. |
| Dishwasher | Enabled plugin discovery, manifest sync, service registry writes, recipe/scheduled-task definition sync, and due scheduled-task injection. | Provider execution, credential generation, runtime reconciliation, or human-facing recipe authorship beyond manifest sync. |
| Credential Manager | Plugin adapter-owned credential creation, rotation, revocation, and credential status in `adapter_credentials`. | Internal HMAC bootstrap ownership, human auth, recipe mutation, runtime reconciliation, or provider execution. |
| Service Identity Manager | Internal service HMAC identity in `service_identity_credentials`. | Adapter/provider credentials, human sessions, or plugin content registration. |
| Auth Service | UI/CLI/API human identity, sessions, RBAC bindings, and IdP flows. | Adapter credentials, internal HMAC key minting, or workflow runtime mutation. |
| Expediter and execution runner | Runtime adapter calls through `dispatch`, terminal result reconciliation, and execution envelopes. | Recipe planning, durable graph decisions, direct credential mutation, or scheduled-task definition ownership. |
| Timer | Poll/cancel requests through Expediter, runtime row reconciliation, and Cook dish advancement for non-terminal work. | Direct adapter/client imports, provider mutation during status checks, recipe edits, or credential writes. |
| UI and human roles | Redacted status, operator recipe/config controls, admin credentials/RBAC, and redacted observability according to route policy. | Raw service registry writes, internal runtime claims/reconciliation, unredacted secrets, or plugin-owned template mutation. |

## Glossary

- **Plugin**: A logical integration boundary for an external or internal system, such as Alertmanager, Bakery, StackStorm, Kubernetes, Git, or GitHub.
- **Plugin adapter**: PoundCake-side shim code called by Expediter. It translates canonical PoundCake execution requests into provider-native API/client calls and normalizes provider responses back into `ExecutionResult`.
- **Ingredient**: An immutable plugin-provided capability template registered in `ingredients`.
- **Recipe ingredient**: A mutable recipe step that uses an ingredient with recipe-specific payload, operation, expected outcome, timing, phase, and run-condition overrides.
- **Operation/capability**: A selectable action within an ingredient, carried through `service_exec_parameters.operation` and advertised with `allowed_operations` plus `operation_metadata`. An operation may advertise a `payload_schema` that narrows the ingredient-wide payload schema for that selected operation.
- **Expediter**: The only runtime gateway that calls plugin adapter workload methods for dispatch, status, and cancellation.

Prefer adding operations to an existing ingredient when the same plugin adapter surface and payload family fit the work. Add a new ingredient only when the provider action has a distinct contract, purpose, payload shape, blocking behavior, or runtime lifecycle.

## Manifest Contract

Each `ServicePlugin` manifest describes the plugin's capabilities:

- `service_type`: Stable lowercase provider key. By default it must match the plugin directory name.
- `adapter_factory`: Factory that returns an `ExecutionAdapter` implementation.
- `ingredient_templates`: Immutable action templates registered into `ingredients`.
- `recipe_templates`: Optional recipe templates registered during bootstrap.
- `communication_routes`: Optional default communication routes for communication policy.
- `scheduled_tasks`: Plugin-owned recurring work, including the required health check.
- `helper_factory` and `helper_capabilities`: Optional helper object for bootstrap or cross-plugin support.
- `required_helper_capabilities`: Optional dependencies on helpers exposed by other enabled plugins.
- `bootstrap_factory`: Optional metadata-stage bootstrap hook.
- `credential_requirements`: Plugin adapter-owned credential declarations exposed through `credential_requirements()`, not raw secrets.
- route/RBAC declarations: Optional advertised route needs for future RBAC Manager sync. These are requests for policy, not grants.
- `plugin_tier`: `community` by default; `supported` is restricted to PoundCake-approved service types covered by the core support/test contract.
- `plugin_log_key`: Optional logging key for supported plugins.

Operator-facing requirements for built-in plugins are documented under
[plugins/README.md](plugins/README.md).

Every enabled plugin must declare a `plugin_health_check` scheduled task. Plugin scheduled tasks must use one of these task types:

- `plugin_health_check`
- `service_execution`

Each scheduled task declares its own `run_interval_seconds`. There is no plugin-wide sync interval. Health checks, provider sync jobs, repository imports, and other recurring plugin work all use the same scheduled task cadence model so Dishwasher can reconcile one durable contract and inject due work through the normal order pipeline.

Operators may request an immediate run of a registered plugin-owned scheduled task when the task is enabled, sourced from `plugin_manifest`, and uses `plugin_health_check` or `service_execution`. A run-now request is a runtime command, not a scheduled task definition edit: the requester cannot provide payloads, parameters, credentials, service identity, expected outcome, cadence, adapter configuration, or any other execution override. The API may satisfy the request by moving `next_run_at` to the current time, but `next_run_at` is not part of the operator patch contract.

Dishwasher remains the only service that claims due scheduled tasks and injects orders. Manual run requests must not call plugin adapters, helpers, Cook, Expediter, or Timer directly. Runtime evidence for manual runs is the same as scheduled runs: a generated order, dish ingredient execution records, and scheduled task completion state.

Plugin manifests may not set `next_run_at`; bootstrap owns runtime scheduling. Manifest sync creates missing scheduled tasks but does not overwrite existing runtime intervals, so an admin can tune task frequency in the UI without fighting Dishwasher reconciliation.

Future adapter-declared run policy may allow plugins to advertise which roles or principals can request specific scheduled tasks. Until that policy exists, operator run-now is limited to predeclared plugin manifest tasks and does not grant adapter-specific RBAC.

## Adapter Contract

Built-in adapter packages should use a predictable module layout:

- `plugin.py`: declares the `ServicePlugin` manifest and factories.
- `templates.py`: declares immutable ingredient, recipe, route, and scheduled-task templates.
- `adapter.py`: implements the `ExecutionAdapter` runtime boundary.
- `client.py`: provider transport/auth helpers when the adapter talks to an external API.
- `helper.py`: helper capabilities exposed for bootstrap or cross-plugin composition.
- `bootstrap.py`: credential or provider bootstrap hooks.
- `content_sync.py`: plugin-owned sync/import logic for recurring content-sync
  service-execution ingredients.

Only include modules that the adapter needs, but keep sync/import work out of
`adapter.py` and provider transport modules. The adapter should validate and
route the service execution; the named sync module should own the actual
catalog/content reconciliation.

Adapters implement `api.plugins.base.ExecutionAdapter`:

- `validate(ctx)`: Validate service-specific execution context before dispatch.
- `dispatch(ctx)`: Adapter workload execution, invoked only by Expediter on behalf of the internal execution runner. It returns an execution result that may describe completed fast work or an asynchronous provider receipt; Expediter only envelopes that result.
- `poll(ctx, service_exec_id)`: Read-only observation of provider work; return canonical runtime state for an existing receipt.
- `health_check()`: Return plugin control-plane health without exposing secrets.
- `credential_requirements()`: Optionally declare plugin adapter-owned credentials.
- `bootstrap_credentials(force=False)`: Optionally bootstrap or refresh credentials when called by the startup adapter-credentials stage under Credential Manager authority.
- `bootstrap_plugin(ctx, force=False)`: Optionally report local bootstrap readiness without exposing credentials or performing provider mutation.
- `cancel(ctx, service_exec_id)`: Optionally cancel provider work.

Expediter is the only runtime component that calls plugin adapter workload methods. Cook sends hydrated runtime rows to Expediter to create an execution-runner receipt; the internal execution runner claims that receipt and asks Expediter to execute the workload. Expediter may return a terminal or non-terminal execution envelope, but it must not reconcile the durable `dish_ingredients` row. The execution runner reconciles terminal adapter results; Timer sends status and cancellation requests to Expediter for `poll` and `cancel` and reconciles non-terminal provider receipts. Timer must not import plugin adapters or provider clients directly, and plugins must not ingest runtime work outside the Expediter path.

`poll` is a read-only observation boundary. A status request may read adapter-owned execution state or provider execution state, but it must not start or retry the requested work, mutate PoundCake-owned runtime state, perform catalog/bootstrap work, or issue provider write operations. Any operation that mutates PoundCake state or an external provider must be performed by the execution runner through Expediter as part of an ingredient execution, and subsequent status calls must only observe that execution.

**Poll for sync-completing plugins:** When a plugin completes all work during `dispatch` (no asynchronous provider work), `poll` must replay the dispatch result with `status="succeeded"` rather than returning `"errored"`. The contract requires `poll` to be a read-only observation boundary; returning `"errored"` is misleading because the work itself succeeded. A sync-completing plugin's `poll` is simply a confirmation of dispatch's already-completed work.

`ExecutionContext` is the request shape from Expediter to a plugin:

- `service_type`
- `service_exec`
- `req_id`
- `service_payload`
- `service_exec_parameters`
- `retry_count`
- `retry_delay`
- `service_exec_timeout`
- `context`

Plugin adapter-owned credentials must be written through Credential Manager.
Plugins must not write credential tables directly, and no plugin may mint
`internal_control_plane_hmac` credentials. Internal HMAC credentials are owned by
Service Identity Manager and stored separately from adapter/provider credentials.
`bootstrap_factory` is metadata-stage only and must not require either
credential encryption key to exist.

Plugins do not declare or receive database grants. Protected database work is
exposed through PoundCake helper operations that check the caller's
RBAC/service capability and then rely on Helm-managed MariaDB grants as a
backstop. Runtime adapter code, `content_sync` modules, and adapter-associated
helper scripts must not import `api.core.database` or open raw SQLAlchemy
sessions. Protected `service_plugins`, `recipes`, `ingredients`,
`recipe_ingredients`, `scheduled_tasks`, and dish metadata changes must go
through `api.services.plugin_operations`; adapter/provider credential reads and
writes must go through `api.services.credential_manager`.

Short-lived adapter/bootstrap helpers that need teardown should use a
service-layer runtime helper such as `api.services.adapter_runtime` rather than
importing cleanup functions from `api.core.database` directly.

Dedicated startup bootstrap jobs remain a separate boundary. The split startup
stages under `api.scripts.*` may use their assigned bootstrap database
principals directly because they are control-plane bootstrap code, not adapter
runtime code.

`credential-manager` is the canonical internal token-generation service. External plugin adapters may declare credential requirements and provide bootstrap hooks, but the shared helper records credentials as `credential-manager`; adapters should not name Dishwasher, Timer, Prep Chef, or their own `service_type` as credential writers.

Credential requirements are service-ecosystem scoped, not recipe scoped. A plugin should use `credential_key_id=default` unless it truly owns multiple independent credential identities. Current examples:

- `alertmanager`: optional `alertmanager_http_auth` for authenticated Alertmanager API calls.
- `bakery`: required bootstrap-owned `bakery_monitor_hmac` returned by remote Bakery registration.
- `git`: optional `git_repository_auth` for private repository reads and required
  for write operations. Payloads may provide token-style credentials or an SSH
  key path.
- `github`: optional `github_token` for private repositories or write operations. Public repositories such as `genestack-monitoring` do not need their own credential row.
- `k8s`: optional `kubernetes_kubeconfig` for Kubernetes API access. When absent, the adapter uses in-cluster service account auth; local kubeconfig fallback is a dev-only setting.
- `prometheus`: optional `prometheus_http_auth` for authenticated monitoring endpoints.
- `genestack_monitoring`: no direct credentials; it composes GitHub and Prometheus helpers.

## Plugin Registration And Authority

Plugin registration is declarative. PoundCake splits startup and ongoing sync authority:

- startup plugin-registry bootstrap discovers enabled plugins, registers `service_plugins`, and runs metadata-safe bootstrap hooks
- Dishwasher reads enabled plugin manifests and syncs:
- plugin metadata into `service_plugins`
- immutable action templates into `ingredients`
- starter recipes into `recipes` and `recipe_ingredients`
- scheduled work into `scheduled_tasks`
- credential requirements into credential bootstrap planning
- advertised route needs into RBAC planning

The manifest is not the authority boundary. Registration records what a plugin can do; RBAC policy decides what the plugin or an internal service may call.

Service registry writes are an internal service surface. Dishwasher owns
manifest-driven mutation calls for `/api/v1/internal/service-registry/*`
using its registered HMAC service identity. Human and UI access should use
redacted status or read-only definition views; they must not submit raw
ingredient registration payloads. The internal registration schema accepts only
plugin manifest contract fields and keeps lifecycle fields fully PoundCake-owned.

External provider plugins have no inbound PoundCake API authority by default. Their plugin adapter code runs only when Expediter calls it as part of an order. They return provider execution results to Expediter and Timer through the normal order workflow.

First-class internal services are separate registered service identities. Prep Chef, Cook, Timer, Dishwasher, Credential Manager, and RBAC Manager should each be represented as `service_plugins` rows with their own Service Identity Manager-owned credentials and explicitly scoped API authority.

## Credential And RBAC Managers

Adapter credential lifecycle is owned by an internal Credential Manager service.
Dishwasher can discover that a plugin needs credentials, but it should ask
Credential Manager to create, rotate, or revoke those credentials. Credential
Manager is the only service with token-generation authority for plugin
adapter-owned credentials and is not a universal secret manager.

Initial startup/bootstrap now follows this path:

1. Plugin-registry bootstrap registers internal and external `service_plugins` rows and runs metadata-safe `bootstrap_factory` hooks.
2. Service Identity Manager bootstrap creates internal control-plane HMAC credentials for PoundCake-owned services.
3. Credential Manager bootstrap calls adapter `bootstrap_credentials()` hooks and writes initial plugin adapter-owned rows into `adapter_credentials`.
4. Dishwasher performs ongoing manifest-driven sync for ingredients, recipes, scheduled tasks, and communication-route policy state.
5. Runtime workers read the credentials they need, but they do not write or rotate them.

Internal control-plane HMAC credentials are bootstrap-owned. They bind registered internal services to authenticated service identities and must not be generated by external plugins or generic adapter code.

StackStorm follows the same external-plugin credential model. Its startup API key may be sourced from deployment-managed bootstrap config during the adapter-credentials stage, then persisted as a plugin adapter-owned `stackstorm_api_key` credential for `service_type=stackstorm`. Runtime execution must load the credential from Credential Manager rather than from raw files or generic runtime tokens.

PoundCake-owned StackStorm action metadata lives with the StackStorm plugin under `api/plugins/stackstorm/content` and is synced through the plugin's `content_sync` service-execution ingredient. StackStorm pack installation and workflow file distribution are Helm/ST2-cluster bootstrap concerns, not Cook routes or PoundCake runtime APIs. Recipes invoke native ST2 assets through `stackstorm` ingredients such as `action_execution` and `workflow_execution`; PoundCake recipes are not converted into StackStorm workflows.

GitOps writes run through the external `git` plugin. Repository mutations should be modeled as `git/repo_write` recipe steps with `commit_files`, `create_pull_request`, or `commit_and_pr` operation metadata; legacy API helpers must not push branches or open PRs directly.

RBAC Manager owns internal route policy. When a plugin registers advertised route needs, RBAC Manager may sync those into policy records only through an allowlisted model. Plugin-advertised routes are not self-grants.

The target control-plane authority split is:

- Dishwasher: service registry, plugin manifest sync, recipes, scheduled task definitions.
- Credential Manager: credential generation, rotation, revocation, and credential status.
- RBAC Manager: service route policy, role bindings, and internal service scopes.
- Prep Chef: dispatchable order reads and Cook order dispatch.
- Timer: runtime row polling, reconciliation, Expediter status/cancel, and Cook dish advance.
- External provider plugins: no direct inbound API authority unless explicitly approved later.

`ExecutionResult` is the response shape from a plugin back to Expediter:

- `service_type`
- `status`
- `service_exec_id`
- `service_exec_error`
- `result`
- `raw`
- `retryable`
- `attempts`
- `context_updates`

Expediter includes plugin `context_updates` in execution envelopes. Runtime
workers preserve those updates on the dish ingredient outcome under
`_context_updates`, then Expediter folds completed dish ingredient outcomes back
into later `ExecutionContext.context.dish` payloads. The dish context contains
completed `ingredients`, an `evidence` subset for rows marked as evidence, and
merged `context_updates` such as provider ticket identifiers.

## Templates And Runtime Rows

Plugins register immutable ingredient templates at bootstrap. These rows live in `ingredients` and define:

- `service_type`
- `service_exec`
- `destination_target`
- `task_key_template`
- `service_payload_template`
- `payload_schema`
- `service_exec_parameters`
- `default_expected_secs`
- `default_timeout`
- `service_exec_expected_outcome_default`
- `ingredient_purpose`
- `is_blocking`
- `retry_count`
- `retry_delay`
- `on_failure`

Template changes always create a new ingredient revision. If bootstrap sees an
active ingredient with the same identity but a changed contract, it retires the
old active row and creates a new active row. Disabled ingredients are historical
revisions; bootstrap does not reuse or reactivate them. Public service-registry
registration rejects active contract drift with `409` rather than auto-retiring
rows on behalf of a human caller.

Plugin manifests must not include PoundCake-owned database or lifecycle fields
such as `id`, `is_active`, `deleted`, `deleted_at`, `created_at`, or
`updated_at`. Recipe templates must not include `ingredient_id` or other
database identity fields in `recipe_ingredients`; recipe steps refer to
ingredient templates by service identity.

Recipes reference ingredients through mutable `recipe_ingredients`. A recipe step can override payload, execution parameters, expected runtime, timeout, expected outcome, run phase, and run condition. PoundCake validates filled `service_payload` values against the ingredient `payload_schema`.

When an operation advertises `operation_metadata[operation].payload_schema`,
PoundCake validates the filled `service_payload` against both schemas: first the
ingredient-wide schema, then the selected operation schema. The nested schema is
plugin-owned capability metadata, not a recipe override. Recipe authors choose
operation and payload values; they cannot redefine which fields an operation
accepts.

Running an order creates `dish_ingredients`. These runtime rows hold dispatch proof and reconciliation state:

- `service_exec_id`
- `service_exec_status`
- `service_exec_start_time`
- `service_exec_completed_time`
- `service_exec_canceled_time`
- `service_exec_run_time`
- `service_exec_actual_outcome`
- `service_exec_error`

Admins may read full dish ingredient execution history through read-only observability
routes such as `GET /api/v1/orders/{order_id}/execution-history` and
`GET /api/v1/dishes/{dish_id}/ingredient-history`. These routes are for audit and
debugging only: they do not grant claim, release, reconcile, Cook, Timer, or
Expediter authority. Secret-like nested keys in runtime payload/result JSON are
redacted before returning history to a human admin.

Expected outcome evaluation is separate from transport success. A provider response with a failure-shaped payload can still satisfy a step when it matches the configured expected outcome.

## Status Vocabulary

Canonical service execution statuses are:

- Non-terminal: `pending`, `dispatched`, `running`
- Terminal: `succeeded`, `failed`, `errored`, `timeout`, `canceled`

Adapters normalize provider-native states into this vocabulary before returning results.

Plugin health statuses are:

- `unknown`
- `initializing`
- `healthy`
- `degraded`
- `unhealthy`
- `disabled`

Plugin bootstrap statuses are:

- `ready`
- `initializing`
- `failed`

## Scheduled Tasks

Plugin scheduled tasks are durable control-plane work. Dishwasher syncs plugin manifests into `scheduled_tasks`, then injects due tasks as internal orders. Those orders follow the same Prep Chef, Cook, Expediter, Timer, and Cook finalization path as alert-driven work.

The generated order `req_id` is the run identity. Runtime evidence lives on the resulting `dish_ingredients`, and task completion state is written back to `scheduled_tasks`.

Scheduled task controls are bounded operator runtime knobs. Operators may inspect plugin-owned task status, request eligible tasks to run now, tune `is_enabled` or `run_interval_seconds`, and edit adapter-declared non-secret operator configuration. Admins own task definitions, payloads, parameters, expected outcomes, and adapter credentials. This keeps sync frequency, health cadence, and other recurring plugin work inside the registered control-plane path while still allowing production tuning without a redeploy.

## Built-In Bakery Plugin

Bakery is a supported communication plugin for remote provider ticketing and notifications. Production deployments enable it through `config.enabledPlugins`, then configure the remote connection and bootstrap credential through the plugin configuration and credential APIs; see [REMOTE_BAKERY.md](REMOTE_BAKERY.md) and [plugins/bakery.md](plugins/bakery.md).

## Built-In Alertmanager Plugin

Alertmanager is a community-tier monitoring plugin that owns read-only Alertmanager API access and silence synchronization. The plugin exposes:

- `health_check`: scheduled plugin health.
- `sync_silences`: recurring synchronization of Alertmanager silences into PoundCake suppressions.
- `inspect`: one utility ingredient with selectable operations for `list_alerts`, `list_groups`, and `find_inhibited_by_source`.

Recipes opt into inhibition context by adding the `alertmanager-inspect` ingredient and selecting `find_inhibited_by_source`. Alertmanager remains the source of truth for inhibition, silencing, and mute evidence; PoundCake records and presents Alertmanager status data instead of inferring root-cause relationships from labels.

## Built-In Kubernetes Plugin

Kubernetes is a community-tier infrastructure plugin until its supported-tier test coverage is in place. It owns Prometheus Operator `PrometheusRule` CRD management through the normal plugin adapter and order workflow. Core Prometheus rule APIs must not patch CRDs directly; CRD apply/delete/list/get operations are represented as `service_type=k8s`, `service_exec=prometheus_rule` executions. See [plugins/k8s.md](plugins/k8s.md).

The intended supported-tier promotion candidates are `k8s`, `prometheus`, `alertmanager`, `stackstorm`, `git`, and `github` once their support test coverage is in place.

## Reference Development Plugin

The PoundCake-provided `dummy` plugin is the standard reference implementation for plugin and adapter development. It demonstrates manifest discovery, adapter lifecycle, immutable templates, communication operations, scheduled health checks, helper registration, bootstrap hooks, expected-outcome matching, cancellation, and Timer reconciliation. See [DEVELOPER.md](DEVELOPER.md) and [plugins/dummy.md](plugins/dummy.md).
