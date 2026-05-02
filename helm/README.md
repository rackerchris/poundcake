# poundcake Helm Chart

This chart deploys PoundCake, its UI, workers, and the PoundCake database/runtime bootstrap jobs.

## Install

```bash
helm upgrade --install poundcake ./helm \
  --set poundcakeImage.repository=<your-repo/poundcake> \
  --set poundcakeImage.tag=<tag>
```

When browser auth is enabled, override `auth.allowedOrigins` with the explicit
UI origin(s) for the deployment instead of using a wildcard CORS origin.

## Installer

Use the repo wrapper for PoundCake:

```bash
./install/install-poundcake-helm.sh
```

Service plugin enablement, endpoint configuration, and credentials are managed through PoundCake's plugin system and credential-manager.
