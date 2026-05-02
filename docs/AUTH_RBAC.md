# PoundCake Auth And Internal RBAC

## Summary

PoundCake applies authentication and RBAC as a global FastAPI dependency. Human users are authorized with role-based access control. Internal workers authenticate with signed HMAC requests backed by registered `service_plugins` credentials and are authorized by service-specific route policy.

This doc describes the current auth shape after the PoundCake 2.0 service plugin rewrite. It intentionally does not replace provider setup docs for Auth0, Azure AD, or local auth.

## Auth Layers

- Public paths bypass auth for edge liveness/readiness, metrics, and login/device/OIDC bootstrap.
- Human sessions resolve to one of `reader`, `operator`, or `admin`.
- Internal HMAC requests resolve to `service` plus registered service plugin metadata.
- Raw service-token requests do not create an internal service principal.

The global dependency is `require_auth_if_enabled`, mounted on the FastAPI app. It authenticates the request, resolves an `AuthContext`, and calls `ensure_request_authorized`.

## Public Paths

Public paths bypass authentication even when auth is enabled:

- `/`, `/metrics`, `/docs`, `/redoc`, `/openapi.json`
- `/livez`, `/readyz`
- `/api/v1/auth/login`, `/api/v1/auth/providers`
- `/api/v1/auth/oidc/login`, `/api/v1/auth/oidc/callback`
- `/api/v1/auth/device/start`, `/api/v1/auth/device/poll`
- `/api/v1/webhook`, guarded by the webhook bearer token at the route handler
- `/static/*`
- `OPTIONS` preflight requests

The application health routes `/api/v1/live`, `/api/v1/ready`, and `/api/v1/health` require reader auth. Use `/livez` and `/readyz` for unauthenticated Kubernetes probes.

## Route RBAC Matrix

| Route family | Read | Write |
|---|---:|---:|
| edge liveness/readiness, metrics, docs, static assets | public | n/a |
| `/api/v1/live`, `/api/v1/ready`, `/api/v1/health` | reader | n/a |
| login, OIDC, and device flow bootstrap | public | public bootstrap |
| `/api/v1/auth/me`, logout | reader | reader |
| `/api/v1/auth/principals`, `/api/v1/auth/bindings` | admin | admin |
| `/api/v1/plugins` summary and stored health | reader | operator for runtime knobs |
| `/api/v1/plugins/{service_type}/configuration`, direct connection test | operator | operator |
| `/api/v1/plugins/{service_type}/credentials`, direct content sync | admin | admin |
| `/api/v1/service-registry/ingredients/status` | reader | n/a |
| `/api/v1/service-registry` plugin-owned definition detail | operator/admin read-only during conversion | scoped Dishwasher only for writes |
| `/api/v1/recipes/status`, recipe ingredient status | reader | n/a |
| `/api/v1/recipes` operator-authored definition detail | operator | operator; scoped Dishwasher |
| `/api/v1/scheduled-tasks/status`, run-now | reader for status | operator for run-now; service-only due claim |
| `/api/v1/communications/policy` | reader | admin |
| `/api/v1/suppressions` | reader | operator |
| `/api/v1/webhook` | route bearer token | route bearer token |
| `/api/v1/orders/status`, order timeline | reader | n/a |
| `/api/v1/orders` runtime detail | service; scoped Prep Chef | service; scoped Dishwasher |
| `/api/v1/orders/{id}/execution-history` | admin | n/a |
| `/api/v1/dishes/status`, dish ingredient status | reader | n/a |
| `/api/v1/dishes` runtime detail | service; scoped Timer where applicable; admin for execution-history reads | service |
| `/api/v1/scheduled-tasks/status` | reader | n/a |
| `/api/v1/scheduled-tasks` full detail | admin; scoped Dishwasher | operator for enabled/interval only; admin/scoped Dishwasher for full payload |
| `/api/v1/scheduled-tasks/due` | scoped Dishwasher | n/a |
| `/api/v1/cook/*` | reader | service; scoped Prep Chef, Execution Runner, or Timer depending on route |
| `/api/v1/dish-ingredients/*` runtime routes | service; scoped Timer or Execution Runner | service; scoped Timer or Execution Runner |
| `/api/v1/expediter/status/*` | service; scoped Timer | n/a |
| `/api/v1/expediter/cancel/*` | n/a | service; scoped Timer |
| `/api/v1/expediter/execute/*` | n/a | service; scoped Execution Runner |
| default GET routes | reader | n/a |
| default non-GET routes | n/a | admin |

The UI exposes RBAC policy management at `/config/access` as the `RBAC` configuration page. It uses the same `/api/v1/auth/principals` and `/api/v1/auth/bindings` endpoints and is visible only to admin-capable users.

## Role Responsibilities

