# Alertmanager Plugin

## Status

- Service type: `alertmanager`
- Tier: `community`
- External service: Alertmanager HTTP API

## Purpose

`alertmanager` owns Alertmanager API inspection and silence synchronization.
Alertmanager remains the source of truth for silencing, inhibition, and mute
evidence.

## Requirements

- A reachable Alertmanager API v2 endpoint.
- TLS verification settings appropriate for the endpoint.
- Optional HTTP credentials if Alertmanager requires authentication.

## Credentials

The optional credential is:

- `credential_type=alertmanager_http_auth`
- `credential_key_id=default`

Credential payloads must include either a bearer-style token
(`bearer_token`, `token`, `api_key`, or `access_token`) or `username` plus
`password`.

## Operator configuration

The adapter supports URL, TLS verification, and timeout configuration. Devstack
installs Alertmanager into the `monitoring` namespace and points PoundCake at
that in-cluster service.

## Enabled behavior

- `health_check`
- `sync_silences`
- `inspect` operations for alert/group inspection and inhibition lookup

## Payload contracts

Operation-level `payload_schema` validation is authoritative. Invalid payloads
are rejected before adapter execution.

- `inspect.list_alerts`, `list_groups`, and `verify_firing` accept bounded
  filter fields only.
- `inspect.find_inhibited` requires `fingerprint`.
- `inspect.find_inhibited_by_source` requires `source_ref`.
- `sync_silences.create`, `ensure`, and `sync` require a non-empty `matchers`
  list plus `name`, `starts_at`, and `ends_at`.
- `sync_silences.expire` and `delete` require `source_ref`.
- `health_check` accepts no payload fields.
