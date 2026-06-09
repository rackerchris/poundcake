# Helm Devstack

These helpers create a local kind cluster for PoundCake Helm chart work. The
default cluster has one control-plane node and three worker nodes.

PoundCake consumes Prometheus, Alertmanager, and StackStorm through plugin
configuration URLs; it does not render or own the external backend pods.

The Bakery adapter is enabled in devstack as a remote-only integration. Devstack
does not install a local Bakery release or seed Bakery credentials; configure
the remote endpoint and HMAC material through the normal adapter configuration
and credential-manager flow.

For local kind nodes, `create.sh` applies fsnotify-friendly sysctls by default
so bootstrap workloads that watch many files, such as StackStorm bootstrap, do
not fail with `too many open files`:

- `fs.inotify.max_user_watches=1048576`
- `fs.inotify.max_user_instances=8192`
- `fs.file-max=2097152`

The devstack bootstrap also installs `metrics-server` into `kube-system` by
default so `kubectl top pod` and `kubectl top node` work for local resource
evidence gathering. Use `--skip-metrics-server` if you need a leaner cluster or
want to validate behavior without resource metrics.

## Quick Start

```bash
# Full teardown
helm/devstack/destroy.sh DELETE_CLUSTER=true

# Build, deploy, and start everything
helm/devstack/create.sh --build-images --all
```

## Create

```bash
helm/devstack/create.sh --build-images --all
```

The script accepts flags for each phase so you can run steps independently.

### Flags

```
Cluster & image options:
  --build-images            Build poundcake:local and poundcake-ui:local
  --load-images             Load images into the kind cluster
  --create-cluster          Create the kind cluster (default when missing)
  --skip-create-cluster     Skip cluster creation
  --app-image IMAGE         Override API image tag (default: poundcake:local)
  --ui-image IMAGE          Override UI image tag (default: poundcake-ui:local)

Install options:
  --all                     Install poundcake + monitoring + stackstorm
  --poundcake               Install only the PoundCake Helm chart
  --monitoring              Install Prometheus/Alertmanager stack
  --stackstorm              Install StackStorm (includes adapter config)
  --skip-stackstorm-config  Skip StackStorm adapter configuration
  --skip-github-public-read-config  Skip GitHub allow_public_read devstack configuration
  --require-github-write   Fail bootstrap unless a GitHub write token is configured
  --skip-metrics-server     Skip metrics-server install for kubectl top support
  --no-port-forward         Skip local port-forwards

Lifecycle options:
  --skip-node-sysctls       Skip fsnotify sysctl tuning
  --no-wait                 Skip helm --wait
  --timeout DURATION        Wait timeout (default: 15m)
  --values FILE             Override values file
```

### Defaults

When `--all` is used, the following are deployed:

- kind cluster: `poundcake`
- Kubernetes context: `kind-poundcake`
- namespace: `poundcake`
- Helm release: `poundcake`
- chart: `helm`
- values: `helm/devstack/values/poundcake-plugins-kind.yaml`
- monitoring namespace: `monitoring`
- Prometheus Operator CRDs release: `poundcake-prometheus-operator-crds`
- Prometheus Operator stack release: `poundcake-prometheus`
- StackStorm namespace: `stackstorm`
- StackStorm release: `stackstorm`
- StackStorm chart source: `https://github.com/rackerlabs/poundcake-stackstorm.git`
- StackStorm client pod: enabled by default
- StackStorm web pod: disabled by default
- UI port-forward: `http://127.0.0.1:8080`
- Prometheus port-forward: `http://127.0.0.1:9090`
- Alertmanager port-forward: `http://127.0.0.1:9093`

Adapter plugin enablement and local credential expectations are documented in
`helm/devstack/ADAPTERS.md`. The broader built-in plugin requirements live in
`docs/plugins/README.md`.

By default, the devstack bootstrap also configures the `github` adapter with
`allow_public_read=true` through the credential-manager boundary so the
`genestack_monitoring` content-sync task can read the public Genestack catalog
without a token. Use `--skip-github-public-read-config` to validate the
locked-down default instead.

For end-to-end Genestack export and PR testing, provide a GitHub token with
repository write access before running `create.sh`. The configurator accepts
`GITHUB_TOKEN`. Add `--require-github-write` if you want bootstrap to fail
unless that token is present.

