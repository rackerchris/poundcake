{{- define "poundcake.name" -}}
{{- default .Chart.Name .Values.nameOverride | trunc 63 | trimSuffix "-" -}}
{{- end -}}

{{- define "poundcake.fullname" -}}
{{- if .Values.fullnameOverride -}}
{{- .Values.fullnameOverride | trunc 63 | trimSuffix "-" -}}
{{- else -}}
{{- $name := include "poundcake.name" . -}}
{{- if contains $name .Release.Name -}}
{{- .Release.Name | trunc 63 | trimSuffix "-" -}}
{{- else -}}
{{- printf "%s-%s" .Release.Name $name | trunc 63 | trimSuffix "-" -}}
{{- end -}}
{{- end -}}
{{- end -}}

{{- define "poundcake.labels" -}}
helm.sh/chart: {{ .Chart.Name }}-{{ .Chart.Version | replace "+" "_" }}
app.kubernetes.io/name: {{ include "poundcake.name" . }}
app.kubernetes.io/instance: {{ .Release.Name }}
app.kubernetes.io/version: {{ .Chart.AppVersion | quote }}
app.kubernetes.io/managed-by: {{ .Release.Service }}
{{- end -}}

{{- define "poundcake.selectorLabels" -}}
app.kubernetes.io/name: {{ include "poundcake.name" . }}
app.kubernetes.io/instance: {{ .Release.Name }}
{{- end -}}

{{- define "poundcake.serviceAccountName" -}}
{{- if .Values.serviceAccount.create -}}
{{- default (include "poundcake.fullname" .) .Values.serviceAccount.name -}}
{{- else -}}
{{- default "default" .Values.serviceAccount.name -}}
{{- end -}}
{{- end -}}

{{- define "poundcake.apiServiceUrl" -}}
{{- printf "http://poundcake-api.%s.svc.cluster.local:%v" .Release.Namespace .Values.services.api.port -}}
{{- end -}}

{{- define "poundcake.prometheusServiceUrl" -}}
{{- required "monitoring.prometheus.url is required when monitoring.enabled=true" .Values.monitoring.prometheus.url -}}
{{- end -}}

{{- define "poundcake.alertmanagerServiceUrl" -}}
{{- required "monitoring.alertmanager.url is required when monitoring.enabled=true" .Values.monitoring.alertmanager.url -}}
{{- end -}}

{{- define "poundcake.monitoringEnv" -}}
{{- if .Values.monitoring.enabled }}
- name: POUNDCAKE_PROMETHEUS_URL
  value: {{ include "poundcake.prometheusServiceUrl" . | quote }}
- name: POUNDCAKE_PROMETHEUS_CRD_NAMESPACE
  value: {{ .Values.monitoring.prometheus.crdNamespace | quote }}
- name: POUNDCAKE_ALERTMANAGER_URL
  value: {{ include "poundcake.alertmanagerServiceUrl" . | quote }}
{{- end }}
{{- end -}}

{{- define "poundcake.stackstormEnv" -}}
{{- if .Values.stackstorm.url }}
- name: POUNDCAKE_STACKSTORM_URL
  value: {{ .Values.stackstorm.url | quote }}
- name: POUNDCAKE_STACKSTORM_VERIFY_SSL
  value: {{ .Values.stackstorm.verifySsl | quote }}
{{- end }}
{{- end -}}

{{- define "poundcake.bakeryEnv" -}}
{{- if .Values.bakery.client.enabled }}
- name: POUNDCAKE_BAKERY_BASE_URL
  value: {{ required "bakery.client.baseUrl is required when bakery.client.enabled=true" .Values.bakery.client.baseUrl | quote }}
- name: POUNDCAKE_BAKERY_TLS_VERIFY
  value: {{ .Values.bakery.client.verifySsl | quote }}
- name: POUNDCAKE_BAKERY_REQUEST_TIMEOUT_SECONDS
  value: {{ .Values.bakery.client.requestTimeoutSeconds | quote }}
- name: POUNDCAKE_BAKERY_MAX_RETRIES
  value: {{ .Values.bakery.client.maxRetries | quote }}
- name: POUNDCAKE_BAKERY_POLL_INTERVAL_SECONDS
  value: {{ .Values.bakery.client.pollIntervalSeconds | quote }}
- name: POUNDCAKE_BAKERY_POLL_TIMEOUT_SECONDS
  value: {{ .Values.bakery.client.pollTimeoutSeconds | quote }}
