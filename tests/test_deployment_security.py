"""Deployment hardening checks for credential and service identity boundaries."""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _read(path: str) -> str:
    return (ROOT / path).read_text()


def test_worker_deployments_use_service_scoped_database_identities() -> None:
    workloads = _read("helm/templates/poundcake-workloads.yaml")
    compose = _read("docker/docker-compose.yml")

    assert "DB_WORKER_READER_USER" not in workloads
    assert "poundcake_worker_reader" not in compose
    for service_name, env_name, user_name in (
        ("prep-chef", "DB_PREP_CHEF_READER_USER", "poundcake_prep_chef_reader"),
        ("timer", "DB_TIMER_READER_USER", "poundcake_timer_reader"),
        (
            "expediter-runner",
            "DB_EXPEDITER_RUNNER_READER_USER",
            "poundcake_expediter_runner_reader",
        ),
        ("dishwasher", "DB_DISHWASHER_READER_USER", "poundcake_dishwasher_reader"),
    ):
        assert env_name in workloads
        assert user_name in compose
        assert f"value: {service_name}" in workloads
        assert f"POUNDCAKE_INTERNAL_HMAC_SERVICE_TYPE: {service_name}" in compose


def test_worker_grants_target_service_identity_views_not_base_table() -> None:
    docker_init = _read("docker/mariadb-init/01-create-databases.sh")
    helm_init = _read("helm/files/mariadb-init/01-create-databases.sh")
    shared_mariadb = _read("helm/templates/poundcake-shared-mariadb.yaml")

    for content in (docker_init, helm_init, shared_mariadb):
        assert "poundcake_worker_reader" not in content
        assert "worker-reader-service-identity" not in content
        assert "service_identity_credentials_prep_chef" in content
        assert "service_identity_credentials_timer" in content
        assert "service_identity_credentials_expediter_runner" in content
        assert "service_identity_credentials_dishwasher" in content
    assert "poundcake_plugin_registry" in docker_init
    assert "poundcake_service_identity_manager" in docker_init
    assert "MYSQL_PLUGIN_REGISTRY_USER" in helm_init
    assert "MYSQL_SERVICE_IDENTITY_MANAGER_USER" in helm_init
    assert "dbPluginRegistryUser" in shared_mariadb
    assert "dbServiceIdentityManagerUser" in shared_mariadb


def test_readonly_grants_do_not_include_credential_tables() -> None:
    docker_init = _read("docker/mariadb-init/01-create-databases.sh")
    helm_init = _read("helm/files/mariadb-init/01-create-databases.sh")
    shared_mariadb = _read("helm/templates/poundcake-shared-mariadb.yaml")

    assert "GRANT SELECT ON poundcake.* TO 'poundcake_readonly'" not in docker_init
    assert "GRANT SELECT ON \\`${MYSQL_DATABASE}\\`.* TO '${MYSQL_READONLY_USER}'" not in helm_init
    assert '"suffix" "readonly"' not in shared_mariadb
    assert "service_identity_credentials_%'" in helm_init
    for content in (docker_init, helm_init, shared_mariadb):
        readonly_lines = [
            line
            for line in content.splitlines()
            if "readonly" in line.lower() or "MYSQL_READONLY_USER" in line
        ]
        readonly_text = "\n".join(readonly_lines)
        assert "adapter_credentials" not in readonly_text
        assert "service_identity_credentials" not in readonly_text


