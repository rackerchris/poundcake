#!/bin/bash
#  ___                        _  ____      _
# |  _ \ ___  _   _ _ __   __| |/ ___|__ _| | _____
# | |_) / _ \| | | | '_ \ / _` | |   / _` | |/ / _ \
# |  __/ (_) | |_| | | | | (_| | |__| (_| |   <  __/
# |_|   \___/ \__,_|_| |_|\__,_|\____\__,_|_|\_\___|
#
set -e

echo "Creating PoundCake databases..."

mariadb -uroot -p"${MYSQL_ROOT_PASSWORD}" <<-EOSQL
    -- PoundCake application database
    CREATE DATABASE IF NOT EXISTS poundcake CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;

    -- PoundCake least-privilege personas
    CREATE USER IF NOT EXISTS 'poundcake_migrator'@'%' IDENTIFIED BY 'poundcake';
    CREATE USER IF NOT EXISTS 'poundcake_plugin_registry'@'%' IDENTIFIED BY 'poundcake';
    CREATE USER IF NOT EXISTS 'poundcake_api'@'%' IDENTIFIED BY 'poundcake';
    CREATE USER IF NOT EXISTS 'poundcake_auth_verifier'@'%' IDENTIFIED BY 'poundcake';
    CREATE USER IF NOT EXISTS 'poundcake_service_identity_manager'@'%' IDENTIFIED BY 'poundcake';
    CREATE USER IF NOT EXISTS 'poundcake_credential_manager'@'%' IDENTIFIED BY 'poundcake';
    CREATE USER IF NOT EXISTS 'poundcake_plugin_operation'@'%' IDENTIFIED BY 'poundcake';
    CREATE USER IF NOT EXISTS 'poundcake_prep_chef_reader'@'%' IDENTIFIED BY 'poundcake';
    CREATE USER IF NOT EXISTS 'poundcake_timer_reader'@'%' IDENTIFIED BY 'poundcake';
    CREATE USER IF NOT EXISTS 'poundcake_expediter_runner_reader'@'%' IDENTIFIED BY 'poundcake';
    CREATE USER IF NOT EXISTS 'poundcake_dishwasher_reader'@'%' IDENTIFIED BY 'poundcake';
    CREATE USER IF NOT EXISTS 'poundcake_readonly'@'%' IDENTIFIED BY 'poundcake';

    GRANT ALL PRIVILEGES ON poundcake.* TO 'poundcake_migrator'@'%';
    GRANT SELECT ON poundcake.recipe_ingredients TO 'poundcake_api'@'%';
    GRANT SELECT ON poundcake.recipes TO 'poundcake_api'@'%';
    GRANT SELECT ON poundcake.ingredients TO 'poundcake_api'@'%';
    GRANT SELECT ON poundcake.service_plugins TO 'poundcake_api'@'%';
    GRANT SELECT ON poundcake.scheduled_tasks TO 'poundcake_api'@'%';
    GRANT SELECT ON poundcake.dishes TO 'poundcake_api'@'%';
    GRANT SELECT ON poundcake.dish_ingredients TO 'poundcake_api'@'%';
    GRANT SELECT ON poundcake.orders TO 'poundcake_api'@'%';
    GRANT SELECT ON poundcake.alert_suppressions TO 'poundcake_api'@'%';
    GRANT SELECT ON poundcake.alert_suppression_matchers TO 'poundcake_api'@'%';
    GRANT SELECT ON poundcake.suppressed_events TO 'poundcake_api'@'%';
    GRANT SELECT ON poundcake.suppression_summaries TO 'poundcake_api'@'%';
    GRANT SELECT ON poundcake.auth_principals TO 'poundcake_api'@'%';
    GRANT SELECT ON poundcake.auth_role_bindings TO 'poundcake_api'@'%';
    GRANT INSERT, UPDATE, DELETE ON poundcake.recipe_ingredients TO 'poundcake_api'@'%';
    GRANT INSERT, UPDATE, DELETE ON poundcake.recipes TO 'poundcake_api'@'%';
    GRANT INSERT, UPDATE, DELETE ON poundcake.ingredients TO 'poundcake_api'@'%';
    GRANT INSERT, UPDATE, DELETE ON poundcake.service_plugins TO 'poundcake_api'@'%';
    GRANT INSERT, UPDATE, DELETE ON poundcake.scheduled_tasks TO 'poundcake_api'@'%';
    GRANT INSERT, UPDATE, DELETE ON poundcake.dishes TO 'poundcake_api'@'%';
    GRANT INSERT, UPDATE, DELETE ON poundcake.dish_ingredients TO 'poundcake_api'@'%';
    GRANT INSERT, UPDATE, DELETE ON poundcake.orders TO 'poundcake_api'@'%';
    GRANT INSERT, UPDATE, DELETE ON poundcake.alert_suppressions TO 'poundcake_api'@'%';
    GRANT INSERT, UPDATE, DELETE ON poundcake.alert_suppression_matchers TO 'poundcake_api'@'%';
    GRANT INSERT, UPDATE, DELETE ON poundcake.suppressed_events TO 'poundcake_api'@'%';
    GRANT INSERT, UPDATE, DELETE ON poundcake.suppression_summaries TO 'poundcake_api'@'%';
    GRANT INSERT, UPDATE, DELETE ON poundcake.auth_principals TO 'poundcake_api'@'%';
    GRANT INSERT, UPDATE, DELETE ON poundcake.auth_role_bindings TO 'poundcake_api'@'%';
    GRANT SELECT, INSERT, UPDATE ON poundcake.service_plugins TO 'poundcake_plugin_registry'@'%';
    GRANT SELECT ON poundcake.service_plugins TO 'poundcake_auth_verifier'@'%';
    GRANT SELECT ON poundcake.service_identity_credentials TO 'poundcake_auth_verifier'@'%';
    GRANT SELECT, INSERT, DELETE ON poundcake.hmac_nonces TO 'poundcake_auth_verifier'@'%';
    GRANT SELECT, UPDATE ON poundcake.service_plugins TO 'poundcake_service_identity_manager'@'%';
    GRANT SELECT, INSERT, UPDATE ON poundcake.service_identity_credentials TO 'poundcake_service_identity_manager'@'%';
    GRANT SELECT, UPDATE ON poundcake.service_plugins TO 'poundcake_credential_manager'@'%';
    GRANT SELECT, INSERT, UPDATE, DELETE ON poundcake.adapter_credentials TO 'poundcake_credential_manager'@'%';
    GRANT SELECT, UPDATE ON poundcake.service_plugins TO 'poundcake_plugin_operation'@'%';
    GRANT SELECT, INSERT, UPDATE, DELETE ON poundcake.ingredients TO 'poundcake_plugin_operation'@'%';
    GRANT SELECT, INSERT, UPDATE, DELETE ON poundcake.recipes TO 'poundcake_plugin_operation'@'%';
    GRANT SELECT, INSERT, UPDATE, DELETE ON poundcake.recipe_ingredients TO 'poundcake_plugin_operation'@'%';
    GRANT SELECT, INSERT, UPDATE, DELETE ON poundcake.scheduled_tasks TO 'poundcake_plugin_operation'@'%';
    GRANT SELECT, INSERT, UPDATE, DELETE ON poundcake.dishes TO 'poundcake_plugin_operation'@'%';
    GRANT SELECT ON poundcake.service_plugins TO 'poundcake_prep_chef_reader'@'%';
    GRANT SELECT ON poundcake.service_identity_credentials_prep_chef TO 'poundcake_prep_chef_reader'@'%';
    GRANT SELECT ON poundcake.service_plugins TO 'poundcake_timer_reader'@'%';
    GRANT SELECT ON poundcake.service_identity_credentials_timer TO 'poundcake_timer_reader'@'%';
    GRANT SELECT ON poundcake.service_plugins TO 'poundcake_expediter_runner_reader'@'%';
    GRANT SELECT ON poundcake.service_identity_credentials_expediter_runner TO 'poundcake_expediter_runner_reader'@'%';
    GRANT SELECT ON poundcake.service_plugins TO 'poundcake_dishwasher_reader'@'%';
    GRANT SELECT ON poundcake.service_identity_credentials_dishwasher TO 'poundcake_dishwasher_reader'@'%';
    GRANT SELECT ON poundcake.recipe_ingredients TO 'poundcake_readonly'@'%';
    GRANT SELECT ON poundcake.recipes TO 'poundcake_readonly'@'%';
    GRANT SELECT ON poundcake.ingredients TO 'poundcake_readonly'@'%';
    GRANT SELECT ON poundcake.service_plugins TO 'poundcake_readonly'@'%';
    GRANT SELECT ON poundcake.scheduled_tasks TO 'poundcake_readonly'@'%';
    GRANT SELECT ON poundcake.dishes TO 'poundcake_readonly'@'%';
    GRANT SELECT ON poundcake.dish_ingredients TO 'poundcake_readonly'@'%';
    GRANT SELECT ON poundcake.orders TO 'poundcake_readonly'@'%';
    GRANT SELECT ON poundcake.alert_suppressions TO 'poundcake_readonly'@'%';
    GRANT SELECT ON poundcake.alert_suppression_matchers TO 'poundcake_readonly'@'%';
    GRANT SELECT ON poundcake.suppressed_events TO 'poundcake_readonly'@'%';
    GRANT SELECT ON poundcake.suppression_summaries TO 'poundcake_readonly'@'%';
    GRANT SELECT ON poundcake.auth_principals TO 'poundcake_readonly'@'%';
    GRANT SELECT ON poundcake.auth_role_bindings TO 'poundcake_readonly'@'%';

    FLUSH PRIVILEGES;
EOSQL

echo "Database initialization complete!"
echo "Created databases:"
echo "  - poundcake (users: migrator, api, auth_verifier, credential_manager, plugin_operation, per-worker readers, readonly)"
echo ""
