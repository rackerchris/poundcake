# Dummy Plugin

## Status

- Service type: `dummy`
- Tier: `supported`
- External service: none

## Purpose

`dummy` is the reference implementation for PoundCake plugin development. It
exercises manifest discovery, helper registration, bootstrap hooks, ingredient
templates, communication routes, scheduled health checks, dispatch, polling,
expected outcomes, and cancellation.

## Receipt contract

`dispatch` returns opaque `service_exec_id` values owned by Expediter. Receipt
strings use the format `dummy:<service_exec>:[<operation>:]<extra>` where
`<extra>` is always a UUID except for the `sleep_10` service-exec, where it
stores the `ready_at` epoch timestamp so `poll` can detect completion. The
timestamp is also included in the `result` envelope (`result["ready_at"]`) so
clients never need to inspect the receipt string to infer provider state.

`poll` is a read-only observation method.  It inspects the receipt string only
to determine which service-exec the call targets and to parse the `ready_at`
timestamp for `sleep_10`; it mutates no state and performs no writes.

## Requirements

- No external service is required.
- No plugin credential row is required.
- No operator configuration is required.

## Enabled behavior

- Registers the `dummy.echo` helper capability.
- Runs a scheduled plugin health check.
- Provides reference ingredients and recipes used by local development and
  contract tests.

## Validation

Run the plugin and contract test suites after changing the dummy plugin:

```bash
.venv/bin/python -m pytest tests/test_plugin_contract.py tests/test_plugin_bootstrap.py
```