def test_api_service_identity_reads_use_auth_verifier_persona() -> None:
    docker_init = _read("docker/mariadb-init/01-create-databases.sh")
    helm_init = _read("helm/files/mariadb-init/01-create-databases.sh")
    shared_mariadb = _read("helm/templates/poundcake-shared-mariadb.yaml")
    workloads = _read("helm/templates/poundcake-workloads.yaml")
    compose = _read("docker/docker-compose.yml")

    assert "poundcake_auth_verifier" in docker_init
    assert "POUNDCAKE_AUTH_VERIFIER_DATABASE_URL" in compose
    assert "POUNDCAKE_AUTH_VERIFIER_DATABASE_URL" in workloads
    assert "dbAuthVerifierUser" in shared_mariadb
    assert "MYSQL_AUTH_VERIFIER_USER" in helm_init
    assert "service_identity_credentials\\` TO '${MYSQL_AUTH_VERIFIER_USER}'" in helm_init
    assert "service_identity_credentials TO 'poundcake_auth_verifier'" in docker_init
    assert "service_identity_credentials TO 'poundcake_api'" not in docker_init
    assert "service_identity_credentials` TO '${MYSQL_API_USER}'" not in helm_init
    assert '"username" $.Values.secrets.dbApiUser' in shared_mariadb
    assert '"table" "service_identity_credentials"' in shared_mariadb
    assert (
        '"username" $.Values.secrets.dbApiUser "passwordKey" "DB_API_PASSWORD" '
        '"privileges" (list "SELECT") "table" "service_identity_credentials"' not in shared_mariadb
    )


def test_plugin_operation_boundary_uses_dedicated_persona() -> None:
    docker_init = _read("docker/mariadb-init/01-create-databases.sh")
    helm_init = _read("helm/files/mariadb-init/01-create-databases.sh")
    shared_mariadb = _read("helm/templates/poundcake-shared-mariadb.yaml")
    workloads = _read("helm/templates/poundcake-workloads.yaml")
    compose = _read("docker/docker-compose.yml")

    assert "poundcake_plugin_operation" in docker_init
    assert "MYSQL_PLUGIN_OPERATION_USER" in helm_init
    assert "dbPluginOperationUser" in shared_mariadb
    assert "POUNDCAKE_PLUGIN_OPERATION_DB_URL" in workloads
    assert "POUNDCAKE_PLUGIN_OPERATION_DB_URL" in compose
    assert "service_plugins\\` TO '${MYSQL_PLUGIN_OPERATION_USER}'" in helm_init
    assert "service_plugins TO 'poundcake_plugin_operation'" in docker_init

    plugin_operation_lines = "\n".join(
        line for line in docker_init.splitlines() if "poundcake_plugin_operation" in line
    )
    assert "service_plugins" in plugin_operation_lines
    assert "ingredients" in plugin_operation_lines
    assert "recipes" in plugin_operation_lines
    assert "recipe_ingredients" in plugin_operation_lines
    assert "scheduled_tasks" in plugin_operation_lines
    assert "dishes" in plugin_operation_lines
    assert "adapter_credentials" not in plugin_operation_lines
    assert "service_identity_credentials" not in plugin_operation_lines


def test_credential_boundary_code_uses_explicit_personas() -> None:
    plugin_api = _read("api/api/plugins.py")
    service_identity = _read("api/services/service_identity.py")
    auth = _read("api/api/auth.py")

    assert "read_adapter_credential_with_policy" in plugin_api
    assert "_credential_configured(\n    *," in plugin_api
    assert "worker_reader_db_session" in service_identity
    assert "SessionLocal" not in service_identity
    assert "get_session_store().put_if_absent" in auth
    assert "if not await _check_nonce(nonce_key, clock_skew):" in auth


def test_plugin_packages_do_not_open_raw_database_sessions() -> None:
    import re

    forbidden_patterns = (
        (r"^from api\.core\.database import", "from api.core.database import"),
        (r"^import api\.core\.database", "import api.core.database"),
        (r"^SessionLocal\b", "SessionLocal"),
        (r"^plugin_operation_db_session", "plugin_operation_db_session"),
        (r"^credential_manager_db_session", "credential_manager_db_session"),
    )
    offenders: list[str] = []
    for path in sorted((ROOT / "api/plugins").rglob("*.py")):
        contents = path.read_text()
        for pattern, _label in forbidden_patterns:
            if re.search(pattern, contents, re.MULTILINE):
                offenders.append(str(path.relative_to(ROOT)))
                break

    assert offenders == []


