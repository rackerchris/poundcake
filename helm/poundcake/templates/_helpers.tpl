{{/*
Expand the name of the chart.
*/}}
{{- define "poundcake.name" -}}
{{- default .Chart.Name .Values.nameOverride | trunc 63 | trimSuffix "-" }}
{{- end }}

{{/*
Create a default fully qualified app name.
We truncate at 63 chars because some Kubernetes name fields are limited to this (by the DNS naming spec).
If release name contains chart name it will be used as a full name.
*/}}
{{- define "poundcake.fullname" -}}
{{- if .Values.fullnameOverride }}
{{- .Values.fullnameOverride | trunc 63 | trimSuffix "-" }}
{{- else }}
{{- $name := default .Chart.Name .Values.nameOverride }}
{{- if contains $name .Release.Name }}
{{- .Release.Name | trunc 63 | trimSuffix "-" }}
{{- else }}
{{- printf "%s-%s" .Release.Name $name | trunc 63 | trimSuffix "-" }}
{{- end }}
{{- end }}
{{- end }}

{{/*
Create chart name and version as used by the chart label.
*/}}
{{- define "poundcake.chart" -}}
{{- printf "%s-%s" .Chart.Name .Chart.Version | replace "+" "_" | trunc 63 | trimSuffix "-" }}
{{- end }}

{{/*
Common labels
*/}}
{{- define "poundcake.labels" -}}
helm.sh/chart: {{ include "poundcake.chart" . }}
{{ include "poundcake.selectorLabels" . }}
{{- if .Chart.AppVersion }}
app.kubernetes.io/version: {{ .Chart.AppVersion | quote }}
{{- end }}
app.kubernetes.io/managed-by: {{ .Release.Service }}
{{- end }}

{{/*
Selector labels
*/}}
{{- define "poundcake.selectorLabels" -}}
app.kubernetes.io/name: {{ include "poundcake.name" . }}
app.kubernetes.io/instance: {{ .Release.Name }}
{{- end }}

{{/*
Create the name of the service account to use
*/}}
{{- define "poundcake.serviceAccountName" -}}
{{- if .Values.serviceAccount.create }}
{{- default (include "poundcake.fullname" .) .Values.serviceAccount.name }}
{{- else }}
{{- default "default" .Values.serviceAccount.name }}
{{- end }}
{{- end }}

{{/*
Get the secret name for StackStorm credentials
*/}}
{{- define "poundcake.stackstormSecretName" -}}
{{- if .Values.stackstorm.existingSecret }}
{{- .Values.stackstorm.existingSecret }}
{{- else }}
{{- include "poundcake.fullname" . }}-stackstorm
{{- end }}
{{- end }}

{{/*
Get StackStorm MongoDB resource name
*/}}
{{- define "poundcake.stackstormMongoName" -}}
{{- .Values.stackstorm.resourceNames.mongodb | default "st2-mongodb" }}
{{- end }}

{{/*
Get StackStorm RabbitMQ resource name
*/}}
{{- define "poundcake.stackstormRabbitmqName" -}}
{{- .Values.stackstorm.resourceNames.rabbitmq | default "st2-rabbitmq" }}
{{- end }}

{{/*
Get StackStorm Redis resource name
*/}}
{{- define "poundcake.stackstormRedisName" -}}
{{- .Values.stackstorm.resourceNames.redis | default "st2-redis" }}
{{- end }}

{{/*
Get the secret name for Redis credentials
*/}}
{{- define "poundcake.redisSecretName" -}}
{{- if .Values.redis.existingSecret }}
{{- .Values.redis.existingSecret }}
{{- else if and (not .Values.redis.deploy) .Values.redis.external.existingSecret }}
{{- .Values.redis.external.existingSecret }}
{{- else }}
{{- include "poundcake.stackstormRedisName" . }}
{{- end }}
{{- end }}

{{/*
Get the secret name for RabbitMQ credentials
*/}}
{{- define "poundcake.rabbitmqSecretName" -}}
{{- if .Values.rabbitmq.existingSecret }}
{{- .Values.rabbitmq.existingSecret }}
{{- else }}
{{- include "poundcake.stackstormRabbitmqName" . }}
{{- end }}
{{- end }}

