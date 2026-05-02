# PoundCake Developer Guide

## Summary

This guide is for developers changing PoundCake itself. It focuses on the local Python environment, plugin and plugin adapter development, and validation against local containers. Production Helm operations live in [OPERATOR.md](OPERATOR.md).

For runtime architecture, see [ARCHITECTURE.md](ARCHITECTURE.md). For the service plugin contract, see [SERVICE_PLUGIN_CONTRACT.md](SERVICE_PLUGIN_CONTRACT.md). For per-plugin requirements and support tiers, see [plugins/README.md](plugins/README.md). For schema and migration work, see [DATABASE.md](DATABASE.md). For auth and internal RBAC, see [AUTH_RBAC.md](AUTH_RBAC.md).

## Development Prerequisites

Recommended tools:

- Python 3.11+
- `git`
- Colima for the local Docker-compatible container layer
- Docker Compose through Colima's Docker runtime, either as `docker compose` or `docker-compose`
- `kind`, `kubectl`, and `helm` for local Kubernetes and chart validation
- `curl`

Typical Python setup:

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip setuptools wheel
pip install -e ".[dev]"
pre-commit install
```

Common checks:

```bash
pre-commit run -a
.venv/bin/python -m pytest tests
```

## Plugin Development Standard

The `dummy` plugin is the standard reference for plugin and plugin adapter development. New plugins should follow its shape before adding provider-specific complexity.

Built-in plugin requirements are documented under [plugins/README.md](plugins/README.md).

Reference files:

- `api/plugins/dummy/plugin.py`: plugin manifest
- `api/plugins/dummy/adapter.py`: plugin adapter implementation
- `api/plugins/dummy/templates.py`: immutable ingredient templates, recipes, communication routes, and scheduled tasks
- `api/plugins/dummy/helper.py`: optional helper registration pattern
- `api/plugins/dummy/bootstrap.py`: optional bootstrap hook pattern

A plugin should provide:

- a `get_plugin()` function that returns `ServicePlugin`
- a stable lowercase `service_type`
- an `ExecutionAdapter` with `validate`, `dispatch`, `poll`, and `health_check`
- immutable `ingredient_templates` with JSON Schema payload validation
- a `plugin_health_check` scheduled task
- a health-check recipe named `plugin-health-check:<service_type>`
- canonical `ExecutionResult.status` values
- optional communication route templates when the plugin owns communication work
- optional helper and bootstrap hooks only when cross-plugin or install-time setup requires them

Use the dummy plugin to prove these contract behaviors:

- positive provider result maps to a successful runtime row
- negative provider result maps to a failed runtime row
- expected negative outcome can still satisfy a PoundCake step
- long-running work stays `running` until Timer polls it terminal
- cancellation maps to canonical `canceled`
- communication operations/capabilities use `open`, `notify`, `update`, and `close`
- plugin health is exercised through scheduled-task orders

## Adapter Rules

Plugin adapters are provider translators. They should not bypass Cook, Expediter, Timer, or the normal order workflow.

Adapter implementation requirements:

- Normalize provider states to `pending`, `dispatched`, `running`, `succeeded`, `failed`, `errored`, `timeout`, or `canceled`.
- Return stable `service_exec_id` values for asynchronous provider work.
- Keep provider-native response data in `result` and `raw` without leaking credentials.
- Use `retryable` only for failures that the orchestration layer can safely retry.
- Implement `cancel` when the provider supports cancellation; otherwise inherit the default unsupported response.
- Keep `health_check` lightweight and safe for scheduled execution.

Template requirements:

- Treat `ingredients` as immutable templates.
- Put provider-specific operation/capability metadata in `service_exec_parameters`.
- Prefer adding operations to an existing ingredient when the same adapter surface and payload family fit the work; create a new ingredient only for a distinct contract or lifecycle.
- Validate fillable payload fields with JSON Schema.
- Prefer explicit `service_exec_expected_outcome_default`.
- Mark communication work `is_blocking=false` unless it must block order completion.

## Local Container Devstack

The local container devstack is for PoundCake development and plugin contract validation. It starts a dummy-only PoundCake stack with MariaDB, API, UI, Prep Chef, Dishwasher, and Timer.

Colima is the default local container layer. The dev scripts use Docker Compose through Colima's Docker runtime, first as `docker compose` and then as the standalone `docker-compose` binary.

Start the stack:

```bash
bash docker/devstack/create.sh
```

Defaults:

- API: `http://127.0.0.1:8000/api/v1`
- UI: `http://127.0.0.1:8080`

Override compose when needed:

```bash
COMPOSE="docker compose" bash docker/devstack/create.sh
COMPOSE="docker-compose" bash docker/devstack/create.sh
```

The script waits for `/live`, `/ready`, and the UI, then prints dummy plugin health, service registry ingredients, and recipes.

Run dummy e2e scenarios against the running stack:

```bash
bash tests/run_e2e.sh
```

Run one scenario:

```bash
bash tests/run_e2e.sh --single positive
bash tests/run_e2e.sh --single expected-negative
bash tests/run_e2e.sh --single cancel-parallel
```

Tear down the stack:

```bash
bash docker/devstack/destroy.sh
```

Keep the database volume for follow-up debugging:

```bash
REMOVE_VOLUMES=false bash docker/devstack/destroy.sh
```

## Local Kind Cluster

Use Kind on top of Colima when validating the PoundCake Helm chart against a real local Kubernetes API. Start Colima with the Docker runtime, then create the Kind cluster:

```bash
brew install colima docker docker-compose kubectl helm

colima start --cpu 6 --memory 12 --disk 80 --runtime docker
docker context use colima
docker info
docker compose version || docker-compose version

unset KIND_EXPERIMENTAL_PROVIDER

helm/devstack/create.sh
kubectl cluster-info --context kind-poundcake
kubectl config use-context kind-poundcake
kubectl get nodes
```

If Kind fails while pulling `kindest/node` with `docker-credential-osxkeychain` missing, reset the
Docker client config left behind by a previous desktop runtime and retry:

```bash
cp ~/.docker/config.json ~/.docker/config.json.pre-colima 2>/dev/null || true
printf '{\n  "auths": {},\n  "currentContext": "colima"\n}\n' > ~/.docker/config.json

docker pull kindest/node:v1.35.0
DELETE_NAMESPACE=false helm/devstack/destroy.sh
INSTALL_CHART=false helm/devstack/create.sh
```

Run the chart checks before installing:

```bash
helm lint ./helm
helm unittest ./helm --file 'tests/unittest/*_test.yaml'
```

Install PoundCake into the local Kind cluster or refresh the release:

```bash
VALUES_FILE=helm/devstack/values/poundcake-plugins-kind.yaml helm/devstack/create.sh
```

Set `INSTALL_CHART=false` when you only want the kind cluster.

Tear down the Kind cluster when finished:

```bash
helm/devstack/destroy.sh
```

Stop Colima when you are done with local containers:

```bash
colima stop
```

## Test Expectations

For plugin contract changes, run:

```bash
.venv/bin/python -m pytest tests
bash docker/devstack/create.sh
bash tests/run_e2e.sh
bash docker/devstack/destroy.sh
```

If your code change affects the Helm chart, rendered environment variables, or production deployment behavior, also run the Helm checks listed in [OPERATOR.md](OPERATOR.md).

If a change affects a specific plugin, add or update tests around that plugin's manifest, adapter normalization, template validation, and scheduled health behavior.