def test_startup_principals_have_exact_grants() -> None:
    docker_init = _read("docker/mariadb-init/01-create-databases.sh")
    helm_init = _read("helm/files/mariadb-init/01-create-databases.sh")
    shared_mariadb = _read("helm/templates/poundcake-shared-mariadb.yaml")

    assert "poundcake_plugin_registry" in docker_init
    assert "poundcake_service_identity_manager" in docker_init
    assert "MYSQL_PLUGIN_REGISTRY_USER" in helm_init
    assert "MYSQL_SERVICE_IDENTITY_MANAGER_USER" in helm_init
    assert "dbPluginRegistryUser" in shared_mariadb
    assert "dbServiceIdentityManagerUser" in shared_mariadb

    plugin_registry_lines = "\n".join(
        line for line in docker_init.splitlines() if "poundcake_plugin_registry" in line
    )
    assert "service_plugins" in plugin_registry_lines
    assert "adapter_credentials" not in plugin_registry_lines
    assert "service_identity_credentials" not in plugin_registry_lines
    assert "recipes" not in plugin_registry_lines
    assert "ingredients" not in plugin_registry_lines
    assert "scheduled_tasks" not in plugin_registry_lines

    service_identity_lines = "\n".join(
        line for line in docker_init.splitlines() if "poundcake_service_identity_manager" in line
    )
    assert "service_plugins" in service_identity_lines
    assert "service_identity_credentials" in service_identity_lines
    assert "adapter_credentials" not in service_identity_lines

    credential_manager_lines = "\n".join(
        line for line in docker_init.splitlines() if "poundcake_credential_manager" in line
    )
    assert "adapter_credentials" in credential_manager_lines
    assert "service_identity_credentials" not in credential_manager_lines


def test_split_bootstrap_jobs_isolate_keys_and_database_users() -> None:
    startup_jobs = _read("helm/templates/poundcake-startup-jobs.yaml")

    assert "poundcake-bootstrap-plugin-registry" in startup_jobs
    assert "poundcake-bootstrap-service-identity" in startup_jobs
    assert "poundcake-bootstrap-adapter-credentials" in startup_jobs
    assert "poundcake-migrate" not in startup_jobs
    assert "python3 -m api.scripts.bootstrap_plugin_registry" in startup_jobs
    assert "python3 -m api.scripts.bootstrap_service_identities" in startup_jobs
    assert "python3 -m api.scripts.bootstrap_adapter_credentials" in startup_jobs
    assert (
        'export POUNDCAKE_DATABASE_URL="mysql+pymysql://${DB_PLUGIN_REGISTRY_USER}' in startup_jobs
    )
    assert (
        'export POUNDCAKE_DATABASE_URL="mysql+pymysql://${DB_SERVICE_IDENTITY_MANAGER_USER}'
        in startup_jobs
    )
    assert (
        'export POUNDCAKE_DATABASE_URL="mysql+pymysql://${DB_CREDENTIAL_MANAGER_USER}'
        in startup_jobs
    )

    plugin_section = startup_jobs.split("name: poundcake-bootstrap-plugin-registry", 1)[1].split(
        "---", 1
    )[0]
    assert "POUNDCAKE_PLUGIN_CREDENTIAL_ENCRYPTION_KEY" not in plugin_section
    assert "POUNDCAKE_SERVICE_IDENTITY_CREDENTIAL_ENCRYPTION_KEY" not in plugin_section

    service_identity_section = startup_jobs.split("name: poundcake-bootstrap-service-identity", 1)[
        1
    ].split("---", 1)[0]
    assert "POUNDCAKE_SERVICE_IDENTITY_CREDENTIAL_ENCRYPTION_KEY" in service_identity_section
    assert "POUNDCAKE_PLUGIN_CREDENTIAL_ENCRYPTION_KEY" not in service_identity_section

    adapter_section = startup_jobs.split("name: poundcake-bootstrap-adapter-credentials", 1)[1]
    assert "POUNDCAKE_PLUGIN_CREDENTIAL_ENCRYPTION_KEY" in adapter_section
    assert "POUNDCAKE_SERVICE_IDENTITY_CREDENTIAL_ENCRYPTION_KEY" not in adapter_section
