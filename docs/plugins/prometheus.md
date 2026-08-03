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

`alert_evidence` evaluates a supplied alert expression as both an instant query
and a recent range query, returning the current result, trend result, labels,
lookback, and step metadata for downstream alert recipes.

## Dependency note

Prometheus Operator CRDs are not required for Prometheus HTTP inspection. They
are required only when the monitoring rule flow synchronizes PrometheusRule
content into Kubernetes.
