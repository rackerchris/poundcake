# API

FastAPI service exposing PoundCake endpoints.

## Key Endpoints

- `/api/v1/webhook` - Alertmanager intake
- `/api/v1/orders` - Orders list, detail, create, update, status, and timeline reads
- `/api/v1/recipes` - Recipes CRUD
- `/api/v1/service-registry/ingredients` - Read-only immutable plugin-provided ingredient templates
- `/api/v1/internal/service-registry/ingredients/bulk` - Internal Dishwasher-owned ingredient registration
- `/api/v1/dishes` - Dishes query and updates
- `/api/v1/dishes/{dish_id}/ingredient-status` - Redacted dish ingredient status
- `/api/v1/dishes/{dish_id}/ingredients` - Internal dish ingredient runtime records
- `/api/v1/dishes/{dish_id}/ingredient-history` - Admin-readable full dish ingredient execution history
- `/api/v1/orders/{order_id}/execution-history` - Admin-readable order execution history across dishes
- `/api/v1/cook/orders/{order_id}` - Expand an order into a dish
- `/api/v1/cook/dishes/{dish_id}/advance` - Advance ready dish ingredients
- `/api/v1/expediter/execute/{dish_ingredient_id}` - Execute runner-owned plugin workload
- `/api/v1/expediter/status/{service_type}/{service_exec_id}` - Poll plugin execution status
- `/api/v1/expediter/cancel/{service_type}/{service_exec_id}` - Cancel plugin execution when supported

## Execution

Cook dispatches ready `dish_ingredients` through the internal Expediter gateway. The first dispatch path is internal to Cook and records an `expediter-runner` receipt; the public Expediter routes execute runner-owned work, poll provider receipts, and cancel provider work when supported. Dishwasher registers plugin capability templates through `/api/v1/internal/service-registry/ingredients/bulk`, while humans read immutable definitions from `/api/v1/service-registry/ingredients`; recipe ingredients select an ingredient plus any operation/payload overrides. Timer reconciles in-flight work through the `dish-ingredients` claim/release/reconcile routes, reads dish runtime records, advances dishes, and uses Expediter status/cancel routes for provider receipts.

## Environment

```bash
DATABASE_URL=mysql+pymysql://user:pass@poundcake-mariadb:3306/poundcake
POUNDCAKE_AUTH_DEV_USERNAME=admin
POUNDCAKE_AUTH_DEV_PASSWORD=change-me
POUNDCAKE_PLUGIN_CREDENTIAL_ENCRYPTION_KEY=plugin-credential-key
```

When auth is enabled, most API endpoints require authentication. Public paths are `/`, `/metrics`, `/livez`, `/readyz`, debug docs/OpenAPI paths when enabled, `/api/v1/webhook`, and auth login/OIDC/device-flow paths. The `/api/v1/live`, `/api/v1/ready`, `/api/v1/health`, and `/api/v1/health/status` routes remain under the API auth dependency. Internal services must send PoundCake HMAC-signed requests that resolve to registered and enabled `service_plugins` rows; raw `X-Auth-Token` and Bearer service-token requests are not accepted as internal service identities.
