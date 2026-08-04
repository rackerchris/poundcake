# GitHub Plugin

## Status

- Service type: `github`
- Tier: `community`
- External service: GitHub API

## Purpose

`github` provides GitHub-native repository reads, commits, and pull request
operations.

## Requirements

- GitHub API access from PoundCake.
- A default repository and branch when recipes omit them.
- A token for private repositories, higher rate limits, or write operations.

## Credentials

The optional credential is:

- `credential_type=github_token`
- `credential_key_id=default`

Credential payloads must include `token`, `access_token`, or `api_key`.
Read-only public repository access can use the credential-manager
`allow_public_read` flag without a token, but write operations such as
`commit_and_pr` require one of those token fields.

## Operator configuration

The operator config supports GitHub API base URL, default repository, default
branch, and TLS verification. Public repository reads can run without a token,
but write operations require credentials.

## Enabled behavior

- `health_check`
- `repo_read` operations: `read_file`, `list_files`
- `repo_write` operations: `commit_files`, `create_pull_request`,
  `commit_and_pr`

## Payload contracts

Operation-level `payload_schema` validation is authoritative. Invalid payloads
are rejected before adapter execution.

- `read_file` requires `service_payload.path`.
- `list_files` accepts optional `path`, `ref`, `recursive`, and `repo`.
- `commit_files` requires `branch` and a non-empty `files` object.
- `create_pull_request` requires `branch` and `title`.
- `commit_and_pr` requires `branch`, a non-empty `files` object, and `title`.

Optional supported fields include `repo`, `ref`, `base_branch`, `message`, and
pull request body text where the operation supports them.