- name: POUNDCAKE_BAKERY_ALLOW_INSECURE_HTTP
  value: {{ .Values.bakery.client.allowInsecureHttp | quote }}
- name: POUNDCAKE_BAKERY_PLUGIN_ID
  value: {{ .Values.bakery.client.pluginId | quote }}
- name: POUNDCAKE_BAKERY_ACTIVE_PROVIDER
  value: {{ .Values.bakery.config.activeProvider | quote }}
- name: POUNDCAKE_BAKERY_PLUGIN_ENVIRONMENT_LABEL
  value: {{ .Values.bakery.client.monitor.environmentLabel | quote }}
- name: POUNDCAKE_BAKERY_PLUGIN_REGION
  value: {{ .Values.bakery.client.monitor.region | quote }}
- name: POUNDCAKE_BAKERY_PLUGIN_CLUSTER_NAME
  value: {{ .Values.bakery.client.monitor.clusterName | quote }}
- name: POUNDCAKE_BAKERY_PLUGIN_NAMESPACE
  value: {{ .Release.Namespace | quote }}
- name: POUNDCAKE_BAKERY_PLUGIN_RELEASE_NAME
  value: {{ .Release.Name | quote }}
- name: POUNDCAKE_BAKERY_PLUGIN_TAGS
  value: {{ join "," .Values.bakery.client.monitor.tags | quote }}
{{- end }}
{{- end }}

{{- define "poundcake.validateUniqueUrlServicePorts" -}}
{{- $urlServices := list
  (dict "name" "services.api.port" "port" (int .Values.services.api.port))
  (dict "name" "services.ui.port" "port" (int .Values.services.ui.port))
-}}
{{- $seen := dict -}}
{{- range $service := $urlServices -}}
{{- $name := get $service "name" -}}
{{- $port := get $service "port" -}}
{{- $key := printf "%d" $port -}}
{{- if hasKey $seen $key -}}
{{- fail (printf "URL-addressable service ports must be unique. %s and %s both use port %d." (get $seen $key) $name $port) -}}
{{- end -}}
{{- $_ := set $seen $key $name -}}
{{- end -}}
{{- end -}}

{{- define "poundcake.enabledPlugins" -}}
{{- $configured := .Values.config.enabledPlugins | default "dummy" -}}
{{- if and .Values.bakery.client.enabled (not (has "bakery" (splitList "," $configured))) -}}
{{- printf "%s,bakery" $configured -}}
{{- else -}}
{{- $configured -}}
{{- end -}}
{{- end -}}

{{- define "poundcake.databaseMode" -}}
{{- $database := .Values.database | default dict -}}
{{- $mode := $database.mode | default "embedded" -}}
{{- if eq $mode "shared_operator" -}}
shared_operator
{{- else -}}
embedded
{{- end -}}
{{- end -}}

{{- define "poundcake.databaseServerName" -}}
{{- if eq (include "poundcake.databaseMode" .) "shared_operator" -}}
{{- .Values.database.sharedOperator.serverName | default "" -}}
{{- else -}}
poundcake-mariadb
{{- end -}}
{{- end -}}

{{- define "poundcake.databaseServiceNamespace" -}}
{{- if eq (include "poundcake.databaseMode" .) "shared_operator" -}}
{{- .Values.database.sharedOperator.namespace | default .Release.Namespace -}}
{{- else -}}
{{- .Release.Namespace -}}
{{- end -}}
{{- end -}}

{{- define "poundcake.databaseHost" -}}
{{- $mode := include "poundcake.databaseMode" . -}}
{{- $serverName := include "poundcake.databaseServerName" . -}}
{{- $namespace := include "poundcake.databaseServiceNamespace" . -}}
{{- if eq $mode "shared_operator" -}}
  {{- if and $serverName (ne $namespace .Release.Namespace) -}}
{{ printf "%s.%s.svc.cluster.local" $serverName $namespace }}
  {{- else -}}
{{ $serverName }}
  {{- end -}}
{{- else -}}
poundcake-mariadb
{{- end -}}
{{- end -}}

{{- define "poundcake.secretChecksumMaterial" -}}
{{- $material := dict
  "databaseMode" (include "poundcake.databaseMode" .)
  "databaseHost" (include "poundcake.databaseHost" .)
  "secrets" (.Values.secrets | default dict)
  "auth" (.Values.auth | default dict)
  "bakery" (.Values.bakery | default dict)
-}}
{{ toYaml $material }}
{{- end -}}

