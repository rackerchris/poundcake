# PoundCake CLI

`cakectl` is the preferred command name for the PoundCake control-plane CLI.
It uses PoundCake's human auth model directly: log in with a username/password
or device flow, receive a PoundCake session, and reuse that session for later
requests.

## Auth Model

- Human CLI auth is session-based.
- Use `--token` or `POUNDCAKE_TOKEN` only for an existing PoundCake session token.
- Use `--username` and `--password` for non-interactive operator use; the CLI
  will log in automatically when it needs a session.
- Internal service HMAC auth is not part of the human CLI surface.

Examples:

```bash
cakectl --url http://localhost:8080 auth login --provider local --username alice --password secret
cakectl --url http://localhost:8080 auth me
cakectl --url http://localhost:8080 orders list --processing-status processing
```

## Global Options

| Option | Env Var | Description |
|---|---|---|
| `--url, -u` | `POUNDCAKE_URL` | PoundCake API URL |
| `--token, -t` | `POUNDCAKE_TOKEN` | PoundCake session token for authentication |
| `--username` | `POUNDCAKE_USERNAME` | Username for password-based auto-login |
| `--password` | `POUNDCAKE_PASSWORD` | Password for password-based auto-login |
| `--webhook-token` | `POUNDCAKE_WEBHOOK_TOKEN` | Bearer token for webhook POST endpoints |
| `--format, -f` | | Output format: `json`, `yaml`, `table` (default: `table`) |
| `--verbose, -v` | | Enable verbose output |

## Supported Command Areas

- `auth`: provider discovery, login/logout, current principal, RBAC bindings
- `overview`: reader/operator summary over health, observability, orders, communications, and suppressions
- `orders`: reporting/status views backed by `/orders/status`, `/orders/{id}/status`, and `/orders/{id}/timeline`
- `dishes`: reporting/status views backed by `/dishes/status` and `/dishes/{id}/ingredient-status`
- `communications`: redacted communication activity backed by `/communications/activity/status`
- `recipes`: recipe CRUD for operator-owned desired state
- `suppressions`: suppression management
- `comm-policy`: communication policy management
- `ingredients`: read-only ingredient template inspection
- `plugins`: plugin inventory, health, configuration, credentials, connection tests, and Kubernetes PrometheusRule inspection
- `scheduled-tasks`: typed scheduled task CRUD, status, and run-now controls
- `api`: low-level authenticated API wrapper for E2E and debugging
- `webhook`: post alerts via the webhook endpoint (POST /webhook)
- `activity`: view suppressed activity records
- `ready`: check API readiness (GET /ready)
- `health`: get full health status (GET /health)

The CLI no longer exposes the removed action-template or Prometheus rule
command families that targeted out-of-contract API routes.

## E2E-Friendly API Wrapper

Use `cakectl api` when a test needs an authenticated control-plane call without
managing cookies directly.

### Generic API Requests

```bash
cakectl --url http://localhost:8080 --username alice --password secret \
  api get /service-registry/ingredients

cakectl --url http://localhost:8080 --username alice --password secret \
  api post /recipes/ --body-json '{"name":"demo","enabled":true,"recipe_ingredients":[]}'
```

Read request body from a file:

```bash
cakectl --url http://localhost:8080 \
  api post /suppressions --body-file suppression-payload.json --format json
```

For public or route-level-token endpoints, bypass the CLI session explicitly:

```bash
cakectl --url http://localhost:8080 \
  api post /webhook \
  --no-session \
  --header 'Authorization: Bearer example-token' \
  --body-json '{"status":"firing","alerts":[]}'
```

Verb aliases are available as `api get`, `api post`, `api put`, `api patch`,
and `api delete`, each accepting the same options.

## Webhook Commands

Post Alertmanager-format payloads to trigger remediation orders.

```bash
# Post from a file
cakectl --url http://localhost:8080 --webhook-token my-token \
  webhook post -f webhook-payload.json --format json

# Post and output only the resulting order ID
order_id="$(cakectl --url http://localhost:8080 --webhook-token my-token \
  webhook post -f webhook-payload.json --order-id-only)"

# Post from stdin
cat alertmanager-alert.json | cakectl --url http://localhost:8080 --webhook-token my-token \
  webhook post --order-id-only

# Post from inline JSON
cakectl --url http://localhost:8080 --webhook-token my-token \
  webhook post --body-json '{"status":"firing","alerts":[]}' --format json
```

## Plugin Commands

### Inventory and Health

```bash
cakectl --url http://localhost:8080 plugins list
cakectl --url http://localhost:8080 plugins show k8s
cakectl --url http://localhost:8080 plugins health k8s
```

### Configuration and Credentials

```bash
# Show non-secret configuration
cakectl --url http://localhost:8080 plugins config show alertmanager

# Update via file
cakectl --url http://localhost:8080 plugins config set alertmanager \
  --config-file plugin-config.json

# Write credential material
cakectl --url http://localhost:8080 plugins credentials set stackstorm \
  --credential-type stackstorm_api_key \
  --payload-json '{"api_key":"example"}'

# Run a control-plane connection test
cakectl --url http://localhost:8080 plugins test-connection bakery --credential-key-id default

# Inspect PrometheusRule CRDs through the Kubernetes plugin
cakectl --url http://localhost:8080 plugins k8s prometheus-rules --namespace monitoring
```

## Scheduled Task Commands

```bash
# List redacted status rows
cakectl --url http://localhost:8080 scheduled-tasks status --service-type stackstorm

# Create from inline fields
cakectl --url http://localhost:8080 scheduled-tasks create \
  --task-key stackstorm-health \
  --task-type plugin_health_check \
  --service-type stackstorm \
  --service-exec health_check

# Update one field
cakectl --url http://localhost:8080 scheduled-tasks update 7 --run-interval-seconds 600

# Request an immediate run for a plugin-manifest task
cakectl --url http://localhost:8080 scheduled-tasks run-now 7
```

## Activity Commands

```bash
# List activity suppressed by a specific suppression
cakectl --url http://localhost:8080 activity suppressed 42 --format json
```

## Health Commands

```bash
# Quick readiness check
cakectl --url http://localhost:8080 ready

# Full health status
cakectl --url http://localhost:8080 health
```

## Naming

`cakectl` is the recommended executable name because it reads naturally in
operator and E2E workflows and matches the control-plane role of the tool more
closely than `cakecli`. The existing `poundcake` script remains available as an
alias to the same implementation.
