# Kitchen Services

This directory contains the background workers that drive execution.

## Services

- **prep-chef**: Claims ready orders and hands them to Cook.
- **cook**: Expands orders into dishes, hydrates dish ingredients from recipe ingredients, and advances ready steps.
- **expediter-runner**: Claims runner-dispatched dish ingredients and performs workload execution through Expediter's internal execute boundary.
- **timer**: Crawls in-flight dish ingredients, observes existing provider execution state only through Expediter, and reconciles outcomes.
- **dishwasher**: Syncs plugin manifests into immutable ingredient templates, recipes, and scheduled tasks.

## Flow (High Level)

1. Alertmanager posts `/api/v1/webhook`.
2. `prep-chef` polls `/api/v1/orders?processing_status=new` and calls `/api/v1/cook/orders/{order_id}`.
3. Cook creates the dish and snapshots hydrated service fields into `dish_ingredients`.
4. Cook dispatches ready dish ingredients through the internal Expediter gateway; Expediter records an expediter-runner receipt.
5. `expediter-runner` claims runner receipts, asks Expediter to execute the row, and records either a terminal result or an external provider receipt.
6. Expediter is the only worker-facing gateway that calls plugin adapters for execution, status, and cancellation.
7. `timer` reads `/api/v1/dish-ingredients/in-flight`, polls `/api/v1/expediter/status/{service_type}/{service_exec_id}` as a read-only status check, reconciles outcomes, and calls `/api/v1/cook/dishes/{dish_id}/advance`.

Timer never imports plugin adapters or provider clients directly, never dispatches plugin work, and never expects status polling to perform work. Cancellation is a lifecycle reconciliation exception, but it still goes through `/api/v1/expediter/cancel/{service_type}/{service_exec_id}` so Expediter remains the provider boundary.

## Environment Variables

- `POUNDCAKE_API_URL` - PoundCake API base URL (default: `http://poundcake:8080`; workers append `/api/v1`).
- `POLL_INTERVAL` - Poll interval in seconds.

## Debug Tips

- List dishes: `curl http://localhost:8000/api/v1/dishes`