{{/*
Get the Redis URL
*/}}
{{- define "poundcake.redisUrl" -}}
{{- if .Values.redis.deploy }}
{{- printf "redis://%s:6379/0" (include "poundcake.stackstormRedisName" .) }}
{{- else }}
{{- .Values.redis.external.url }}
{{- end }}
{{- end }}

{{/*
Get the secret name for Git credentials
*/}}
{{- define "poundcake.gitSecretName" -}}
{{- if .Values.git.existingSecret }}
{{- .Values.git.existingSecret }}
{{- else }}
{{- include "poundcake.fullname" . }}-git
{{- end }}
{{- end }}

{{/*
Get the Celery broker URL
*/}}
{{- define "poundcake.celeryBrokerUrl" -}}
{{- if .Values.celery.brokerUrl }}
{{- .Values.celery.brokerUrl }}
{{- else if .Values.redis.enabled }}
{{- include "poundcake.redisUrl" . }}
{{- else }}
{{- "redis://localhost:6379/0" }}
{{- end }}
{{- end }}

{{/*
Get the Celery result backend URL
*/}}
{{- define "poundcake.celeryResultBackend" -}}
{{- if .Values.celery.resultBackend }}
{{- .Values.celery.resultBackend }}
{{- else if .Values.redis.enabled }}
{{- include "poundcake.redisUrl" . }}
{{- else }}
{{- "redis://localhost:6379/0" }}
{{- end }}
{{- end }}

{{/*
MariaDB Operator helpers
*/}}

{{/*
Get the MariaDB instance name
*/}}
{{- define "poundcake.mariadbName" -}}
{{- if .Values.mariadbOperator.server.name }}
{{- .Values.mariadbOperator.server.name }}
{{- else }}
{{- include "poundcake.fullname" . }}-mariadb
{{- end }}
{{- end }}

{{/*
Get the MariaDB namespace
*/}}
{{- define "poundcake.mariadbNamespace" -}}
{{- if .Values.mariadbOperator.namespace }}
{{- .Values.mariadbOperator.namespace }}
{{- else }}
{{- .Release.Namespace }}
{{- end }}
{{- end }}

{{/*
Get the MariaDB root password secret name
*/}}
{{- define "poundcake.mariadbRootSecretName" -}}
{{- if .Values.mariadbOperator.server.rootPasswordSecret }}
{{- .Values.mariadbOperator.server.rootPasswordSecret }}
{{- else }}
{{- include "poundcake.mariadbName" . }}-root
{{- end }}
{{- end }}

{{/*
Get the MariaDB user password secret name
*/}}
{{- define "poundcake.mariadbUserSecretName" -}}
{{- if .Values.mariadbOperator.user.passwordSecret }}
{{- .Values.mariadbOperator.user.passwordSecret }}
{{- else }}
{{- include "poundcake.fullname" . }}-mariadb-user
{{- end }}
{{- end }}

{{/*
Get the MariaDB database name
*/}}
{{- define "poundcake.mariadbDatabaseName" -}}
{{- .Values.mariadbOperator.database.name | default "poundcake" }}
{{- end }}

{{/*
Get the MariaDB username
*/}}
{{- define "poundcake.mariadbUsername" -}}
{{- .Values.mariadbOperator.user.name | default "poundcake" }}
{{- end }}

{{/*
Get the MariaDB service host
*/}}
{{- define "poundcake.mariadbHost" -}}
{{- $namespace := include "poundcake.mariadbNamespace" . }}
{{- $name := include "poundcake.mariadbName" . }}
{{- printf "%s.%s.svc.cluster.local" $name $namespace }}
{{- end }}

{{/*
Check if MariaDB Operator CRDs are available
*/}}
{{- define "poundcake.mariadbOperatorAvailable" -}}
{{- if .Capabilities.APIVersions.Has "k8s.mariadb.com/v1alpha1" }}
{{- true }}
{{- else }}
{{- false }}
{{- end }}
{{- end }}

