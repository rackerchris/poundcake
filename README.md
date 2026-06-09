# PoundCake

PoundCake is the monitoring and remediation control plane. It receives Alertmanager webhooks,
plans remediation work, runs StackStorm-backed workflows, and manages communication state through a
remote Bakery deployment.

## Architecture

- PoundCake API, workers, UI, and StackStorm stay in this repo.
- Bakery now lives in its own repo: [rackerlabs/bakery](https://github.com/rackerlabs/bakery).
- PoundCake talks to Bakery through the `bakery` service plugin, its
  adapter-owned configuration contract, and its monitor HMAC credential.

## Local Validation

```bash
./.venv/bin/pre-commit run --all-files
./.venv/bin/mypy api kitchen cli
helm lint ./helm
helm unittest ./helm --file 'tests/unittest/*_test.yaml'
./.venv/bin/pytest -m 'not integration' tests/ -v --cov=api --cov-report=xml
```

## CLI Auth

PoundCake CLI human auth is session-based. Operators authenticate with a
username/password or device flow, PoundCake returns a session token, and the
CLI stores that session for later API calls. This is distinct from internal
service HMAC auth and from external plugin credentials.

The preferred executable name is `cakectl`. The legacy `poundcake` entrypoint
still points at the same CLI.

Operator flow:

```bash
# Admin grants the user an operator role binding first.
cakectl auth bindings create --provider local --type user --principal-id 42 --role operator

# Operator runs a normal CLI command with username/password once.
cakectl --url http://localhost:8080 --username alice --password secret auth me

# Later commands reuse the stored PoundCake session automatically.
cakectl --url http://localhost:8080 recipes list
```

You can also pass an explicit session token with `--token` or
`POUNDCAKE_TOKEN`.

## Deployment

PoundCake now installs only PoundCake:

```bash
./install/install-poundcake-helm.sh
```

If you need communications, deploy Bakery separately from its standalone repo,
enable the `bakery` plugin, and configure the remote Bakery URL and monitor
HMAC credential through PoundCake's Plugins UI/API:

```yaml
config:
  enabledPlugins: dummy,k8s,git,github,prometheus,alertmanager,bakery,stackstorm,genestack_monitoring
```

The corresponding Bakery deployment and install flow live in
[rackerlabs/bakery](https://github.com/rackerlabs/bakery).

## Plugin and Task Operations

`cakectl` now exposes first-class typed surfaces for plugin contracts and
scheduled task controls:

```bash
cakectl --url http://localhost:8080 plugins list
cakectl --url http://localhost:8080 plugins show stackstorm
cakectl --url http://localhost:8080 plugins config show bakery
cakectl --url http://localhost:8080 plugins k8s prometheus-rules --namespace monitoring
cakectl --url http://localhost:8080 scheduled-tasks status --service-type stackstorm
```