{{- define "poundcake.logGroupLabel" -}}
poundcake.io/log-group: "poundcake"
{{- end -}}

{{- define "poundcake.logRoleApi" -}}
poundcake.io/log-subgroup: "app"
poundcake.io/log-role: "api"
{{- end -}}

{{- define "poundcake.logRoleWorker" -}}
poundcake.io/log-subgroup: "app"
poundcake.io/log-role: "worker"
{{- end -}}

{{- define "poundcake.logRoleInfra" -}}
poundcake.io/log-subgroup: "data"
poundcake.io/log-role: "infra"
{{- end -}}

{{- define "poundcake.storageClass" -}}
{{- if .Values.persistence.storageClassName }}
storageClassName: {{ .Values.persistence.storageClassName | quote }}
{{- end }}
{{- end -}}

{{- define "poundcake.poundcakePullSecrets" -}}
{{- $pullSecrets := .Values.poundcakeImage.pullSecrets | default list -}}
{{- if gt (len $pullSecrets) 0 }}
imagePullSecrets:
{{- range $secret := $pullSecrets }}
  {{- if kindIs "string" $secret }}
  - name: {{ $secret | quote }}
  {{- else if and (kindIs "map" $secret) (hasKey $secret "name") }}
  - name: {{ index $secret "name" | quote }}
{{- end }}
{{- end }}
{{- end }}
{{- end -}}

{{- define "poundcake.podPlacement" -}}
{{- with .Values.nodeSelector }}
nodeSelector:
  {{- toYaml . | nindent 2 }}
{{- end }}
{{- with .Values.affinity }}
affinity:
  {{- toYaml . | nindent 2 }}
{{- end }}
{{- with .Values.tolerations }}
tolerations:
  {{- toYaml . | nindent 2 }}
{{- end }}
{{- end -}}

{{- define "poundcake.poundcakeImageRef" -}}
{{- $digest := .Values.poundcakeImage.digest | default "" -}}
{{- if $digest -}}
{{- printf "%s@%s" .Values.poundcakeImage.repository $digest -}}
{{- else -}}
{{- printf "%s:%s" .Values.poundcakeImage.repository (default .Chart.AppVersion .Values.poundcakeImage.tag) -}}
{{- end -}}
{{- end -}}

{{- define "poundcake.poundcakeImageVersion" -}}
{{- $digest := .Values.poundcakeImage.digest | default "" -}}
{{- if $digest -}}
{{- $digest -}}
{{- else -}}
{{- default .Chart.AppVersion .Values.poundcakeImage.tag -}}
{{- end -}}
{{- end -}}

{{- define "poundcake.pvcStorageClass" -}}
{{- $root := .root -}}
{{- $pvcStorageClass := .pvcStorageClass | default "" -}}
{{- if $pvcStorageClass }}
storageClassName: {{ $pvcStorageClass | quote }}
{{- else if $root.Values.persistence.storageClassName }}
storageClassName: {{ $root.Values.persistence.storageClassName | quote }}
{{- end }}
{{- end -}}

{{- define "poundcake.startupHookDeletePolicy" -}}
{{- $policies := list "before-hook-creation" -}}
{{- if and .Values.startupHooks.cleanup.enabled .Values.startupHooks.cleanup.deleteSuccessful -}}
{{- $policies = append $policies "hook-succeeded" -}}
{{- end -}}
{{- if and .Values.startupHooks.cleanup.enabled .Values.startupHooks.cleanup.deleteFailed -}}
{{- $policies = append $policies "hook-failed" -}}
{{- end -}}
{{- join "," $policies -}}
{{- end -}}

{{- define "poundcake.logLabels" -}}
{{- $group := .group | default "other" -}}
{{- $subgroup := .subgroup | default "general" -}}
{{- $role := .role | default "other" -}}
poundcake.io/log-group: {{ $group | quote }}
poundcake.io/log-subgroup: {{ $subgroup | quote }}
poundcake.io/log-role: {{ $role | quote }}
{{- end -}}

{{- define "poundcake.logLabelsForComponent" -}}
{{- $component := .component | default "unknown" -}}
{{- $group := "other" -}}
{{- $subgroup := "general" -}}
{{- $role := $component -}}

