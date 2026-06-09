# PoundCake Operator Guide

## Summary

PoundCake production deployments are Helm based. Operators manage runtime behavior with Helm values files and Kubernetes secrets; application code and local developer workflows stay out of production override files.

This guide covers the PoundCake Helm release. Remote Bakery is deployed as its own Helm release and connected to PoundCake through the `bakery` plugin. See [REMOTE_BAKERY.md](REMOTE_BAKERY.md).

For database modes and startup database behavior, see [DATABASE.md](DATABASE.md).
For auth and internal RBAC behavior, see [AUTH_RBAC.md](AUTH_RBAC.md).
For plugin support tiers and per-plugin requirements, see
[plugins/README.md](plugins/README.md).

## Supported Deployment Model

- PoundCake deploys API, UI, workers, StackStorm, and supporting infrastructure.
- Bakery is not rendered as an in-cluster PoundCake subcomponent.
- Remote Bakery communication is configured through the plugin configuration and credential contracts.
- Runtime configuration belongs in Helm values files or operator-owned plugin configuration records, depending on the subsystem.
- Sensitive material belongs in Kubernetes secrets, Auth Service configuration,
  Service Identity Manager rows, or Credential Manager-owned adapter credential
  rows depending on the subsystem.
- Plugin health checks and plugin-owned scheduled work enter the normal order workflow.

## Values And Overrides

Recommended operator-managed paths:

- PoundCake overrides: `/etc/poundcake/helm-configs/poundcake/`
- Global overrides: `/etc/poundcake/helm-configs/global_overrides/`
- Shared chart version file: `/etc/poundcake/helm-chart-versions.yaml`

Recommended override layout:

- `00-pull-secret-overrides.yaml`
- `10-main-overrides.yaml`
- `20-auth-overrides.yaml`
- `30-git-sync-overrides.yaml`

The installer builds Helm input in this order:

1. chart defaults from `helm/values.yaml`
2. base overrides when configured
3. sorted files in the global overrides directory
4. sorted files in the PoundCake service override directory
5. explicit extra Helm args

Keep image repositories, tags, digests, gateway settings, auth settings, and StackStorm bootstrap settings in values files. Configure Bakery adapter connection state from the Plugins UI or plugin configuration API.

When browser auth is enabled, set `auth.allowedOrigins` to the explicit UI
origin(s) that should be allowed to call the API. Do not use `["*"]` for
production browser/OIDC deployments.

## Minimum Helm Values

```yaml
gateway:
  enabled: true
  gatewayName: flex-gateway
  gatewayNamespace: envoy-gateway
  hostnames:
    - poundcake.example.com
  listeners:
    api:
      pathPrefix: /api
    ui:
      pathPrefix: /

config:
  enabledPlugins: dummy,k8s,git,github,prometheus,alertmanager,bakery,stackstorm,genestack_monitoring

auth:
  allowedOrigins:
    - https://poundcake.example.com
    - https://ui.poundcake.example.com
```

After enabling `bakery`, configure the remote HTTPS URL and non-secret adapter metadata from the Plugins UI. Admins configure the Bakery bootstrap credential through the adapter credential contract; PoundCake stores the returned monitor credential as adapter-managed state.

## Bakery Credential

Admins configure the Bakery-issued bootstrap credential through
`/api/v1/plugins/bakery/credentials` or the Plugins UI credential controls. The
UI labels the fields as `bootstrap-key-id` and `bootstrap-key`; the API payload
uses `hmac_key_id` and `hmac_secret`.

## Install Or Upgrade

Using the repo installer:

```bash
./install/install-poundcake-helm.sh --validate
./install/install-poundcake-helm.sh
```

Using Helm directly:

```bash
helm upgrade --install poundcake <poundcake-chart> \
  --namespace poundcake \
  --create-namespace \
  -f /etc/poundcake/helm-configs/poundcake/10-main-overrides.yaml
```

For private registries, configure pull secrets with `poundcakeImage.pullSecrets` and ensure the namespace has the referenced Docker registry secret.

## Optional StackStorm Packs

The chart can install StackStorm `kubernetes` and `openstack` packs during startup. These are opt-in and should be configured through secured operator override files.