| Role | Owns | Must not own |
|---|---|---|
| `reader` | Redacted status, health, timelines, plugin summaries, and reporting activity. | Raw runtime payloads, credentials, provider IDs, claim metadata, or configuration writes. |
| `operator` | Recipes, suppressions, non-secret adapter configuration, plugin enabled/cadence/query-limit tuning, health-check cadence, and scheduled-task enabled/interval changes. | Kubeconfigs, usernames, passwords, bearer tokens, plugin credential writes, ingredient template writes, scheduled-task payloads, raw order/dish detail, or RBAC bindings. |
| `admin` | RBAC, adapter credentials, global communications policy, read-only ingredient template inspection during conversion, full scheduled-task payload definitions, redacted runtime execution history, and destructive repo/import-clear workflows. | Internal service claim/reconcile/update authority, service-registry mutation, or unredacted credentials/secrets. |
| `service` | Scoped internal workflow execution, raw order/dish/dish-ingredient runtime records, Cook/Timer/Dishwasher/Expediter routes, and service-owned order mutation. | Human configuration surfaces outside its explicit service route policy. |

## Reporting And Detail Route Split

Control-plane routes are classified by payload sensitivity:

- `public`: unauthenticated probes, docs/static, auth bootstrap, and webhook ingress with route-level bearer validation.
- `reporting/status`: redacted list and drilldown views for operators and readers.
- `configuration/editor`: desired-state detail reads and writes. Operators own recipes, safe runtime tuning, and non-secret adapter configuration. Admins own adapter credentials, RBAC, read-only plugin-owned ingredient template inspection during conversion, and scheduled task payload definitions.
- `admin/observability`: full dish ingredient execution history for incident/debug review. This is read-only, preserves internal service mutation boundaries, and redacts obvious secret-bearing nested keys from payload/result JSON.
- `internal/runtime`: service-only observed-state detail, raw workflow rows, link-table internals, claims, reconciliation, Expediter status/cancel, and service-owned order mutation.

Reporting/status routes must not expose raw alert labels or annotations, credential material, provider execution IDs, claim metadata, raw service payloads, expected outcomes, actual outcomes, or provider error payloads. UI operator reads should consume status routes for observed/runtime state. Desired-state definition routes are split by ownership: operators author recipes and tune cadence/limits, operators manage non-secret adapter connection settings, plugins declare ingredient templates, Dishwasher registers them through the internal service boundary, and admins own credential/RBAC trust boundaries. Observed-state runtime mutation, claims, and reconciliation remain service-only; admins may read redacted execution history for audit and debugging.

Service-registry mutation is a scoped internal Dishwasher/bootstrap surface, not
a human UI authoring surface. Human readers may use
`/api/v1/service-registry/ingredients/status` and, during the conversion,
admin/operator read-only detail views for debugging. The target API shape is to
move raw ingredient registration off the human-facing route contract and onto an
internal service URL/schema that omits lifecycle fields such as `is_active`.

Plugin registry routes are read-only views over persisted PoundCake state.
`GET /api/v1/plugins` and `GET /api/v1/plugins/{service_type}/health` must not
call plugin adapters or live providers. If operators need an active health probe,
model it as a plugin-owned health-check ingredient/order so execution, RBAC, and
adapter responses stay inside the service plugin workflow boundary.

Plugin bootstrap hooks are limited to local registration/credential seeding and
their own helper capabilities. Hooks must not use other plugins' helpers to pull
or push external provider state during application bootstrap. External catalog
sync, repository writes, Kubernetes reconciliation, and similar provider work
must run through service-execution ingredients/orders or through explicit
cluster bootstrap tooling outside the PoundCake runtime.

Execution adapters may still declare and consume helper capabilities from other
plugins when that work is reached through an ingredient/order. Cross-adapter
composition belongs behind Expediter execution records, not behind startup
hooks or read-only router views.

The Kubernetes Python client is owned by the `k8s` plugin boundary. Other API
services must not create Kubernetes clients to read Secrets or cluster state.
Auth bootstrap credentials are supplied to workloads through Kubernetes
`secretKeyRef` environment variables, and any runtime Kubernetes action must flow
through k8s plugin ingredients/orders.

Guarded `GET` route families are inventoried in
`api.services.route_surface_contract`. Adding a `GET` route under orders,
recipes, dishes, dish-ingredients, service-registry, scheduled-tasks,
observability, communications, plugins, or suppressions requires an explicit
route-surface entry. The entry must classify the route as `reporting_status`,
`configuration_editor`, `admin_observability`, or `internal_runtime`, record
the expected minimum role, and include or adjust schema guard coverage when the
route is a reporting/status surface.

## Internal Service Policy

Internal workers use HMAC signatures loaded from their registered
`service_identity_credentials` row. Bootstrap generates and stores the internal
HMAC credential in the database with
`credential_type=internal_control_plane_hmac`; Helm does not expose HMAC
secrets as workload environment variables.

HMAC authentication now resolves:

- `service_plugin_id`
- `service_type`
- `plugin_type`
- `enabled`
- `credential_scope`

Missing, disabled, undecryptable, or secretless service identity credentials cannot authenticate with internal HMAC credentials. The HMAC key proves possession, but authorization comes from the registered and enabled service plugin row.

PoundCake does not accept `X-Auth-Token` or `Authorization: Bearer <service-token>` as an internal service identity. Requests must be signed with the service-specific HMAC key and must map to a registered service plugin row.

