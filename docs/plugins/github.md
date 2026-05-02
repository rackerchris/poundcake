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

## Operator configuration

The operator config supports GitHub API base URL, default repository, default
branch, and TLS verification. Public repository reads can run without a token,
but write operations require credentials.

## Enabled behavior

- `health_check`
- `repo_read` operations: `read_file`, `list_files`
- `repo_write` operations: `commit_files`, `create_pull_request`,
  `commit_and_pr`
