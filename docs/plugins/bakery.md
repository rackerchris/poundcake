# Bakery Plugin

## Status

- Service type: `bakery`
- Tier: `supported`
- External service: remote Bakery deployment

## Purpose

`bakery` is the supported communication plugin for remote provider ticketing and
notifications. PoundCake owns order flow and plugin identity; Bakery owns
provider-native ticket and notification behavior.

## Requirements

- A reachable Bakery deployment.
- Non-secret adapter connection settings configured through the plugin
  configuration contract.
- Bakery monitor HMAC material configured through the plugin credentials
  contract.
- A `bakery_monitor_hmac` credential row with `credential_key_id=default`,
  created through the credential-manager bootstrap flow.

## Operator configuration

Production Bakery setup is documented in
[`REMOTE_BAKERY.md`](../REMOTE_BAKERY.md). Operators manage non-secret fields
such as URL, TLS verification, retry settings, plugin identity, and environment
metadata through `/api/v1/plugins/bakery/configuration`. Admins manage encrypted
monitor HMAC fields through `/api/v1/plugins/bakery/credentials`. Do not seed
Bakery credentials with direct SQL or Helm fixtures.

## Enabled behavior

- Communication operations: open/create, notify/comment, update, and close.
- Dish execution evidence: when Expediter provides completed dish evidence in
  the execution context, Bakery includes it in the outgoing communication
  `context.evidence` payload so tickets and comments carry the validation and
  inspection history gathered by earlier recipe steps.
- Scheduled plugin health checks.
- Bootstrap credential registration with remote Bakery.

## Payload contracts

Operation-level `payload_schema` validation is authoritative. Invalid payloads
are rejected before adapter execution.

Ticket creation operations (`open` and `create`) require:

- `title`
- `description`
- `source`
- `context`

The creation payload and its nested `context` object are fail-closed: only the
documented fields are accepted.

Ticket mutation operations (`notify`, `update`, and `close`) require a
resolvable ticket identifier. Recipes may provide it as top-level
`service_payload.ticket_id` or inside `service_payload.context` using one of:

- `ticket_id`
- `bakery_ticket_id`
- `bakery_comms_id`
- `communication_id`

Mutation payloads and their nested `context` object are also fail-closed. The
payload may include the documented ticket identifier fields, standard
communication fields, and no unsupported extras.

The adapter also resolves those same keys from execution context paths produced
by prior dish steps, including `context.ticket_id`, `context.bakery_ticket_id`,
`context.bakery_comms_id`, `context.communication_id`, and the same keys under
`context.dish.context_updates`.

The `incident_reconcile.reconcile` operation accepts only an optional integer
`limit`.

The `collect` operations (`monitor_diagnostics`, `cluster_inventory`, and
`ticket_context`) accept only these optional fields: integer `order_id`, string
`req_id`, string `bakery_ticket_id`, string `namespace`, and integer `limit`
from 1 through 200.

## Validation

Verify plugin state after deployment:

```bash
curl -fsS https://poundcake.example.com/api/v1/plugins
curl -fsS https://poundcake.example.com/api/v1/plugins/bakery/health
```