{{/*
Get the database URL - prioritizes explicit config over operator
*/}}
{{- define "poundcake.databaseUrl" -}}
{{- if .Values.database.url }}
{{- .Values.database.url }}
{{- else if .Values.database.existingSecret }}
{{- /* Will be loaded from secret */ -}}
{{- else if .Values.mariadbOperator.enabled }}
{{- $host := include "poundcake.mariadbHost" . }}
{{- $db := include "poundcake.mariadbDatabaseName" . }}
{{- $user := include "poundcake.mariadbUsername" . }}
{{- /* Password will be injected from secret, this is just for reference */ -}}
{{- printf "mysql+pymysql://%s:$(MARIADB_PASSWORD)@%s:3306/%s" $user $host $db }}
{{- end }}
{{- end }}

{{/*
Get StackStorm API URL - either from subchart service or external URL
*/}}
{{- define "poundcake.stackstormSubchartPrefix" -}}
{{- $stackstormChart := (index .Values "stackstorm-chart") | default dict -}}
{{- $chartFullnameOverride := index $stackstormChart "fullnameOverride" -}}
{{- $legacyFullnameOverride := .Values.stackstorm.subchart.fullnameOverride -}}
{{- coalesce $chartFullnameOverride $legacyFullnameOverride .Release.Name }}
{{- end }}

{{/*
Get StackStorm auth secret name from subchart
*/}}
{{- define "poundcake.stackstormAuthSecretName" -}}
{{- printf "%s-st2-auth" (include "poundcake.stackstormSubchartPrefix" .) }}
{{- end }}

{{- define "poundcake.stackstormApiUrl" -}}
{{- printf "http://%s-st2api.%s.svc.cluster.local:9101" (include "poundcake.stackstormSubchartPrefix" .) .Release.Namespace }}
{{- end }}

{{/*
Get StackStorm Auth URL - either from subchart service or external URL
*/}}
{{- define "poundcake.stackstormAuthUrl" -}}
{{- printf "http://%s-st2auth.%s.svc.cluster.local:9100" (include "poundcake.stackstormSubchartPrefix" .) .Release.Namespace }}
{{- end }}

{{/*
Get StackStorm API key secret name
*/}}
{{- define "poundcake.stackstormApiKeySecret" -}}
{{- .Values.stackstorm.subchart.apiKeySecretName | default (printf "%s-st2-apikeys" (include "poundcake.stackstormSubchartPrefix" .)) }}
{{- end }}

{{/*
Get StackStorm API key secret key
*/}}
{{- define "poundcake.stackstormApiKeySecretKey" -}}
{{- .Values.stackstorm.subchart.apiKeySecretKey | default "st2_api_key" }}
{{- end }}

{{/*
Marker secret written by stackstorm-bootstrap when StackStorm initialization is complete.
*/}}
{{- define "poundcake.stackstormBootstrapReadySecret" -}}
{{- printf "%s-stackstorm-bootstrap-ready" (include "poundcake.fullname" .) }}
{{- end }}

{{/*
Marker secret written by poundcake-bootstrap when PoundCake initialization is complete.
*/}}
{{- define "poundcake.poundcakeBootstrapReadySecret" -}}
{{- printf "%s-poundcake-bootstrap-ready" (include "poundcake.fullname" .) }}
{{- end }}

{{/*
Init container that blocks worker services until PoundCake bootstrap is complete.
*/}}
{{- define "poundcake.waitForPoundcakeBootstrapInitContainer" -}}
- name: wait-for-poundcake-bootstrap
  image: bitnami/kubectl:1.30
  imagePullPolicy: IfNotPresent
  command:
    - /bin/sh
    - -c
    - |
      set -e
      NS="{{ .Release.Namespace }}"
      READY_SECRET="{{ include "poundcake.poundcakeBootstrapReadySecret" . }}"
      MAX_WAIT=900
      ELAPSED=0
      INTERVAL=5

      echo "Waiting for PoundCake bootstrap marker secret: ${READY_SECRET}"

      while [ "${ELAPSED}" -lt "${MAX_WAIT}" ]; do
        if kubectl -n "${NS}" get secret "${READY_SECRET}" >/dev/null 2>&1; then
          echo "PoundCake bootstrap complete."
          exit 0
        fi
        sleep "${INTERVAL}"
        ELAPSED=$((ELAPSED + INTERVAL))
      done

      echo "Timed out waiting for PoundCake bootstrap completion."
      exit 1
{{- end }}

