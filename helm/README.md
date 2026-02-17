# poundcake-standalone

Standalone Helm chart generated from `docker/docker-compose.yml`.

## What it includes

- Core PoundCake services: `api`, `chef`, `prep-chef`, `timer`, `dishwasher`
- StackStorm services: `stackstorm-api`, `stackstorm-auth`, `stackstorm-actionrunner`, `stackstorm-stream`, `stackstorm-workflowengine`, `stackstorm-notifier`, `stackstorm-garbagecollector`, `stackstorm-scheduler`, `stackstorm-client`
- Datastores: `mariadb`, `stackstorm-mongodb`, `stackstorm-rabbitmq`, `stackstorm-redis`
- Bootstraps/jobs: `poundcake-pack-init`, `stackstorm-bootstrap`, `poundcake-bootstrap`
- Mounted scripts/config from compose embedded as chart files/configmaps

## Install

```bash
helm upgrade --install poundcake-standalone ./temp/poundcake-standalone \
  --set poundcakeImage.repository=<your-repo/poundcake> \
  --set poundcakeImage.tag=<tag>
```

## Notes

- This chart is intentionally direct and mirrors compose behavior.
- Persistent volumes for `packs` and `config` are shared; ensure your storage class can satisfy simultaneous mounts.
