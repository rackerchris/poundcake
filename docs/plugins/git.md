# Git Plugin

## Status

- Service type: `git`
- Tier: `community`
- External service: Git repository endpoint

## Purpose

`git` provides portable repository read and write operations for Git-backed
automation.

## Requirements

- A reachable repository URL for configured operations.
- A default branch for operations that omit a branch.
- Credentials for private repository reads or all write operations.

## Credentials

The optional credential is:

- `credential_type=git_repository_auth`
- `credential_key_id=default`

Credential payloads must include a token-style secret (`token`, `access_token`,
or `password`) or `ssh_key_path`.

## Operator configuration

The operator config supports repository URL, default branch, provider label,
commit user name, and commit user email.

## Enabled behavior

- `health_check`
- `repo_read` operations: `read_file`, `list_files`
- `repo_write` operations: `commit_files`, `create_pull_request`,
  `commit_and_pr`

## Payload contracts

Operation-level `payload_schema` validation is authoritative. Invalid payloads
are rejected before adapter execution.

- `read_file` requires `service_payload.path`.
- `list_files` accepts optional `path` and `recursive`.
- `commit_files` requires `branch` and a non-empty `files` object.
- `create_pull_request` requires `branch` and `title`.
- `commit_and_pr` requires `branch`, a non-empty `files` object, and `title`.

Optional supported fields include repository override fields, refs or base
branches, commit messages, and pull request body text where the adapter supports
them.
