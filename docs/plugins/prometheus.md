# Prometheus Plugin

## Status

- Service type: `prometheus`
- Tier: `community`
- External service: Prometheus HTTP API

## Purpose

`prometheus` owns Prometheus API inspection, reload operations, monitoring rule
helper capabilities, and the PrometheusRule content lifecycle.

## Requirements

- A reachable Prometheus HTTP API endpoint.
- TLS verification settings appropriate for the endpoint.
- Optional HTTP credentials if Prometheus requires authentication.
- Monitoring rule ownership wiring when PrometheusRule content is synchronized
  into a cluster.

## Credentials

The optional credential is:

- `credential_type=prometheus_http_auth`
- `credential_key_id=default`

Credential payloads must include either a bearer-style token
(`bearer_token`, `token`, `api_key`, or `access_token`) or `username` plus
`password`.

## Operator configuration

The adapter supports HTTP operator config for URL, TLS verification, timeout,
and auth mode. Devstack installs Prometheus into the `monitoring` namespace and
points PoundCake at that in-cluster service.

## Enabled behavior

- `health_check`
- `inspect` operations: `list_rules`, `list_rule_groups`, `list_metrics`,
  `list_labels`, `list_label_values`, and `alert_evidence`
- `reload_config`
- Helper capabilities: `alert_rules.parse`, `alert_rules.index`, and
  `alert_rules.render`

`alert_evidence` builds a bounded `ALERTS{alertname=..., ...}` selector from
the alert name and labels, then runs instant and recent range queries for that
selector. It rejects raw PromQL in `service_payload.query` so downstream alert
recipes cannot widen the evidence scope.

## Payload contracts

Operation-level `payload_schema` validation is authoritative. Invalid payloads
are rejected before adapter execution.

- `alert_evidence` requires `alert_name`, accepts optional `labels`,
  `lookback_seconds`, and `step_seconds`, and rejects raw `query`.
- `list_label_values` requires `label_name` and accepts optional `metric`.
- `list_labels` accepts optional `metric`.
- `list_rules`, `list_rule_groups`, and `list_metrics` accept no payload
  fields.
- `health_check`, `reload_config`, and `watchdog` accept no payload fields.

The adapter keeps raw-query rejection as defense in depth, but the public
contract is the operation schema.

## Dependency note

Prometheus Operator CRDs are not required for Prometheus HTTP inspection. They
are required only when the monitoring rule flow synchronizes PrometheusRule
content into Kubernetes.
