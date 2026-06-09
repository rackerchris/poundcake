# PoundCake Contracts

These are hard system invariants. Any change that could violate these rules must be reviewed carefully. They are extracted from the codebase and existing documentation to serve as a single reference for AI-assisted development.

---

## 1. Workflow Execution Path

ALL execution MUST follow the singular order pipeline. There are no bypasses.

1. Intake creates or updates an `order`.
2. Prep Chef claims dispatchable orders (`GET /orders?status=new`) and calls `POST /cook/orders/{id}`.
3. Cook creates a phase-scoped `dish`, seeds `dish_ingredients` via the dish planner, and dispatches via Expediter.
4. Expediter is the sole outbound service-plugin gateway. Only Cook may call `expediter_dispatch_from_cook`.
5. Expediter calls the plugin adapter and owns provider health gating plus canonical execution envelopes.
6. Timer polls in-flight runtime rows through Expediter read-only status/cancel endpoints, reconciles terminal results, and calls Cook to advance the dish.
7. Cook evaluates all runtime rows in the dish; if blocking, dish advances to terminal failure. If all succeed, dish becomes complete.
8. Final dish state rolls up to the order.

### Workflow Invariants

- **Every execution enters through the order pipeline.** Alertmanager webhooks, scheduled plugin work, and manual run requests all surface as orders. No component may execute provider work outside this path.
- **Expediter is the sole outbound gateway.** No component may call `adapter.dispatch`, `adapter.poll`, or `adapter.cancel` directly. Only Expediter invokes plugin adapter workload methods.
- **Timer is a read-only observer.** Timer calls `/expediter/status/{type}/{id}` and `/expediter/cancel/{type}/{id}` only. It must never import provider plugins, call adapters directly, or dispatch new provider work.
- **Cook owns dish planning.** `api.services.dish_planner` is Cook's core decision engine. Plugins describe capabilities through ingredient templates but do not decide global workflow semantics or seed runtime rows.
- **Dish advancement requires terminal prior state.** A resolving dish cannot be dispatched until all firing dishes for the same order are terminal. No segment dispatches while in-flight executions (`dispatched`/`running`) exist.
- **Blocking failures halt progress and cascade-cancel future segments.** When a step fails with `on_failure != "continue"`, all pending steps in later execution groups are cancelled automatically.
- **Status transitions are strictly enforced.** `service_exec_status` moves `pending → dispatched/running → terminal(succeeded/failed/errored/timeout/canceled)`. Terminal states are self-locking. Dish and order processing follow their own strict state machines.
- **Provider `poll()` is strictly read-only.** Poll must not start/retry work, mutate runtime state, perform catalog/bootstrap, or issue provider write operations. For sync-completing plugins, `poll` replays the dispatch result with `status="succeeded"` rather than returning `"errored"`.
- **Plugin health gates execution.** A plugin in `failed`, `disabled`, `unknown`, or `initializing` state blocks all execution (except health checks). Runner and Cook respect this gate.
- **Adapter results are validated for identity.** `ExecutionResult.service_type` must match the registered adapter that was dispatched. An adapter cannot spoof another service's identity.

---

## 2. Database Access Contract

The rule is clear: **Plugins do not declare or receive database grants.**

Protected database work is exposed through PoundCake helper operations that check the caller's RBAC/service capability. Helm-managed MariaDB grants are the hard backstop if a pod tries to bypass the helper with raw SQL.

- **Five distinct database session factories** with separate connection pools: main (general API), credential manager, auth verifier, per-service worker readers (prep-chef, timer, expediter-runner, dishwasher), and optional readonly.
- **Credential Manager database URL is required**, not optional. The system is not designed to run without it.
- **Database capabilities are enforced through a capability matrix.** The seven capabilities (`adapter-credential:read/write`, `service-identity:read-own/write`, `service-plugin:read/update-status`, `migration:apply`, `app:data-read/write`) are granted based on role or service type.
- **Adapters cannot write credential tables directly.** Adapter credential lifecycle is owned exclusively by Credential Manager (`credential-manager` service type). No other service may write `adapter_credentials`.
- **Credential Manager cannot write `internal_control_plane_hmac` credentials.** HMAC identity is owned exclusively by Service Identity Manager.
- **Worker readers can only read their own service identity credentials.** Timer can read its own `service-identity:read-own` but cannot read Dishwasher's. Service-scoped DB views enforce this.
- **`principal_for_internal_service()` may only be called from boundary modules:** `database_access.py`, `credential_manager.py`, `service_identity.py`, `plugin_bootstrap.py`.
- **`allow_public_read` defaults to `false`** on all adapter credentials. Operators must explicitly enable unauthenticated public-read paths.

---

## 3. RBAC and Endpoint Separation

Internal endpoints return full data. External endpoints return sanitized/redacted data. All I/O is validated against schemas.

