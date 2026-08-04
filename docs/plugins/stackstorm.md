# StackStorm Plugin

## Status

- Service type: `stackstorm`
- Tier: `community`
- External service: StackStorm

## Purpose

`stackstorm` executes StackStorm actions and workflows from PoundCake orders and
syncs PoundCake-owned StackStorm content.

In the provider-neutral capability model, StackStorm is intended for workflow
orchestration and pack-owned automations, not as a duplicate home for every
native single-step Kubernetes mutation that PoundCake already exposes through
the `k8s` plugin.

## Requirements

- A reachable StackStorm API endpoint.
- StackStorm content installed or synced for PoundCake-owned actions.
- A StackStorm API key or auth token stored through the credential-manager
  boundary.

## Credentials

The required credential is:

- `credential_type=stackstorm_api_key`
- `credential_key_id=default`

Credential payloads may include `api_key`, `st2_api_key`, or `auth_token`.

## Operator configuration

The operator config requires:

- `url`: StackStorm API URL
- `verify_ssl`: whether PoundCake verifies the StackStorm API certificate

## Enabled behavior

- `health_check`
- `action_execution`
- `workflow_execution`
- `content_sync`

`content_sync` runs as a normal service-execution ingredient/order. The plugin
router does not expose a direct StackStorm content-sync action. The sync
implementation lives in `api/plugins/stackstorm/content_sync.py`, matching the
standard adapter layout for plugin-owned recurring import/sync work.

## Payload contracts

Operation-level `payload_schema` validation is authoritative. Invalid payloads
are rejected before adapter execution.

- `action_execution.execute_action` requires `action_ref` and accepts optional
  object `parameters`.
- `workflow_execution.execute_workflow` requires `workflow_ref` and accepts
  optional object `inputs`.
- `content_sync.sync_content` and `health_check` accept no payload fields.

## Devstack

The devstack installs StackStorm from
`https://github.com/rackerlabs/poundcake-stackstorm` into the `stackstorm`
namespace when `INSTALL_STACKSTORM=true`.