{{- if has $component (list "api" "ui" "prep-chef" "expediter-runner" "timer" "dishwasher") -}}
  {{- $group = "poundcake" -}}
  {{- $subgroup = "app" -}}
  {{- if eq $component "api" -}}
    {{- $role = "api" -}}
  {{- else if eq $component "ui" -}}
    {{- $role = "ui" -}}
  {{- else -}}
    {{- $role = "worker" -}}
  {{- end -}}
{{- else if eq $component "mariadb" -}}
  {{- $group = "infra" -}}
  {{- $subgroup = "data" -}}
{{- else if hasPrefix "poundcake-" $component -}}
  {{- $group = "startup-hooks" -}}
  {{- $subgroup = "orchestration" -}}
{{- end -}}

{{- include "poundcake.logLabels" (dict "group" $group "subgroup" $subgroup "role" $role) -}}
{{- end -}}

{{- define "poundcake.gateLogHelpers" -}}
GATE_LOG_ENABLED="{{ ternary "true" "false" .Values.startupHooks.gateLogging.enabled }}"
GATE_LOG_INTERVAL="{{ .Values.startupHooks.gateLogging.intervalSeconds }}"
GATE_LOG_PREFIX={{ .Values.startupHooks.gateLogging.prefix | quote }}
case "${GATE_LOG_INTERVAL}" in
  ''|*[!0-9]*) GATE_LOG_INTERVAL=30 ;;
esac
if [ "${GATE_LOG_INTERVAL}" -lt 1 ]; then
  GATE_LOG_INTERVAL=1
fi

gate_log_wait_start() {
  gate_name="$1"
  gate_detail="$2"
  gate_started_at="$(date +%s)"
  gate_last_log="${gate_started_at}"
  echo "${GATE_LOG_PREFIX} wait=${gate_name} status=waiting elapsed=0s detail=${gate_detail}"
}

gate_log_wait_tick() {
  gate_name="$1"
  gate_detail="$2"
  gate_now="$(date +%s)"
  if [ "${GATE_LOG_ENABLED}" = "true" ] && [ $((gate_now - gate_last_log)) -ge "${GATE_LOG_INTERVAL}" ]; then
    echo "${GATE_LOG_PREFIX} wait=${gate_name} status=waiting elapsed=$((gate_now - gate_started_at))s detail=${gate_detail}"
    gate_last_log="${gate_now}"
  fi
}

gate_log_wait_ready() {
  gate_name="$1"
  gate_now="$(date +%s)"
  echo "${GATE_LOG_PREFIX} wait=${gate_name} status=ready elapsed=$((gate_now - gate_started_at))s"
}

gate_wait_for_true_file() {
  gate_file="$1"
  gate_name="$2"
  gate_detail="$3"
  gate_log_wait_start "${gate_name}" "${gate_detail}"
  until [ "$(cat "${gate_file}" 2>/dev/null)" = "true" ]; do
    gate_log_wait_tick "${gate_name}" "${gate_detail}"
    sleep 2
  done
  gate_log_wait_ready "${gate_name}"
}

gate_wait_for_nonempty_file() {
  gate_file="$1"
  gate_name="$2"
  gate_detail="$3"
  gate_log_wait_start "${gate_name}" "${gate_detail}"
  until [ -n "$(cat "${gate_file}" 2>/dev/null)" ]; do
    gate_log_wait_tick "${gate_name}" "${gate_detail}"
    sleep 2
  done
  gate_log_wait_ready "${gate_name}"
}

gate_wait_for_tcp() {
  gate_host="$1"
  gate_port="$2"
  gate_name="$3"
  gate_detail="$4"
  gate_log_wait_start "${gate_name}" "${gate_detail}"
  until nc -z "${gate_host}" "${gate_port}"; do
    gate_log_wait_tick "${gate_name}" "${gate_detail}"
    sleep 2
  done
  gate_log_wait_ready "${gate_name}"
}

gate_wait_for_http_status() {
  gate_url="$1"
  gate_name="$2"
  gate_detail="$3"
  shift 3
  gate_log_wait_start "${gate_name}" "${gate_detail}"
  while true; do
    gate_resp="$(wget -S -O /dev/null "${gate_url}" 2>&1 || true)"
    for gate_code in "$@"; do
      case "${gate_resp}" in
        *" ${gate_code} "*)
          gate_log_wait_ready "${gate_name}"
          return 0
          ;;
      esac
    done
    gate_log_wait_tick "${gate_name}" "${gate_detail}"
    sleep 2
  done
}
{{- end -}}