When remote Bakery adapter configuration is present, the devstack bootstrap also
disables the `dummy` plugin and its scheduled tasks so there is only one active
communication provider in the live plugin catalog.

The devstack Bakery bootstrap path also sets an explicit remote monitor identity
by default:

- `BAKERY_PLUGIN_ID=rackspace/kronos-poundcake`
- `BAKERY_REGION=ord`

Override `BAKERY_PLUGIN_ID` if the remote Bakery environment minted the
bootstrap credential for a different monitor ID.

### Examples

```bash
# Build images and deploy everything
helm/devstack/create.sh --build-images --all

# Build images and deploy everything with GitHub PR-capable adapter wiring
GITHUB_TOKEN=ghp_example helm/devstack/create.sh --build-images --all --require-github-write

# Create cluster only; no Helm installs
helm/devstack/create.sh --kind-cluster

# Build, load, and install just PoundCake (skip monitoring and stackstorm)
helm/devstack/create.sh --build-images --poundcake

# Install stackstorm on an existing cluster
helm/devstack/create.sh --stackstorm

# Full install without port-forwards
helm/devstack/create.sh --build-images --all --no-port-forward

# Custom timeout and values
helm/devstack/create.sh --all --timeout 20m --values /path/to/values.yaml
```

Supported env-var overrides include `CHART_DIR`, `KIND_CONFIG`,
`POUNDCAKE_NAMESPACE`, `WAIT`, `WAIT_TIMEOUT`, `VALUES_FILE`,
`HELM_EXTRA_ARGS`, `STACKSTORM_CHART_REF`, `STACKSTORM_VALUES_FILE`,
`STACKSTORM_CLIENT_ENABLED`, and `STACKSTORM_WEB_ENABLED`.

## Local Secrets File

Helm devstack scripts automatically source a local-only shell fragment at:

```bash
helm/devstack/.devstack-secrets.sh
```

That file is gitignored on purpose. Shell format is the preferred shape here
because the devstack entrypoints are already Bash scripts, and adapter/test
credentials can be exported directly without extra parsing glue.

Start from the tracked example:

```bash
cp helm/devstack/devstack-secrets.example.sh helm/devstack/.devstack-secrets.sh
```

Or refresh it from the current local cluster state:

```bash
bash helm/devstack/refresh-local-secrets.sh
```

The refresh helper snapshots the current devstack-managed admin auth, webhook
bearer token, MariaDB root password, and StackStorm API key into the gitignored
file, while preserving GitHub and Bakery fields from your local environment
when present.

If StackStorm is installed outside `create.sh`, configure PoundCake's adapter
connection state with:

```bash
helm/devstack/configure-stackstorm-adapter.sh
```

## Destroy

```bash
helm/devstack/destroy.sh
```

Default behavior: uninstall all Helm releases and namespaces, stop port-forwards,
keep the kind cluster.

```bash
# Remove the kind cluster as well
helm/devstack/destroy.sh DELETE_CLUSTER=true

# Keep monitoring or stackstorm namespaces
helm/devstack/destroy.sh DELETE_MONITORING_NAMESPACE=false
helm/devstack/destroy.sh DELETE_STACKSTORM_NAMESPACE=false
```

## Local Port Forwards

`create.sh` starts local port-forwards by default after a successful install
(unless `--no-port-forward` is set), and `destroy.sh` stops them by default.

```bash
helm/devstack/ui-port-forward.sh start
helm/devstack/ui-port-forward.sh status
helm/devstack/ui-port-forward.sh verify
helm/devstack/ui-port-forward.sh stop
```

The helper forwards these URLs by default:

- UI: `http://127.0.0.1:8080` to `svc/poundcake-ui` in `poundcake`
- Prometheus: `http://127.0.0.1:9090` to `svc/kube-prometheus-stack-prometheus` in `monitoring`
- Alertmanager: `http://127.0.0.1:9093` to `svc/kube-prometheus-stack-alertmanager` in `monitoring`

`start` now verifies that each local endpoint is actually reachable before it
reports success, and `create.sh` calls the same verification step before
declaring the devstack ready. PID and log files are stored under
`/tmp/poundcake-helm-devstack`.