{{/*
Init container that blocks PoundCake components until StackStorm bootstrap is complete.
*/}}
{{- define "poundcake.waitForStackstormBootstrapInitContainer" -}}
- name: wait-for-stackstorm-bootstrap
  image: bitnami/kubectl:1.30
  imagePullPolicy: IfNotPresent
  command:
    - /bin/sh
    - -c
    - |
      set -e
      NS="{{ .Release.Namespace }}"
      READY_SECRET="{{ include "poundcake.stackstormBootstrapReadySecret" . }}"
      API_KEY_SECRET="{{ include "poundcake.stackstormApiKeySecret" . }}"
      API_KEY_FIELD="{{ include "poundcake.stackstormApiKeySecretKey" . }}"
      MAX_WAIT=900
      ELAPSED=0
      INTERVAL=5

      echo "Waiting for StackStorm bootstrap marker secret: ${READY_SECRET}"
      echo "Waiting for StackStorm API key secret: ${API_KEY_SECRET}"

      while [ "${ELAPSED}" -lt "${MAX_WAIT}" ]; do
        api_key_value="$(kubectl -n "${NS}" get secret "${API_KEY_SECRET}" -o "jsonpath={.data['${API_KEY_FIELD}']}" 2>/dev/null || true)"
        if kubectl -n "${NS}" get secret "${READY_SECRET}" >/dev/null 2>&1 && \
           [ -n "${api_key_value}" ]; then
          echo "StackStorm bootstrap prerequisites satisfied."
          exit 0
        fi
        sleep "${INTERVAL}"
        ELAPSED=$((ELAPSED + INTERVAL))
      done

      echo "Timed out waiting for StackStorm bootstrap completion."
      exit 1
{{- end }}

{{/*
Get bakery MariaDB root secret name
*/}}
{{- define "poundcake.bakeryMariadbRootSecretName" -}}
{{- .Values.mariadbOperator.server.rootPasswordSecret | default (printf "%s-bakery-mariadb-root" (include "poundcake.fullname" .)) }}
{{- end }}

{{/*
Get bakery DB user password secret name
*/}}
{{- define "poundcake.bakeryDbSecretName" -}}
{{- .Values.bakery.database.user.passwordSecret | default (printf "%s-bakery-db" (include "poundcake.fullname" .)) }}
{{- end }}

{{/*
Standard annotations for chart-managed secrets that must exist before workloads start.
*/}}
{{- define "poundcake.preInstallSecretAnnotations" -}}
"helm.sh/hook": pre-install,pre-upgrade
"helm.sh/hook-weight": "-20"
"helm.sh/hook-delete-policy": before-hook-creation
{{- end }}

{{/*
Init container that blocks PoundCake API until MariaDB service endpoints exist.
*/}}
{{- define "poundcake.waitForMariadbInitContainer" -}}
- name: wait-for-mariadb
  image: bitnami/kubectl:1.30
  imagePullPolicy: IfNotPresent
  command:
    - /bin/sh
    - -c
    - |
      set -e
      NS="{{ include "poundcake.mariadbNamespace" . }}"
      DB_SVC="{{ include "poundcake.mariadbName" . }}"
      MAX_WAIT=900
      ELAPSED=0
      INTERVAL=5

      echo "Waiting for MariaDB service endpoints: ${DB_SVC}.${NS}"
      while [ "${ELAPSED}" -lt "${MAX_WAIT}" ]; do
        endpoint="$(kubectl -n "${NS}" get endpoints "${DB_SVC}" -o jsonpath='{.subsets[0].addresses[0].ip}' 2>/dev/null || true)"
        if [ -n "${endpoint}" ]; then
          echo "MariaDB endpoint ready: ${endpoint}"
          exit 0
        fi
        sleep "${INTERVAL}"
        ELAPSED=$((ELAPSED + INTERVAL))
      done

      echo "Timed out waiting for MariaDB endpoints."
      exit 1
{{- end }}