### Auth Layers
- **Public paths** bypass authentication: `/`, `/docs`, `/redoc`, `/openapi.json`, `/livez`, `/readyz`, public auth entrypoint routes, `/api/v1/webhook` (guarded by route-level bearer), `/static/*`, `OPTIONS` preflight.
- **Human sessions** resolve to `reader`, `operator`, or `admin`.
- **Internal HMAC requests** resolve to `service` plus registered service plugin metadata.
- **Generic service tokens** (`X-Auth-Token`, `Authorization: Bearer <service-token>`) are rejected as internal service identity. Requests must use HMAC signatures mapped to registered service plugin rows.

### Route Classification
- `public`: unauthenticated probes, docs, public auth entrypoints, webhook ingress with bearer validation.
- `reporting/status`: redacted list and drilldown views for operators/readers. Must not expose raw alert labels/annotations, credentials, provider execution IDs, claim metadata, raw service payloads, expected/actual outcomes, provider error payloads, or `fingerprint`, `labels`, `annotations`, `raw_data`, `service_payload`, `claims_metadata`.
- `configuration/editor`: desired-state detail reads/writes. Operators author recipes and tune cadence/limits. Admins own credentials/RBAC/ingredient template inspection/scheduled task payloads.
- `admin/observability`: full dish ingredient execution history for audit/debug. Read-only. Redacts secret-bearing nested keys in payload/result JSON.
- `internal/runtime`: service-only observed-state detail, raw workflow rows, claims, reconciliation, and Expediter routes.

### Endpoint Contracts
- **External endpoints:** all response schemas are audited against a `SENSITIVE_STATUS_FIELDS` list. No status schema may include fields like `labels`, `raw_data`, `service_payload`, `annotations`, `claims_metadata`, `fingerprint`. Depth is limited to 6, lists capped at 20 items, strings truncated at 1000 chars.
- **Internal endpoints:** full/unsanitized data. But schema validation and RBAC checks still apply.
- **Admin execution history redacts secrets:** any dict key containing `authorization`, `credential`, `password`, `private_key`, `refresh_token`, `secret`, or `token` is replaced with `"[redacted]"`.
- **Status strings are sanitized:** patterns like `token=<value>`, `password=<value>` are redacted before returning.
- **All input validated against JSON schemas** before processing. All output validated/serialized before returning. Query parameters validated against typed models (422 for unknown/out-of-range params).

### Service Route Policy (per-service, exact-match)
- **prep-chef:** order reads, Cook dispatch, admin plugin config
- **execution-runner:** dish-ingredient executor routes, expediter execute, Cook dish advance
- **timer:** dish-ingredient in-flight/cancel-requested, expediter status/cancel, Cook dish advance, admin plugin config
- **dishwasher:** service-registry, recipes, scheduled tasks, order creation, full admin
- **credential-manager:** plugin read, credential management routes only
- **External plugins:** no inbound PoundCake API authority by default. Execute through Expediter only.

### Security Effects
- A Timer key cannot modify recipes or auth bindings
- A Prep Chef key cannot reconcile runtime rows
- A Dishwasher key cannot call Timer-only reconciliation routes
- No sibling path prefix collisions (e.g., `/recipes-admin` must not match `/recipes`)
- Disabled service plugins are rejected from HMAC authentication
- Unique key_id must map to exactly one service plugin

---

## 4. Credential Management Contract

ALL credential access flows through Credential Manager.

- **Credential Manager is the only service with token-generation authority** for plugin adapter-owned credentials.
- **Internal HMAC credentials are bootstrap-owned**, stored in `service_identity_credentials`, encrypted in storage, and not exposed as environment variables.
- **Credential requirements are service-ecosystem scoped**, not recipe scoped. A plugin uses `credential_key_id=default` unless it owns multiple independent credential identities.
- **Credential requirements are declared by adapters** but must be written through Credential Manager. No plugin may mint `internal_control_plane_hmac`.
- **Credential Manager bootstrap** runs at startup in its own Helm job with its own database principal, separate from plugin registry bootstrap and service identity bootstrap.
- **Credential scope check is mandatory** for internal HMAC auth: `credential_scope = "poundcake_control_plane"` is required. Any other scope is rejected.
- **HMAC authentication covers method, path, body, and timestamp.** Clock skew window is enforced. Nonce replay protection is atomic for mutating HTTP methods.
- **Webhook ingress is separate from internal HMAC.** Alertmanager uses `credential_scope="alertmanager_webhook"` with a bearer token, not HMAC signatures.

---

## 5. Plugin Contract

### Manifest
- Plugin manifests are declarative capability declarations, not authority boundaries. Registration records what a plugin can do; RBAC policy decides what it may call.
- Manifests must not include PoundCake-owned database or lifecycle fields (`id`, `is_active`, `deleted`, `deleted_at`, `created_at`, `updated_at`, `ingredient_id`).
- Manifests may not set `next_run_at`; bootstrap owns runtime scheduling.
- Manifests may not declare control-plane fields in ingredient templates or recipe steps.
- Each plugin must declare a `plugin_health_check` scheduled task with one of these types: `plugin_health_check` or `service_execution`.
- Plugin manifests must not use reserved internal service types (`prep-chef`, `cook`, `timer`, `dishwasher`, `credential-manager`, `execution-runner`).
- Supported tier requires PoundCake approval; all plugins default to community tier.

