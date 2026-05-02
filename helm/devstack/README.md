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

## Quick Start

```bash
# Full teardown
helm/devstack/destroy.sh DELETE_CLUSTER=true

# Build, deploy, and start everything
helm/devstack/create.sh --build-images --install-all
```

## Create

```bash
helm/devstack/create.sh --build-images --install-all
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
  --install-all             Install poundcake + monitoring + stackstorm
  --install-poundcake       Install only the PoundCake Helm chart
  --install-monitoring      Install Prometheus/Alertmanager stack
  --install-stackstorm      Install StackStorm (includes adapter config)
  --skip-stackstorm-config  Skip StackStorm adapter configuration
  --no-port-forward         Skip local port-forwards

Lifecycle options:
  --skip-node-sysctls       Skip fsnotify sysctl tuning
  --no-wait                 Skip helm --wait
  --timeout DURATION        Wait timeout (default: 15m)
  --values FILE             Override values file
```

### Defaults

When `--install-all` is used, the following are deployed:

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

### Examples

```bash
# Build images and deploy everything
helm/devstack/create.sh --build-images --install-all

# Create cluster only; no Helm installs
helm/devstack/create.sh --create-cluster

# Build, load, and install just PoundCake (skip monitoring and stackstorm)
helm/devstack/create.sh --build-images --install-poundcake

# Install stackstorm on an existing cluster
helm/devstack/create.sh --install-stackstorm

# Full install without port-forwards
helm/devstack/create.sh --build-images --install-all --no-port-forward

# Custom timeout and values
helm/devstack/create.sh --install-all --timeout 20m --values /path/to/values.yaml
```

Env-var overrides such as `CHART_DIR`, `KIND_CONFIG`, `NAMESPACE`, `WAIT`,
`WAIT_TIMEOUT`, `VALUES_FILE`, `HELM_EXTRA_ARGS`, `STACKSTORM_CHART_REF`,
`STACKSTORM_VALUES_FILE`, `STACKSTORM_CLIENT_ENABLED`, and `STACKSTORM_WEB_ENABLED`
are still supported for backward compatibility.

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
helm/devstack/ui-port-forward.sh stop
```

The helper forwards these URLs by default:

- UI: `http://127.0.0.1:8080` to `svc/poundcake-ui` in `poundcake`
- Prometheus: `http://127.0.0.1:9090` to `svc/kube-prometheus-stack-prometheus` in `monitoring`
- Alertmanager: `http://127.0.0.1:9093` to `svc/kube-prometheus-stack-alertmanager` in `monitoring`

PID and log files are stored under `/tmp/poundcake-helm-devstack`.