```yaml
stackstorm:
  bootstrap:
    packs:
      kubernetes:
        enabled: true
        version: ""
        config:
          kubeconfig: |
            apiVersion: v1
            kind: Config
            clusters: []
            contexts: []
            current-context: ""
            users: []
          caCert: ""
      openstack:
        enabled: true
        version: ""
        config:
          cloudsYaml: |
            clouds:
              target:
                auth:
                  auth_url: https://keystone.example.com:5000/v3
                  username: example
                  password: example
                  project_name: example
                  user_domain_name: Default
                  project_domain_name: Default
                region_name: RegionOne
          caCert: ""
```

For horizontally scaled StackStorm, use shared RWX storage for third-party pack files and virtualenvs:

```yaml
persistence:
  stackstormSharedStorage:
    enabled: true
    storageClassName: longhorn-rwx
    accessMode: ReadWriteMany
    packVolumeSize: 5Gi
    virtualenvVolumeSize: 10Gi
```

If the shared directories are owned by a non-default group, set `stackstormPodSecurityContext.fsGroup` and `supplementalGroups` to the owning numeric GID.

## Local Development Devstack

For local development, the `helm/devstack/` scripts manage a kind cluster with
PoundCake, Prometheus/Alertmanager, and StackStorm. See
[`helm/devstack/README.md`](helm/devstack/README.md) for full reference.

### Full Devstack (Build, Deploy, Test)

```bash
# Tear down any existing devstack
helm/devstack/destroy.sh DELETE_CLUSTER=true

# Build images, create cluster, and deploy everything
helm/devstack/create.sh --build-images --all
```

### Individual Steps

The devstack script accepts flags for each step so you can run them independently:

```bash
# Build Docker images only
helm/devstack/create.sh --build-images

# Load pre-built images into an existing kind cluster
helm/devstack/create.sh --load-images

# Create kind cluster only (no Helm installs)
helm/devstack/create.sh --create-cluster --no-port-forward

# Install components individually on an existing cluster
helm/devstack/create.sh --poundcake
helm/devstack/create.sh --monitoring
helm/devstack/create.sh --stackstorm
```

### Devstack Flags

```bash
# Cluster and image options
helm/devstack/create.sh --build-images --load-images --create-cluster
helm/devstack/create.sh --skip-create-cluster
helm/devstack/create.sh --app-image myregistry/poundcake:v1 --ui-image myregistry/poundcake-ui:v1

# Component install options
helm/devstack/create.sh --all
helm/devstack/create.sh --poundcake --monitoring --stackstorm
helm/devstack/create.sh --all --skip-stackstorm-config

# Lifecycle options
helm/devstack/create.sh --all --no-port-forward
helm/devstack/create.sh --all --skip-node-sysctls
helm/devstack/create.sh --all --no-wait
helm/devstack/create.sh --all --timeout 20m
helm/devstack/create.sh --all --values /path/to/custom-values.yaml
```

After a devstack deploy, the UI is available at `http://127.0.0.1:8080`,
Prometheus at `http://127.0.0.1:9090`, and Alertmanager at
`http://127.0.0.1:9093` (when port-forwards are active).

### Teardown

```bash
# Uninstall Helm releases only; keep kind cluster
helm/devstack/destroy.sh

# Uninstall everything including the kind cluster
helm/devstack/destroy.sh DELETE_CLUSTER=true
```

## Verify

Wait for rollout:

```bash
kubectl -n poundcake rollout status deploy/poundcake-api --timeout=300s
kubectl -n poundcake rollout status deploy/poundcake-ui --timeout=300s
```

Confirm rendered values and Bakery plugin state:

```bash
helm get values poundcake -n poundcake -o yaml
curl -fsS https://poundcake.example.com/api/v1/plugins/bakery/configuration
```

Check PoundCake health and plugins:

```bash
curl -fsS https://poundcake.example.com/api/v1/health
curl -fsS https://poundcake.example.com/api/v1/plugins
curl -fsS https://poundcake.example.com/api/v1/plugins/bakery/health
curl -fsS https://poundcake.example.com/api/v1/scheduled-tasks
```

Expected Bakery signals:

- `bakery` appears in `/api/v1/plugins`
- `/api/v1/plugins/bakery/configuration` shows a saved HTTPS `url`
- `/api/v1/plugins/bakery/configuration` reports `credential_configured=true`
- the `bakery` plugin reports `healthy` or `degraded` after its scheduled health order runs

## Operator Checks

Before applying production chart changes:

```bash
helm lint ./helm
helm unittest ./helm --file 'tests/unittest/*_test.yaml'
```

After deployment, use order timelines and scheduled task state to verify plugin work. Plugin health checks, Bakery communication, and scheduled plugin operations should all appear as normal orders with dishes and runtime rows.