### Plugin Bootstrap
- Bootstrap hooks are metadata-stage only. They may validate local helpers or report metadata-safe readiness but must not write credentials, mint HMAC identities, or perform registry writes.
- Bootstrap hooks must not require external helper capabilities. They may only depend on their own helpers.
- External catalog sync, repository writes, and Kubernetes reconciliation must run through service-execution ingredients, not startup hooks.
- Bootstrap must not own manifest sync writes. Dishwasher owns ingredients, recipes, scheduled tasks, and communication-route policy sync.
- Cross-adapter composition belongs behind Expediter execution records, not startup hooks or read-only router views.

### Plugin Adapters
- `dispatch()` starts work. `poll()` only reads it.
- `validate_credential_payload()` validates credential inputs for the adapter.
- `health_check()` returns plugin control-plane health without exposing secrets.
- `cancel()` optionally cancels provider work.
- `credential_requirements()` optionally declares credential needs.
- **Adapters may not import `kubernetes` client outside `api/plugins/k8s/`.**
- **Adapters may not call `/run-now` for scheduled tasks.** Dishwasher is the only service that runs scheduled tasks.
- **GitOps writes must go through the `git` plugin.** Legacy API helpers must not push branches or open PRs directly.
- **Core Prometheus rule APIs must not patch CRDs directly.** Use `service_type=k8s`, `service_exec=prometheus_rule` executions.

### Ingredient Templates
- Ingredients are immutable. Changes create new revisions; bootstrap retires active ones on contract drift (409 from public registration). Disabled ingredients are historical and never reactivated.
- Recipe steps refer to ingredient templates by service identity, not by `ingredient_id`.
- Operation-specific `operation_metadata[operation].payload_schema` is plugin-owned capability metadata. Recipe authors choose operation and payload values; they cannot redefine which fields an operation accepts.
- Communication ingredients (purpose=`comms`) cannot be persisted in `recipe_ingredients`.

---

## 6. Service Registry Contract

- Service-registry mutation is an internal service surface owned by Dishwasher. Not a human UI authoring surface.
- Plugin bootstrap hooks must not own ingredient registration.
- `GET /api/v1/plugins` and `GET /api/v1/plugins/{service_type}/health` must not call plugin adapters or live providers. Health probes belong in plugin-owned scheduled tasks or health-check ingredients.
- Guarded GET route families require explicit `route_surface` entries classifying the route as one of: `reporting_status`, `configuration_editor`, `admin_observability`, or `internal_runtime`.
- Removed execution routes must stay removed. The route surface is an allowlist model.

---

## 7. UI Contract

- UI reporting surfaces must use **status routes** (e.g., `/dishes/status`), not raw routes (e.g., `/dishes`).
- UI operator reads must not call raw routes like `/api/v1/health`, `/api/v1/dishes?limit=100`, or `/api/v1/stats` for observed state.
- UI must not call `/test-connection` or `/sync-content` directly.
- UI must not contain `operatorCredentialInput`, `operatorCredentialKeyIdInput`, or `/credentials` in scheduled task run-now mutations.
- The UI exposes RBAC policy management at `/config/access` for admin users, using `/api/v1/auth/principals` and `/api/v1/auth/bindings`.
- UI numeric fields must match API contracts.

---

## 8. Scheduled Tasks Contract

- Dishwasher is the only service that claims due scheduled tasks and injects orders.
- Operators may `run-now` only predeclared plugin manifest tasks (`plugin_health_check` or `service_execution`) when enabled. Run-now is a runtime command: the requester cannot provide payloads, parameters, credentials, expected outcome, cadence, adapter configuration, or any execution override.
- `next_run_at` is not part of the operator patch contract.
- Scheduled task `due` claim is service-only.
- Operators may tune `is_enabled` and `run_interval_seconds` only.
- Admins own task definitions, payloads, parameters, expected outcomes, and adapter credentials.
- Operator mutation allowlists exclude credential/secret/payload/parameter/outcome fields.

---

## 9. Communication Policy Contract

- Communication policy CRUD is admin-only (`PUT`), reader-only (`GET`).
- Communication ingredients are identified by `ingredient_purpose == "comms"`.
- Managed communication recipe steps use prefix `pcmcomms.`.
- Managed recipe communication steps must validate service payload against ingredient payload schema before creating `RecipeIngredient`.
- When a recipe has a local communication step, inherited policy steps for the same route must not be seeded.
- Communication steps from inherited policy are deferred to finalizer position.

---

## 10. Helper Capabilities Contract

- Helpers declare narrow reusable provider/catalog functions as capabilities for bootstrap or cross-plugin composition.
- Helpers must not own hidden workflow execution, broad provider mutation outside declared capabilities, or bypass service-execution ingredients for cross-adapter runtime work.
- Cross-adapter composition must go through Expediter execution records.
- Kubernetes Python client is owned exclusively by the `k8s` plugin boundary.
