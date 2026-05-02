# PoundCake Database Overview

## Summary

PoundCake stores application state in MariaDB/MySQL and manages schema changes with Alembic. Database setup and migrations are part of the normal Helm startup path: startup waits for MariaDB, applies Alembic migrations, then runs split post-migration bootstrap stages with separate database principals.

This deserves a standalone doc because database mode, migration authoring, Helm startup behavior, and local troubleshooting cut across both operator and developer workflows.

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

## Migration Startup Flow

The Helm startup flow now splits migration and bootstrap authority into distinct jobs:

```bash
python3 -m alembic upgrade head
python3 -m api.scripts.bootstrap_plugin_registry
python3 -m api.scripts.bootstrap_service_identities
python3 -m api.scripts.bootstrap_adapter_credentials
```

This means startup is still release-gating, but the work is privilege-separated:

- migration runs with migrator DB authority only
- plugin registry bootstrap runs without credential encryption keys
- service identity bootstrap runs only with the service-identity encryption key
- adapter credential bootstrap runs only with the plugin credential encryption key

Dishwasher remains the only authority for manifest-driven sync into `ingredients`, `recipes`, `recipe_ingredients`, `scheduled_tasks`, and communication-route policy state.

The API and workers use the same database URL shape as the bootstrap job:

```text
mysql+pymysql://$(DB_API_USER):$(DB_API_PASSWORD)@<database-host>:3306/$(DB_NAME)
```

## Alembic Layout

PoundCake currently has two Alembic trees:

- `alembic/`: root development Alembic tree used by local scripts and direct developer commands.
- `helm/files/poundcake-alembic/`: Helm-shipped Alembic tree mounted into production startup pods.

When adding or changing a migration, keep both trees synchronized. The production chart uses the Helm-shipped copy, so a migration that exists only under the root `alembic/` tree will not run in a Helm deployment.

Current migration chain starts with the full schema baseline:

- `2026_02_03_1600_initial_schema`
- `2026_05_01_1200_service_plugin_short_id`
- `2026_05_01_1230_service_plugin_log_key`
- `2026_05_02_0900_internal_service_plugins`
- later revisions continue from the current head

The old alpha guidance was to fold every change into a single baseline. PoundCake now carries chained Alembic revisions, so new schema changes should be added as forward migrations unless a deliberate baseline reset is planned.

## Developer Commands

Use the local migration wrapper for direct development checks:

```bash
python api/migrate.py current
python api/migrate.py history
python api/migrate.py upgrade
```

The wrapper resolves the synchronous database URL from PoundCake settings and runs Alembic against the configured database.

For local container validation, start the devstack before running migration checks:

```bash
bash docker/devstack/create.sh
python api/migrate.py current
```

## Migration Authoring Rules

When changing schema:

- update SQLAlchemy models and schemas together
- add a forward Alembic revision from the current head
- keep migrations idempotent where practical by checking existing columns or tables before adding them
- include data backfill or default handling when adding non-null columns
- keep root and Helm-shipped Alembic trees synchronized
- add or update tests that exercise the new schema through the API or plugin bootstrap path

Avoid storing provider secrets in migrations. Secrets belong in Kubernetes secrets or encrypted adapter credential rows.

## Operator Verification

After Helm install or upgrade:

```bash
kubectl -n poundcake rollout status deploy/poundcake-api --timeout=300s
kubectl -n poundcake logs job/poundcake-bootstrap-plugin-registry
kubectl -n poundcake logs job/poundcake-bootstrap-service-identity
kubectl -n poundcake logs job/poundcake-bootstrap-adapter-credentials
kubectl -n poundcake exec deploy/poundcake-api -- printenv | grep POUNDCAKE_DATABASE_URL
```

Check API health:

```bash
curl -fsS https://poundcake.example.com/api/v1/health
```

The health response should report the database component as healthy after migrations complete and the API starts.

For database mode rendering changes, run:

```bash
helm lint ./helm
helm unittest ./helm --file 'tests/unittest/poundcake_database_mode_test.yaml'
```
