# Remote Bakery Plugin Deployment

## Summary

PoundCake uses Bakery as the `bakery` plugin for external communication work. In production, Bakery is deployed separately as its own Helm release, exposed at an HTTPS URL, and connected to PoundCake through the plugin configuration and credential contracts.

The remote Bakery integration follows the normal plugin methodology:

- the Bakery plugin manifest registers `service_type=bakery`
- bootstrap registers Bakery ingredient templates and communication routes
- Dishwasher registers the Bakery health scheduled task
- due health checks are injected as orders
- Cook, Expediter, Timer, and the Bakery plugin adapter execute and reconcile Bakery work

For the full PoundCake Helm deployment flow, see [OPERATOR.md](OPERATOR.md).

## Production Shape

The supported production shape is Helm based:

1. Deploy Bakery as its own Helm release.
2. Expose Bakery at a stable HTTPS URL.
3. Provision a Bakery monitor HMAC credential for the PoundCake plugin identity.
4. Enable the `bakery` service plugin in PoundCake.
5. Configure Bakery's non-secret adapter settings through `/api/v1/plugins/bakery/configuration`.
6. Configure Bakery's monitor HMAC material through `/api/v1/plugins/bakery/credentials`.
7. Verify the Bakery plugin health check and communication route through PoundCake.

Bakery itself owns provider credentials and provider-native ticket or communication behavior. PoundCake owns the plugin identity, runtime order flow, expected outcome evaluation, and persisted adapter credential state.

## Plugin Configuration

Enable Bakery in PoundCake by including `bakery` in `config.enabledPlugins`, then configure the remote HTTPS URL through the Plugins UI or plugin configuration API.

```yaml
config:
  enabledPlugins: dummy,k8s,git,github,prometheus,alertmanager,bakery,stackstorm,genestack_monitoring
```

Production deployments should save a `url` that uses HTTPS and keep `verify_ssl=true`. HTTP is accepted only for loopback or in-cluster service DNS endpoints.

## Monitor Credential

Admins configure monitor HMAC material through the adapter credentials contract:

```json
{
  "credential_type": "bakery_monitor_hmac",
  "credential_key_id": "default",
  "credential_payload": {
    "monitor_uuid": "<value issued by Bakery>",
    "monitor_id": "<stable PoundCake plugin identity>",
    "hmac_key_id": "<value issued by Bakery>",
    "hmac_secret": "<value issued by Bakery>"
  },
  "rotate_credential": true
}
```

Use a stable, explicit plugin id for each production PoundCake environment. Do not reuse the same plugin identity across independent clusters.

## Helm Upgrade

Apply the values with the standard PoundCake Helm deployment flow:

```bash
helm upgrade --install poundcake <poundcake-chart> \
  --namespace poundcake \
  --create-namespace \
  -f values-prod.yaml
```

Wait for rollout:

```bash
kubectl -n poundcake rollout status deploy/poundcake-api --timeout=300s
kubectl -n poundcake rollout status deploy/poundcake-ui --timeout=300s
```

Confirm the saved plugin configuration and credential presence through the Plugins UI or `/api/v1/plugins/bakery/configuration`.

## Plugin Bootstrap And Health

When PoundCake starts, the enabled Bakery plugin registers:

- a `bakery` plugin row in `service_plugins`
- a `bakery-health-check` ingredient
- a `bakery-comms` communication ingredient
- a `plugin-health-check:bakery` recipe
- a `plugin-health-check:bakery` scheduled task
- a default Bakery communication route for the active provider

Dishwasher injects the due Bakery health task as an order. That order follows the same workflow as every other PoundCake order: Cook creates the dish, Expediter calls the Bakery plugin adapter, Timer reconciles the result, and Cook finalizes the order.

Verify plugin state through the API:

```bash
curl -fsS https://poundcake.example.com/api/v1/plugins
curl -fsS https://poundcake.example.com/api/v1/plugins/bakery/health
curl -fsS https://poundcake.example.com/api/v1/scheduled-tasks
```

The Bakery plugin should move from `initializing` to `healthy` or `degraded` after the scheduled health order succeeds.

## Communication Workflow

Bakery communication work is modeled as a normal plugin execution:

- recipes or global communication policy select `service_type=bakery`
- `service_exec=communication`
- canonical operations/capabilities are `open`, `notify`, `update`, and `close`
- the Bakery plugin adapter maps those operations to Bakery-native actions
- Bakery returns a provider ticket or communication id in the execution result
- PoundCake stores the resulting context on `dish_ingredients`

Resolving-phase communication uses the same order and dish model as remediation work. If a recipe does not define local communication routes, PoundCake can inherit the global Bakery route.

## Troubleshooting

If the Bakery plugin does not become healthy:

- confirm `bakery` is included in `config.enabledPlugins`
- confirm the saved Bakery plugin `url` is HTTPS
- confirm the `bakery_monitor_hmac/default` credential exists
- check `poundcake-api` logs for Bakery monitor HMAC errors
- check `/api/v1/plugins/bakery/health` for `health_message` and `health_error_code`
- check the latest `plugin-health-check:bakery` scheduled task order timeline

If communication actions fail but health checks pass:

- confirm the Bakery communication route destination matches a provider configured in Bakery
- confirm Bakery provider credentials are valid in the Bakery deployment
- confirm the recipe or global communication policy uses the Bakery route
- inspect the order timeline for the failing `bakery-comms` `dish_ingredient`