Allowed service routes:

| Service | Allowed routes |
|---|---|
| `prep-chef` | `GET /api/v1/orders`, `POST /api/v1/cook/orders/{order_id}`, `GET /api/v1/plugins/prep-chef` |
| `execution-runner` | `GET /api/v1/dish-ingredients/execution-pending`, `POST /api/v1/dish-ingredients/{id}/execution-claim`, `POST /api/v1/dish-ingredients/{id}/execution-release`, `POST /api/v1/dish-ingredients/{id}/reconcile`, `POST /api/v1/expediter/execute/{dish_ingredient_id}`, `POST /api/v1/cook/dishes/{dish_id}/advance`, `GET /api/v1/plugins/execution-runner` |
| `timer` | `GET /api/v1/dish-ingredients/in-flight`, `GET /api/v1/dish-ingredients/cancel-requested`, `GET /api/v1/dishes/{dish_id}/ingredients`, `POST /api/v1/dish-ingredients/{id}/poll-claim`, `POST /api/v1/dish-ingredients/{id}/poll-release`, `POST /api/v1/dish-ingredients/{id}/reconcile`, `GET /api/v1/expediter/status/*`, `POST /api/v1/expediter/cancel/*`, `POST /api/v1/cook/dishes/{dish_id}/advance`, `GET /api/v1/plugins/timer` |
| `dishwasher` | `GET /api/v1/plugins`, `GET /api/v1/plugins/dishwasher`, `GET/POST/PATCH /api/v1/service-registry*`, `GET/POST/PUT/PATCH /api/v1/recipes*`, `GET/POST/PATCH /api/v1/scheduled-tasks*`, `POST /api/v1/orders` |
| `credential-manager` | `GET /api/v1/plugins`, `GET /api/v1/plugins/credential-manager` |

External provider plugins do not receive inbound internal API authority by default. They execute through Expediter and return results through plugin adapter calls, not by calling PoundCake internal workflow routes directly.

Timer's route scope is reconciliation-only. It may claim, release, and reconcile runtime rows, then ask Cook to advance a dish. Its only provider-facing authority is routed through Expediter status and cancel endpoints; it must not call plugin adapters or provider clients directly.

## Credential And Identity Policy

PoundCake separates secret-bearing auth domains by owner:

- Auth Service owns human UI/CLI/API identity, sessions, RBAC bindings, and IdP flows.
- Service Identity Manager owns `internal_control_plane_hmac` credentials in
  `service_identity_credentials`.
- Credential Manager owns adapter/provider credentials in `adapter_credentials`.
- `dishwasher` owns service registry and plugin bootstrap discovery, but it does
  not write adapter credentials.
- Prep Chef and Timer can read their own registered HMAC credentials for signing
  workflow calls, but they cannot create or rotate adapter credentials.
- external provider plugins cannot write credential tables directly.

## Service-Only Routes

These routes are service-only in normal operation:

- non-GET `/api/v1/orders`
- non-GET `/api/v1/dishes`
- non-GET `/api/v1/cook/*`
- `GET /api/v1/scheduled-tasks/due`
- Timer and Execution Runner runtime routes under `/api/v1/dish-ingredients/*`
- `POST /api/v1/expediter/execute/*`
- `POST /api/v1/expediter/cancel/*`

All service-only routes require a registered HMAC service identity. A generic service context without `service_type` is rejected even when the route's minimum role is `service`.

Webhook ingress is different from internal runtime HMAC. Remote Alertmanager posts to `/api/v1/webhook` with the configured webhook bearer token, and the handler validates that token before generating orders.

## Security Effect

The internal HMAC key is no longer just proof of possession for a generic service principal. The request is tied to a registered and enabled service plugin row, then checked against service-specific route policy.

This reduces blast radius:

- a Timer key cannot modify recipes or auth bindings
- a Prep Chef key cannot reconcile runtime rows
- a Dishwasher key cannot call Timer-only reconciliation routes
- an external provider plugin key cannot call internal order workflow routes unless a future policy explicitly allows it

## Implementation Anchors

- App-wide dependency: `api.main.app` uses `require_auth_if_enabled`.
- Public path and route-role mapping: `api.services.auth_service`.
- HMAC request parsing and registered service credential lookup: `api.api.auth`.
- Service identity credential rows: `service_identity_credentials` joined to `service_plugins`.
- HMAC signing helper used by internal workers: `kitchen.service_helpers`.

## Test Coverage

Current guardrails cover:

- public, human, and service role route mapping
- route classification for guarded control-plane GET surfaces
- reporting/status schema redaction by field name
- internal HMAC context identity fields
- rejection of disabled internal HMAC service plugins
- rejection of unregistered generic service-token contexts
- allowed Prep Chef, Timer, and Dishwasher route sets
- denial of cross-service and external plugin internal workflow access

Run focused checks:

```bash
.venv/bin/python -m pytest \
  tests/test_internal_hmac_auth.py \
  tests/test_control_plane_contract.py \
  tests/test_internal_plugin_runtime.py \
  tests/test_plugin_bootstrap.py
```
