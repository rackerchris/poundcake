# Docker Devstack

These helpers run the local PoundCake-only validation stack with Docker Compose. Colima is the
recommended macOS container layer; `docker compose` and standalone `docker-compose` are both
supported.

## create.sh

Builds and starts the dummy-only local development stack, waits for unauthenticated
probe endpoints `/livez` and `/readyz`, logs in, and then prints plugin health,
service registry, and recipes through authenticated API calls.

```bash
docker/devstack/create.sh
```

Defaults:

- API: `http://127.0.0.1:8000/api/v1`
- UI: `http://127.0.0.1:8080`
- Login: `admin` / `poundcake-dev`

## destroy.sh

Stops the local development stack, removes orphan containers, removes the MariaDB volume by
default, and deletes the local bootstrap marker so the next create is greenfield.

```bash
docker/devstack/destroy.sh
```

Set `REMOVE_VOLUMES=false` to keep the database volume:

```bash
REMOVE_VOLUMES=false docker/devstack/destroy.sh
```

The stack builds and starts MariaDB, PoundCake bootstrap, API, UI, Prep-Chef,
Dishwasher, and Timer with only the `dummy` service plugin enabled.
