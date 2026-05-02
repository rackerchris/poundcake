# Tests

This directory now contains the first-pass plugin contract tests for the
PoundCake rewrite. The suite is intentionally small and dummy-focused.

## Unit Tests

Run the Python contract tests:

```bash
.venv/bin/python -m pytest tests
```

The unit suite validates:

- service payload JSON Schema validation
- expected-vs-actual outcome matching, including expected negative results
- dummy plugin manifest/templates/plugin adapter lifecycle
- execution segment ordering and parallel-group metadata
- plugin bootstrap helper behavior
- Timer terminal status and retry behavior

## Security Regression

The negative security suite treats PoundCake trust boundaries as attack surfaces
and keeps the following abuse cases under regression:

| Boundary | Abuse cases |
| --- | --- |
| Credential Manager | Cross-service credential reads/writes, duplicate key reuse across services, public-read policy misuse |
| Internal HMAC | Wrong scope, disabled/external plugin identities, duplicate `key_id`, stale timestamp, tampered method/path/body/query, nonce replay |
| Human RBAC | Anonymous access to protected routes, reader/operator/admin boundary crossing, service-only route denial |
| Reporting surfaces | Secret-bearing nested fields, raw execution payload leakage, sibling-prefix route confusion |
| Webhook ingress | Bearer token valid only on `/api/v1/webhook`, never as control-plane auth |

Focused regression command:

```bash
.venv/bin/python -m pytest \
  tests/test_internal_hmac_auth.py \
  tests/test_plugin_credentials.py \
  tests/test_auth_integration.py \
  tests/test_control_plane_contract.py \
  tests/test_webhook_auth.py
```

## Dummy E2E

The shell e2e runner assumes the local dummy-only devstack is already running:

```bash
bash docker/devstack/create.sh
```

Run all dummy contract scenarios:

```bash
bash tests/run_e2e.sh
```

The runner waits on unauthenticated `/readyz`, then logs in when a password
provider is enabled. Docker devstack defaults to `admin` / `poundcake-dev`.
Override with `AUTH_USERNAME`, `AUTH_PASSWORD`, and `AUTH_PROVIDER`.

Run one scenario:

```bash
bash tests/run_e2e.sh --single positive
bash tests/run_e2e.sh --single negative
bash tests/run_e2e.sh --single expected-negative
bash tests/run_e2e.sh --single cancel-parallel
bash tests/run_e2e.sh --single rapid-parallel
```

Override the API URL:

```bash
bash tests/run_e2e.sh --api-url http://127.0.0.1:8000/api/v1
```

## Live Security Abuse Harness

The live abuse runner is intentionally narrow and denial-focused. It validates
real requests against a running devstack without attempting destructive
persistence or secret exfiltration.

The stock docker devstack exposes anonymous traffic plus the local superuser
login path. Reader/operator human boundary abuse remains covered in the pytest
security regression layer unless you extend devstack with additional auth
principals.

Expected-denial scenarios:

| Scenario | Expected result |
| --- | --- |
| Anonymous access to protected API routes | `401` |
| Webhook bearer used on non-webhook routes | `401` |
| Generic bearer token used on internal service routes | `401` |
| Timer HMAC calling Prep Chef or admin routes | `403` |
| Replayed mutating HMAC request | `401` or `403` |
| Tampered signed path/body/query request | `401` |

Run it after the local devstack is up:

```bash
bash docker/devstack/create.sh
bash tests/run_security_abuse_e2e.sh
```

List scenarios:

```bash
bash tests/run_e2e.sh --list
```

## E2E Coverage

- `dummy-positive-result`: order completes and the runtime row succeeds.
- `dummy-negative-result`: order fails and stores `{"success": false}`.
- `dummy-expected-negative-result`: the plugin returns `{"success": false}`,
  but the recipe expects that outcome, so PoundCake marks the row succeeded.
- `dummy-parallel-slow-cancel-result`: a resolved Alertmanager event cancels
  every in-flight row in the active parallel group, then resolving/default comms
  allow the order to complete.
- `rapid-parallel`: posts multiple alerts back-to-back using the parallel slow
  dummy recipe, verifies Cook dispatches two runtime rows for each order, then
  resolves each alert and confirms both rows cancel cleanly.

## StackStorm Adapter E2E

The StackStorm adapter e2e runner assumes `helm/devstack` is running with
PoundCake in the `poundcake` namespace and StackStorm in the `stackstorm`
namespace:

```bash
bash tests/run_stackstorm_action_e2e.sh
```

The runner configures PoundCake's StackStorm adapter connection, creates a
temporary recipe with the `stackstorm-action-execution` ingredient, posts an
Alertmanager webhook, and verifies the resulting order executes StackStorm
`core.echo` successfully.

The StackStorm workflow remediation e2e runner exercises the auto-remediation
shape used by StackStorm-backed Kubernetes recipes:

```bash
bash tests/run_stackstorm_workflow_remediation_e2e.sh
```

It seeds Alertmanager directly, posts the matching alert through PoundCake's
webhook, and verifies the four-step path completes: Alertmanager guard, k8s
evidence, Alertmanager guard, and StackStorm workflow execution. It also posts
a guard-false case and verifies Timer cancels downstream remediation before
StackStorm is called.

## Kubernetes Pod Action E2E

The Kubernetes pod-action e2e runner is opt-in because it creates and deletes a
temporary pod in the target cluster:

```bash
bash tests/run_k8s_pod_action_e2e.sh
```

It assumes the Helm/devstack cluster is running with the `k8s` plugin enabled
and pod-action RBAC installed. The runner creates a temporary crash-looping pod,
creates a three-step recipe using `k8s/pod_action` for logs, events, and exact
pod deletion, posts an Alertmanager webhook, and verifies the ordered runtime
steps succeeded before confirming the test pod was deleted.

Useful overrides:

```bash
NAMESPACE=poundcake TEST_NAMESPACE=poundcake bash tests/run_k8s_pod_action_e2e.sh
API_URL=http://127.0.0.1:8000/api/v1 bash tests/run_k8s_pod_action_e2e.sh
```
