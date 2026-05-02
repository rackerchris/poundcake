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

## Validation

Verify plugin state after deployment:

```bash
curl -fsS https://poundcake.example.com/api/v1/plugins
curl -fsS https://poundcake.example.com/api/v1/plugins/bakery/health
```
