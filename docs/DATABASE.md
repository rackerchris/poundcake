# PoundCake Database Overview

## Summary

PoundCake stores application state in MariaDB/MySQL and uses split startup jobs
with separate database principals for database readiness, persona/user
reconciliation, plugin registration, service-identity provisioning, and
adapter-credential provisioning. The Helm startup path no longer runs a
separate migration job.

This deserves a standalone doc because database mode, startup-job authoring,
Helm startup behavior, and local troubleshooting cut across both operator and
developer workflows.

## Runtime Database Model

PoundCake uses the relational database for durable control-plane state:

- `orders`: singular unit of work and order lifecycle state
- `recipes`, `ingredients`, `recipe_ingredients`: workflow templates, immutable plugin ingredient templates, and mutable recipe steps
- `dishes`, `dish_ingredients`: per-order execution instances and runtime rows
- `service_plugins`: plugin metadata, health, and runtime state
- `adapter_credentials`: Credential Manager-owned encrypted adapter/provider credentials
- `service_identity_credentials`: Service Identity Manager-owned internal HMAC credentials
- `scheduled_tasks`: durable recurring plugin and control-plane work

StackStorm keeps its own backing stores. Bakery is deployed separately and owns its own persistence.

## Helm Database Modes

PoundCake database behavior is configured with `database.*` Helm values.

```yaml
database:
  mode: embedded
```

Supported modes:

- `embedded`: deploys an in-chart `poundcake-mariadb` Deployment, Service, and PVC.
- `shared_operator`: uses an existing MariaDB operator server and points PoundCake at that service.

For shared operator mode:

```yaml
database:
  mode: shared_operator
  sharedOperator:
    serverName: shared-pc-mariadb
    namespace: ""
    provisionResources: true
```

When `database.sharedOperator.provisionResources=true`, the chart renders MariaDB operator `Database`, `User`, and `Grant` resources for PoundCake.

The chart builds `POUNDCAKE_DATABASE_URL` from the workload's database persona, `DB_NAME`, and the resolved database host. `database.url` is disabled for direct plaintext injection; use chart-managed secrets and values instead.

PoundCake uses role-profile database users instead of one broad runtime user:

- `poundcake_migrator`: schema migration only.
- `poundcake_plugin_registry`: startup `service_plugins` registration and metadata-safe bootstrap hooks only.
- `poundcake_service_identity_manager`: startup internal HMAC credential creation/update only.
- `poundcake_api`: API-owned application reads and writes.
- `poundcake_auth_verifier`: internal HMAC verification and nonce writes.
- `poundcake_credential_manager`: adapter credential lifecycle writes and credential status updates.
- `poundcake_plugin_operation`: adapter-safe control-plane writes through `api.services.plugin_operations`.
- per-worker reader users such as `poundcake_prep_chef_reader` and `poundcake_dishwasher_reader`: read-only service-plugin and service-identity lookup.
- `poundcake_readonly`: optional diagnostics.

Application code should use the policy-aware database helper for protected
plugin and credential operations. RBAC chooses the allowed
operation/capability; MariaDB grants are the hard backstop if a pod tries to
bypass the helper with raw SQL.

For plugin/runtime boundaries, the split is intentional:

- `api.services.plugin_operations`: protected control-plane state such as
  `service_plugins`, recipes, ingredients, scheduled tasks, and plugin-owned
  dish metadata.
- `api.services.credential_manager`: adapter/provider credential reads, writes,
  rotation, and credential status.
- `api.services.adapter_runtime`: short-lived adapter/bootstrap teardown helpers
  that release service-layer database resources without direct
  `api.core.database` imports from adapter-associated code.

Code under `api/plugins/**`, including `adapter.py`, `content_sync.py`, and
adapter-associated helper scripts, must not import `api.core.database` or open
raw sessions directly. Dedicated startup bootstrap jobs under `api.scripts.*`
are the exception because they run under distinct bootstrap database
principals, not the adapter runtime boundary.

## Startup Database Flow

The Helm startup flow splits database and startup authority into distinct jobs:

```bash
wait for MariaDB service readiness
reconcile MariaDB users and grants
python3 -m api.scripts.bootstrap_plugin_registry
python3 -m api.scripts.bootstrap_service_identities
python3 -m api.scripts.bootstrap_adapter_credentials
```

This means startup is still release-gating, but the work is privilege-separated:

- database readiness and user/grant reconciliation run before application bootstrap
- plugin registry bootstrap runs without credential encryption keys
- service identity bootstrap runs only with the service-identity encryption key
- adapter credential bootstrap runs only with the plugin credential encryption key

Dishwasher remains the only authority for manifest-driven sync into `ingredients`, `recipes`, `recipe_ingredients`, `scheduled_tasks`, and communication-route policy state.

The API and workers use the same database URL shape as the startup jobs:

```text
mysql+pymysql://$(DB_API_USER):$(DB_API_PASSWORD)@<database-host>:3306/$(DB_NAME)
```

## Developer Change Notes

When changing database-facing startup behavior:

- update SQLAlchemy models and schemas together
- update MariaDB persona/grant assets when database authority changes:
  `docker/mariadb-init/01-create-databases.sh`
  `helm/files/mariadb-init/01-create-databases.sh`
- update startup-job expectations when bootstrap sequencing changes:
  `helm/templates/poundcake-startup-jobs.yaml`
- add or update tests that exercise the affected behavior through the API,
  startup-job rendering, or plugin registration path

Avoid storing provider secrets in startup assets. Secrets belong in Kubernetes
Secrets or encrypted adapter credential rows.

## Operator Verification

After Helm install or upgrade:

```bash
kubectl -n poundcake rollout status deploy/poundcake-api --timeout=300s
kubectl -n poundcake logs job/poundcake-mariadb-users
kubectl -n poundcake logs job/poundcake-bootstrap-plugin-registry
kubectl -n poundcake logs job/poundcake-bootstrap-service-identity
kubectl -n poundcake logs job/poundcake-bootstrap-adapter-credentials
kubectl -n poundcake exec deploy/poundcake-api -- printenv | grep POUNDCAKE_DATABASE_URL
```

Check API health:

```bash
curl -fsS https://poundcake.example.com/api/v1/health
```

The health response should report the database component as healthy after the
startup jobs complete and the API starts.

For database mode rendering changes, run:

```bash
helm lint ./helm
helm unittest ./helm --file 'tests/unittest/poundcake_database_mode_test.yaml'
```
