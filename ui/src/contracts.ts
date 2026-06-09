import { z } from "zod";

const strictObject = <T extends z.ZodRawShape>(shape: T) => z.object(shape).strict();

const providerNameSchema = z.enum(["local", "active_directory", "auth0", "azure_ad", "service"]);
const roleSchema = z.enum(["reader", "operator", "admin", "service"]);
const userRoleSchema = z.enum(["reader", "operator", "admin"]);
const principalTypeSchema = z.enum(["user", "service"]);
const bindingTypeSchema = z.enum(["user", "group"]);
const communicationsModeSchema = z.enum(["inherit", "local"]);
const unknownRecordSchema = z.record(z.unknown());
const stringRecordSchema = z.record(z.string());
const numberRecordSchema = z.record(z.number());
const repoSyncExportValueSchema = z.union([z.string(), z.number(), z.null()]);
const pluginHealthStatusSchema = z.enum(["unknown", "initializing", "healthy", "degraded", "failed", "disabled"]);
const pluginTierSchema = z.enum(["community", "supported"]);
const orderTypeSchema = z.enum(["webhook_alert", "scheduled_task", "manual"]);
const isRecord = (value: unknown): value is Record<string, unknown> =>
  typeof value === "object" && value !== null && !Array.isArray(value);

export const authProviderRecordSchema = strictObject({
  name: providerNameSchema,
  label: z.string(),
  login_mode: z.string(),
  cli_login_mode: z.string(),
  browser_login: z.boolean(),
  device_login: z.boolean(),
  password_login: z.boolean(),
});
export type AuthProviderRecord = z.infer<typeof authProviderRecordSchema>;
export const authProviderRecordArraySchema = z.array(authProviderRecordSchema);

export const authMeRecordSchema = strictObject({
  username: z.string(),
  display_name: z.string().nullable().optional(),
  provider: providerNameSchema,
  role: roleSchema,
  principal_type: principalTypeSchema,
  principal_id: z.number().int().nullable().optional(),
  is_superuser: z.boolean(),
  permissions: z.array(z.string()),
  groups: z.array(z.string()),
  expires_at: z.string().nullable().optional(),
});
export type AuthMeRecord = z.infer<typeof authMeRecordSchema>;

export const authPrincipalRecordSchema = strictObject({
  id: z.number().int(),
  provider: providerNameSchema,
  subject_id: z.string(),
  username: z.string(),
  display_name: z.string().nullable().optional(),
  principal_type: principalTypeSchema,
  groups: z.array(z.string()),
  last_seen_at: z.string(),
  created_at: z.string(),
  updated_at: z.string(),
});
export type AuthPrincipalRecord = z.infer<typeof authPrincipalRecordSchema>;
export const authPrincipalRecordArraySchema = z.array(authPrincipalRecordSchema);

export const authRoleBindingRecordSchema = strictObject({
  id: z.number().int(),
  provider: providerNameSchema,
  binding_type: bindingTypeSchema,
  role: roleSchema,
  principal_id: z.number().int().nullable().optional(),
  external_group: z.string().nullable().optional(),
  created_by: z.string().nullable().optional(),
  created_at: z.string(),
  updated_at: z.string(),
  principal: authPrincipalRecordSchema.nullable().optional(),
});
export type AuthRoleBindingRecord = z.infer<typeof authRoleBindingRecordSchema>;
export const authRoleBindingRecordArraySchema = z.array(authRoleBindingRecordSchema);

export const appSettingsSchema = strictObject({
  auth_enabled: z.boolean(),
  rbac_enabled: z.boolean(),
  auth_providers: authProviderRecordArraySchema,
  prometheus_use_crds: z.boolean(),
  prometheus_crd_namespace: z.string(),
  prometheus_url: z.string(),
  git_provider: z.string().nullable(),
  git_repo_url: z.string().nullable(),
  git_branch: z.string().nullable(),
  git_rules_path: z.string().nullable(),
  git_workflows_path: z.string().nullable(),
  git_actions_path: z.string().nullable(),
  version: z.string(),
  global_communications_configured: z.boolean(),
});
export type AppSettings = z.infer<typeof appSettingsSchema>;

export const servicePluginSummaryRecordSchema = strictObject({
  service_type: z.string(),
  plugin_short_id: z.string().nullable().optional(),
  plugin_type: z.enum(["internal_plugin", "external_plugin"]),
  plugin_tier: pluginTierSchema,
  plugin_log_key: z.string().nullable().optional(),
  enabled: z.boolean(),
  run_interval_seconds: z.number().int().nullable().optional(),
  query_limit: z.number().int().nullable().optional(),
  status_message: z.string().nullable().optional(),
  config_editable: z.boolean(),
  ingredient_template_count: z.number().int(),
  recipe_template_count: z.number().int(),
  credential_status: z.string(),
  credential_error: z.string().nullable().optional(),
  last_credential_bootstrap_at: z.string().nullable().optional(),
  last_credential_rotation_at: z.string().nullable().optional(),
  health_status: pluginHealthStatusSchema,
  health_message: z.string().nullable().optional(),
  health_error_code: z.string().nullable().optional(),
  health_latency_ms: z.number().int().nullable().optional(),
  last_health_check_at: z.string().nullable().optional(),
  next_health_check_at: z.string().nullable().optional(),
  health_check_task_id: z.number().int().nullable().optional(),
  health_check_interval_seconds: z.number().int().nullable().optional(),
  health_check_enabled: z.boolean().optional(),
  last_success_at: z.string().nullable().optional(),
  consecutive_failures: z.number().int(),
  health_check_state: z.enum(["idle", "queued", "running"]).optional(),
  health_check_order_id: z.number().int().nullable().optional(),
  health_check_started_at: z.string().nullable().optional(),
  health_check_grace_until: z.string().nullable().optional(),
  helper_available: z.boolean(),
  helper_capabilities: z.array(z.string()),
  required_helper_capabilities: z.record(z.array(z.string())),
  missing_helper_capabilities: z.record(z.array(z.string())),
});
export type ServicePluginSummaryRecord = z.infer<typeof servicePluginSummaryRecordSchema>;
export const servicePluginSummaryRecordArraySchema = z.array(servicePluginSummaryRecordSchema);

export const servicePluginConfigurationRecordSchema = strictObject({
  service_type: z.string(),
  config: unknownRecordSchema,
  config_schema: unknownRecordSchema.optional(),
  credential_requirements: z.array(unknownRecordSchema).optional(),
  credential_type: z.string().nullable().optional(),
  credential_key_id: z.string(),
  credential_configured: z.boolean(),
  updated_at: z.string(),
});
export type ServicePluginConfigurationRecord = z.infer<typeof servicePluginConfigurationRecordSchema>;

export const servicePluginActionResponseSchema = strictObject({
  service_type: z.string(),
  status: z.string(),
  message: z.string(),
  details: unknownRecordSchema,
  checked_at: z.string(),
});
export type ServicePluginActionResponse = z.infer<typeof servicePluginActionResponseSchema>;

export const prometheusRuleGroupSummarySchema = strictObject({
  name: z.string(),
  rule_count: z.number().int(),
  alert_count: z.number().int(),
  recording_count: z.number().int(),
  alert_names: z.array(z.string()),
  recording_names: z.array(z.string()),
});
export type PrometheusRuleGroupSummary = z.infer<typeof prometheusRuleGroupSummarySchema>;

export const prometheusRuleResourceRecordSchema = strictObject({
  name: z.string(),
  namespace: z.string(),
  labels: unknownRecordSchema,
  annotations: unknownRecordSchema,
  groups: z.array(prometheusRuleGroupSummarySchema),
  group_count: z.number().int(),
  rule_count: z.number().int(),
  alert_count: z.number().int(),
  recording_count: z.number().int(),
  raw: unknownRecordSchema,
});
export type PrometheusRuleResourceRecord = z.infer<typeof prometheusRuleResourceRecordSchema>;

export const prometheusRuleDetailRecordSchema = strictObject({
  service_type: z.string(),
  name: z.string(),
  namespace: z.string(),
  labels: unknownRecordSchema,
  annotations: unknownRecordSchema,
  groups: z.array(prometheusRuleGroupSummarySchema),
  group_count: z.number().int(),
  rule_count: z.number().int(),
  alert_count: z.number().int(),
  recording_count: z.number().int(),
  raw: unknownRecordSchema,
  checked_at: z.string(),
});
export type PrometheusRuleDetailRecord = z.infer<typeof prometheusRuleDetailRecordSchema>;

export const prometheusRuleRecordSchema = strictObject({
  service_type: z.string(),
  namespace: z.string(),
  crd_name: z.string(),
  group_name: z.string(),
  rule_name: z.string(),
  rule_kind: z.string(),
  source: unknownRecordSchema.nullable().optional(),
  rule_data: unknownRecordSchema,
  checked_at: z.string(),
});
export type PrometheusRuleRecord = z.infer<typeof prometheusRuleRecordSchema>;

export const prometheusRuleListResponseSchema = strictObject({
  service_type: z.string(),
  namespace: z.string(),
  items: z.array(prometheusRuleResourceRecordSchema),
  resource_count: z.number().int(),
  group_count: z.number().int(),
  rule_count: z.number().int(),
  alert_count: z.number().int(),
  recording_count: z.number().int(),
  checked_at: z.string(),
});
export type PrometheusRuleListResponse = z.infer<typeof prometheusRuleListResponseSchema>;

export const scheduledTaskRecordSchema = strictObject({
  id: z.number().int(),
  task_key: z.string(),
  task_type: z.string(),
  service_type: z.string().nullable().optional(),
  service_exec: z.string().nullable().optional(),
  source: z.string(),
  is_enabled: z.boolean(),
  run_interval_seconds: z.number().int(),
  next_run_at: z.string().nullable().optional(),
  priority: z.number().int(),
  timeout_seconds: z.number().int(),
  task_payload: z.record(z.unknown()).nullable().optional(),
  task_parameters: z.record(z.unknown()).nullable().optional(),
  expected_outcome: z.unknown().nullable().optional(),
  status: z.string(),
  last_status: z.string().nullable().optional(),
  last_message: z.string().nullable().optional(),
  last_order_id: z.number().int().nullable().optional(),
  last_order_req_id: z.string().nullable().optional(),
  last_started_at: z.string().nullable().optional(),
  last_completed_at: z.string().nullable().optional(),
  consecutive_failures: z.number().int(),
  created_at: z.string(),
  updated_at: z.string(),
});
export type ScheduledTaskRecord = z.infer<typeof scheduledTaskRecordSchema>;
export const scheduledTaskRecordArraySchema = z.array(scheduledTaskRecordSchema);

export const scheduledTaskStatusRecordSchema = strictObject({
  id: z.number().int(),
  task_key: z.string(),
  task_type: z.string(),
  service_type: z.string().nullable().optional(),
  service_exec: z.string().nullable().optional(),
  source: z.string(),
  is_enabled: z.boolean(),
  run_interval_seconds: z.number().int(),
  next_run_at: z.string().nullable().optional(),
  priority: z.number().int(),
  timeout_seconds: z.number().int(),
  status: z.string(),
  last_status: z.string().nullable().optional(),
  last_message: z.string().nullable().optional(),
  last_order_id: z.number().int().nullable().optional(),
  last_order_req_id: z.string().nullable().optional(),
  last_started_at: z.string().nullable().optional(),
  last_completed_at: z.string().nullable().optional(),
  consecutive_failures: z.number().int(),
  run_now_label: z.string(),
  run_now_description: z.string(),
  created_at: z.string(),
  updated_at: z.string(),
});
export type ScheduledTaskStatusRecord = z.infer<typeof scheduledTaskStatusRecordSchema>;
export const scheduledTaskStatusRecordArraySchema = z.array(scheduledTaskStatusRecordSchema);

const repoSyncPullRequestSchema = strictObject({
  number: z.union([z.number(), z.string(), z.null()]).optional(),
  url: z.string().nullable().optional(),
});

export const repoSyncResponseSchema = strictObject({
  status: z.string(),
  message: z.string(),
  branch: z.string().nullable().optional(),
  pull_request: repoSyncPullRequestSchema.nullable().optional(),
  exported: z.record(repoSyncExportValueSchema).nullable().optional(),
  imported: z.record(z.number()).nullable().optional(),
  skipped: z.record(z.number()).nullable().optional(),
  warnings: z.array(z.string()).nullable().optional(),
  cleared: z.record(z.number()).nullable().optional(),
});
export type RepoSyncResponse = z.infer<typeof repoSyncResponseSchema>;

const communicationRouteRecordNormalizedSchema = strictObject({
  id: z.string(),
  label: z.string(),
  execution_target: z.string(),
  service_type: z.string(),
  destination_target: z.string(),
  provider_config: unknownRecordSchema,
  enabled: z.boolean(),
  position: z.number().int(),
});
export type CommunicationRouteRecord = z.infer<typeof communicationRouteRecordNormalizedSchema>;
export const communicationRouteRecordSchema: z.ZodType<CommunicationRouteRecord> = z.preprocess((input) => {
  if (!isRecord(input)) return input;
  return {
    ...input,
    execution_target: input.execution_target ?? input.service_type ?? "",
    service_type: input.service_type ?? input.execution_target ?? "",
  };
}, communicationRouteRecordNormalizedSchema) as z.ZodType<CommunicationRouteRecord>;

export const communicationPolicyRecordSchema = strictObject({
  configured: z.boolean(),
  routes: z.array(communicationRouteRecordSchema),
  available_routes: z.array(communicationRouteRecordSchema).optional().default([]),
  lifecycle_summary: stringRecordSchema,
});
export type CommunicationPolicyRecord = z.infer<typeof communicationPolicyRecordSchema>;

export const recipeCommunicationsRecordSchema = strictObject({
  mode: communicationsModeSchema,
  effective_source: z.enum(["global", "local"]).nullable().optional(),
  routes: z.array(communicationRouteRecordSchema),
});
export type RecipeCommunicationsRecord = z.infer<typeof recipeCommunicationsRecordSchema>;

export const componentHealthSchema = strictObject({
  status: z.string(),
  message: z.string().nullable().optional(),
  details: unknownRecordSchema.nullable().optional(),
});
export type ComponentHealth = z.infer<typeof componentHealthSchema>;

export const healthResponseSchema = strictObject({
  status: z.string(),
  version: z.string(),
  instance_id: z.string(),
  timestamp: z.string(),
  components: z.record(componentHealthSchema),
});
export type HealthResponse = z.infer<typeof healthResponseSchema>;

export const observabilityOverviewResponseSchema = strictObject({
  health: unknownRecordSchema,
  queue: numberRecordSchema,
  failures: strictObject({
    orders_failed: z.number(),
    dishes_failed: z.number(),
    top_errors: z.array(
      strictObject({
        error: z.string(),
        count: z.number(),
      }),
    ),
    runbook_hints: z.array(z.string()),
  }),
  suppressions: strictObject({
    active: z.number(),
    retrying_operations: z.number(),
    dead_letter: z.number(),
  }),
});
export type ObservabilityOverviewResponse = z.infer<typeof observabilityOverviewResponseSchema>;

export const observabilityActivityRecordSchema = strictObject({
  type: z.string(),
  status: z.string(),
  title: z.string(),
  summary: z.string().nullable().optional(),
  timestamp: z.string().nullable().optional(),
  target_kind: z.string(),
  target_id: z.string(),
  link_hint: z.string().nullable().optional(),
  metadata: unknownRecordSchema,
});
export type ObservabilityActivityRecord = z.infer<typeof observabilityActivityRecordSchema>;
export const observabilityActivityRecordArraySchema = z.array(observabilityActivityRecordSchema);

export const observabilityActivityStatusRecordSchema = strictObject({
  type: z.string(),
  status: z.string(),
  title: z.string(),
  summary: z.string().nullable().optional(),
  timestamp: z.string().nullable().optional(),
  target_kind: z.string(),
  target_id: z.string(),
  link_hint: z.string().nullable().optional(),
});
export type ObservabilityActivityStatusRecord = z.infer<typeof observabilityActivityStatusRecordSchema>;
export const observabilityActivityStatusRecordArraySchema = z.array(observabilityActivityStatusRecordSchema);

export const orderCommunicationSchema = strictObject({
  id: z.number().int(),
  order_id: z.number().int(),
  execution_target: z.string(),
  destination_target: z.string(),
  bakery_ticket_id: z.string().nullable().optional(),
  bakery_operation_id: z.string().nullable().optional(),
  lifecycle_state: z.string(),
  remote_state: z.string().nullable().optional(),
  writable: z.boolean(),
  reopenable: z.boolean(),
  last_error: z.string().nullable().optional(),
  created_at: z.string(),
  updated_at: z.string(),
});
export type OrderCommunication = z.infer<typeof orderCommunicationSchema>;

export const orderResponseSchema = strictObject({
  id: z.number().int(),
  req_id: z.string(),
  fingerprint: z.string(),
  fingerprint_when_active: z.string().nullable().optional(),
  alert_status: z.string(),
  alert_group_name: z.string(),
  processing_status: z.string(),
  is_active: z.boolean(),
  remediation_outcome: z.string(),
  clear_timeout_sec: z.number().nullable().optional(),
  clear_deadline_at: z.string().nullable().optional(),
  clear_timed_out_at: z.string().nullable().optional(),
  auto_close_eligible: z.boolean(),
  severity: z.string().nullable().optional(),
  instance: z.string().nullable().optional(),
  correlation_key: z.string().nullable().optional(),
  counter: z.number(),
  bakery_ticket_id: z.string().nullable().optional(),
  bakery_operation_id: z.string().nullable().optional(),
  bakery_ticket_state: z.string().nullable().optional(),
  bakery_permanent_failure: z.union([z.boolean(), z.undefined()]).transform((value) => value ?? false),
  bakery_last_error: z.string().nullable().optional(),
  bakery_comms_id: z.string().nullable().optional(),
  labels: unknownRecordSchema,
  annotations: unknownRecordSchema.nullable().optional(),
  raw_data: unknownRecordSchema.nullable().optional(),
  starts_at: z.string(),
  ends_at: z.string().nullable().optional(),
  order_lifetime_secs: z.number().int().nullable().optional(),
  communications: z.union([z.array(orderCommunicationSchema), z.undefined()]).transform((value) => value ?? []),
  created_at: z.string(),
  updated_at: z.string(),
});
export type OrderResponse = z.infer<typeof orderResponseSchema>;
export const orderResponseArraySchema = z.array(orderResponseSchema);

export const orderStatusRecordSchema = strictObject({
  id: z.number().int(),
  req_id: z.string(),
  order_type: orderTypeSchema,
  alert_status: z.string(),
  alert_group_name: z.string(),
  processing_status: z.string(),
  is_active: z.boolean(),
  remediation_outcome: z.string(),
  clear_timeout_sec: z.number().nullable().optional(),
  clear_deadline_at: z.string().nullable().optional(),
  clear_timed_out_at: z.string().nullable().optional(),
  auto_close_eligible: z.boolean(),
  severity: z.string().nullable().optional(),
  instance: z.string().nullable().optional(),
  correlation_key: z.string().nullable().optional(),
  counter: z.number(),
  starts_at: z.string(),
  ends_at: z.string().nullable().optional(),
  order_lifetime_secs: z.number().int().nullable().optional(),
  communication_route_count: z.number().int(),
  created_at: z.string(),
  updated_at: z.string(),
});
export type OrderStatusRecord = z.infer<typeof orderStatusRecordSchema>;
export const orderStatusRecordArraySchema = z.array(orderStatusRecordSchema);

export const incidentTimelineEventSchema = strictObject({
  timestamp: z.string().nullable().optional(),
  event_type: z.string(),
  status: z.string(),
  title: z.string(),
  details: unknownRecordSchema,
  correlation_ids: stringRecordSchema,
});
export type IncidentTimelineEvent = z.infer<typeof incidentTimelineEventSchema>;

export const incidentTimelineOrderSchema = orderStatusRecordSchema.extend({
  labels: unknownRecordSchema,
});
export type IncidentTimelineOrderRecord = z.infer<typeof incidentTimelineOrderSchema>;

export const incidentTimelineResponseSchema = strictObject({
  order: incidentTimelineOrderSchema,
  events: z.array(incidentTimelineEventSchema),
});
export type IncidentTimelineResponse = z.infer<typeof incidentTimelineResponseSchema>;

export const communicationActivityRecordSchema = strictObject({
  communication_id: z.string(),
  reference_type: z.string(),
  reference_id: z.string(),
  reference_name: z.string().nullable().optional(),
  channel: z.string(),
  destination: z.string().nullable().optional(),
  ticket_id: z.string().nullable().optional(),
  provider_reference_id: z.string().nullable().optional(),
  operation_id: z.string().nullable().optional(),
  lifecycle_state: z.string().nullable().optional(),
  remote_state: z.string().nullable().optional(),
  last_error: z.string().nullable().optional(),
  writable: z.boolean().nullable().optional(),
  reopenable: z.boolean().nullable().optional(),
  updated_at: z.string().nullable().optional(),
});
export type CommunicationActivityRecord = z.infer<typeof communicationActivityRecordSchema>;
export const communicationActivityRecordArraySchema = z.array(communicationActivityRecordSchema);

export const communicationActivityStatusRecordSchema = strictObject({
  communication_id: z.string(),
  reference_type: z.string(),
  reference_id: z.string(),
  reference_name: z.string().nullable().optional(),
  channel: z.string(),
  destination: z.string().nullable().optional(),
  lifecycle_state: z.string().nullable().optional(),
  remote_state: z.string().nullable().optional(),
  updated_at: z.string().nullable().optional(),
});
export type CommunicationActivityStatusRecord = z.infer<typeof communicationActivityStatusRecordSchema>;
export const communicationActivityStatusRecordArraySchema = z.array(communicationActivityStatusRecordSchema);

export const suppressionMatcherSchema = strictObject({
  label_key: z.string(),
  operator: z.string(),
  value: z.string().nullable().optional(),
});
export type SuppressionMatcher = z.infer<typeof suppressionMatcherSchema>;

export const suppressionRecordSchema = strictObject({
  id: z.number().int(),
  name: z.string(),
  reason: z.string().nullable().optional(),
  scope: z.string(),
  status: z.string(),
  enabled: z.boolean(),
  starts_at: z.string(),
  ends_at: z.string(),
  canceled_at: z.string().nullable().optional(),
  created_by: z.string().nullable().optional(),
  summary_ticket_enabled: z.boolean(),
  source: z.string(),
  source_service_type: z.string().nullable().optional(),
  source_ref: z.string().nullable().optional(),
  source_payload: unknownRecordSchema.nullable().optional(),
  last_synced_at: z.string().nullable().optional(),
  created_at: z.string(),
  updated_at: z.string(),
  matchers: z.array(suppressionMatcherSchema),
});
export type SuppressionRecord = z.infer<typeof suppressionRecordSchema>;
export const suppressionRecordArraySchema = z.array(suppressionRecordSchema);

export const suppressionStatusRecordSchema = strictObject({
  id: z.number().int(),
  name: z.string(),
  reason: z.string().nullable().optional(),
  scope: z.string(),
  status: z.string(),
  enabled: z.boolean(),
  starts_at: z.string(),
  ends_at: z.string(),
  canceled_at: z.string().nullable().optional(),
  source: z.string(),
  source_service_type: z.string().nullable().optional(),
  source_ref: z.string().nullable().optional(),
  last_synced_at: z.string().nullable().optional(),
  created_at: z.string(),
  updated_at: z.string(),
});
export type SuppressionStatusRecord = z.infer<typeof suppressionStatusRecordSchema>;
export const suppressionStatusRecordArraySchema = z.array(suppressionStatusRecordSchema);

const dishRecordNormalizedSchema = strictObject({
  id: z.number().int(),
  req_id: z.string(),
  order_id: z.number().int().nullable().optional(),
  recipe_id: z.number().int(),
  recipe: z
    .object({
      id: z.number().int(),
      name: z.string(),
    })
    .nullable()
    .optional(),
  execution_ref: z.string().nullable().optional(),
  execution_status: z.string().nullable().optional(),
  dish_exec_status: z.string().nullable().optional(),
  processing_status: z.string(),
  run_phase: z.string(),
  expected_duration_sec: z.number().nullable().optional(),
  actual_duration_sec: z.number().nullable().optional(),
  expected_run_secs: z.number().nullable().optional(),
  run_time_secs: z.number().nullable().optional(),
  work_execution_time_secs: z.number().int().nullable().optional(),
  work_execution_groups: z.array(strictObject({
    depth: z.number().int(),
    parallel_group: z.number().int(),
    rows: z.number().int(),
    total_seconds: z.number().int(),
  })).optional(),
  result: z.unknown().nullable().optional(),
  dish_actual_outcome: z.unknown().nullable().optional(),
  error_message: z.string().nullable().optional(),
  retry_attempt: z.number().int().optional(),
  started_at: z.string().nullable().optional(),
  completed_at: z.string().nullable().optional(),
  created_at: z.string(),
  updated_at: z.string(),
});
export type DishRecord = z.infer<typeof dishRecordNormalizedSchema>;
export const dishRecordSchema = z.preprocess((input) => {
  if (!isRecord(input)) return input;
  return {
    ...input,
    execution_status: input.execution_status ?? input.dish_exec_status ?? null,
    expected_duration_sec: input.expected_duration_sec ?? input.expected_run_secs ?? null,
    actual_duration_sec: input.actual_duration_sec ?? input.run_time_secs ?? null,
    work_execution_time_secs: input.work_execution_time_secs ?? null,
    work_execution_groups: input.work_execution_groups ?? [],
    result: input.result ?? input.dish_actual_outcome ?? null,
  };
}, dishRecordNormalizedSchema) as z.ZodType<DishRecord>;
export const dishRecordArraySchema: z.ZodType<DishRecord[]> = z.array(dishRecordSchema);

export const dishStatusRecordSchema = strictObject({
  id: z.number().int(),
  order_id: z.number().int().nullable().optional(),
  order_type: orderTypeSchema,
  recipe_id: z.number().int(),
  recipe_name: z.string().nullable().optional(),
  processing_status: z.string(),
  run_phase: z.string(),
  dish_exec_status: z.string().nullable().optional(),
  started_at: z.string().nullable().optional(),
  completed_at: z.string().nullable().optional(),
  expected_run_secs: z.number().nullable().optional(),
  run_time_secs: z.number().nullable().optional(),
  work_execution_time_secs: z.number().int().nullable().optional(),
  work_execution_groups: z.array(strictObject({
    depth: z.number().int(),
    parallel_group: z.number().int(),
    rows: z.number().int(),
    total_seconds: z.number().int(),
  })).optional(),
  created_at: z.string(),
  updated_at: z.string(),
});
export type DishStatusRecord = z.infer<typeof dishStatusRecordSchema>;
export const dishStatusRecordArraySchema = z.array(dishStatusRecordSchema);

export const dishIngredientRecordSchema = strictObject({
  id: z.number().int(),
  req_id: z.string(),
  dish_id: z.number().int(),
  recipe_ingredient_id: z.number().int().nullable().optional(),
  service_exec_id: z.string().nullable().optional(),
  task_key: z.string().nullable().optional(),
  step_order: z.number().int(),
  parallel_group: z.number().int(),
  depth: z.number().int(),
  service_type: z.string().nullable().optional(),
  service_exec: z.string().nullable().optional(),
  destination_target: z.string().nullable().optional(),
  service_payload: unknownRecordSchema.nullable().optional(),
  service_exec_parameters: unknownRecordSchema.nullable().optional(),
  service_exec_expected_secs: z.number().int().nullable().optional(),
  service_exec_timeout: z.number().int().nullable().optional(),
  service_exec_expected_outcome: z.unknown().nullable().optional(),
  retry_count: z.number().int().nullable().optional(),
  retry_delay: z.number().int().nullable().optional(),
  on_failure: z.string().nullable().optional(),
  service_exec_status: z.string(),
  attempt: z.number().int(),
  service_exec_start_time: z.string().nullable().optional(),
  service_exec_completed_time: z.string().nullable().optional(),
  service_exec_canceled_time: z.string().nullable().optional(),
  service_exec_run_time: z.number().int().nullable().optional(),
  service_exec_sla_exceeded: z.boolean(),
  service_exec_claimed_at: z.string().nullable().optional(),
  service_exec_claimed_by: z.string().nullable().optional(),
  service_exec_actual_outcome: unknownRecordSchema.nullable().optional(),
  service_exec_error: z.string().nullable().optional(),
  deleted: z.boolean(),
  deleted_at: z.string().nullable().optional(),
  created_at: z.string(),
  updated_at: z.string(),
});
export type DishIngredientRecord = z.infer<typeof dishIngredientRecordSchema>;
export const dishIngredientRecordArraySchema = z.array(dishIngredientRecordSchema);

export const dishIngredientStatusRecordSchema = strictObject({
  id: z.number().int(),
  dish_id: z.number().int(),
  recipe_ingredient_id: z.number().int().nullable().optional(),
  task_key: z.string().nullable().optional(),
  step_order: z.number().int(),
  parallel_group: z.number().int(),
  depth: z.number().int(),
  service_type: z.string().nullable().optional(),
  service_exec: z.string().nullable().optional(),
  retry_count: z.number().int().nullable().optional(),
  retry_delay: z.number().int().nullable().optional(),
  on_failure: z.string().nullable().optional(),
  service_exec_status: z.string(),
  attempt: z.number().int(),
  execution_role: z.string().nullable().optional(),
  operation: z.string().nullable().optional(),
  result_status: z.string().nullable().optional(),
  result_message: z.string().nullable().optional(),
  result_summary: unknownRecordSchema.nullable().optional(),
  service_exec_start_time: z.string().nullable().optional(),
  service_exec_completed_time: z.string().nullable().optional(),
  service_exec_canceled_time: z.string().nullable().optional(),
  service_exec_run_time: z.number().int().nullable().optional(),
  service_exec_sla_exceeded: z.boolean(),
  created_at: z.string(),
  updated_at: z.string(),
});
export type DishIngredientStatusRecord = z.infer<typeof dishIngredientStatusRecordSchema>;
export const dishIngredientStatusRecordArraySchema = z.array(dishIngredientStatusRecordSchema);

const ingredientRecordNormalizedSchema = strictObject({
  id: z.number().int(),
  execution_target: z.string(),
  service_exec: z.string(),
  destination_target: z.string(),
  task_key_template: z.string(),
  execution_id: z.string().nullable().optional(),
  action_id: z.string().nullable().optional(),
  execution_payload: unknownRecordSchema.nullable().optional(),
  service_payload_template: unknownRecordSchema.nullable().optional(),
  execution_parameters: unknownRecordSchema.nullable().optional(),
  service_exec_parameters: unknownRecordSchema.nullable().optional(),
  payload_schema: unknownRecordSchema.optional(),
  service_exec_expected_outcome_default: z.unknown().nullable().optional(),
  execution_engine: z.string(),
  service_type: z.string(),
  execution_purpose: z.string(),
  ingredient_purpose: z.string(),
  ingredient_kind: z.string().nullable().optional(),
  is_active: z.boolean(),
  is_blocking: z.boolean(),
  expected_duration_sec: z.number(),
  default_expected_secs: z.number(),
  timeout_duration_sec: z.number(),
  default_timeout: z.number(),
  retry_count: z.number().int(),
  retry_delay: z.number().int(),
  on_failure: z.string(),
  created_at: z.string(),
  updated_at: z.string(),
  deleted: z.boolean(),
  deleted_at: z.string().nullable().optional(),
});
export type IngredientRecord = z.infer<typeof ingredientRecordNormalizedSchema>;
export const ingredientRecordSchema: z.ZodType<IngredientRecord> = z.preprocess((input) => {
  if (!isRecord(input)) return input;
  return {
    ...input,
    execution_target: input.execution_target ?? input.service_exec ?? "",
    service_exec: input.service_exec ?? input.execution_target ?? "",
    execution_engine: input.execution_engine ?? input.service_type ?? "",
    service_type: input.service_type ?? input.execution_engine ?? "",
    execution_purpose: input.execution_purpose ?? input.ingredient_purpose ?? "utility",
    ingredient_purpose: input.ingredient_purpose ?? input.execution_purpose ?? "utility",
    ingredient_kind: input.ingredient_kind ?? input.ingredient_purpose ?? input.execution_purpose ?? "utility",
    expected_duration_sec: input.expected_duration_sec ?? input.default_expected_secs ?? 0,
    default_expected_secs: input.default_expected_secs ?? input.expected_duration_sec ?? 0,
    timeout_duration_sec: input.timeout_duration_sec ?? input.default_timeout ?? 0,
    default_timeout: input.default_timeout ?? input.timeout_duration_sec ?? 0,
    execution_payload: input.execution_payload ?? input.service_payload_template ?? null,
    execution_parameters: input.execution_parameters ?? input.service_exec_parameters ?? null,
  };
}, ingredientRecordNormalizedSchema) as z.ZodType<IngredientRecord>;
export const ingredientRecordArraySchema = z.array(ingredientRecordSchema);

export const ingredientStatusRecordSchema = strictObject({
  id: z.number().int(),
  service_type: z.string(),
  service_exec: z.string(),
  destination_target: z.string().nullable().optional(),
  task_key_template: z.string(),
  ingredient_purpose: z.string(),
  is_active: z.boolean(),
  is_blocking: z.boolean(),
  default_expected_secs: z.number(),
  default_timeout: z.number(),
  retry_count: z.number().int(),
  retry_delay: z.number().int(),
  on_failure: z.string(),
  created_at: z.string(),
  updated_at: z.string(),
});
export type IngredientStatusRecord = z.infer<typeof ingredientStatusRecordSchema>;
export const ingredientStatusRecordArraySchema = z.array(ingredientStatusRecordSchema);

const recipeStepRecordNormalizedSchema = strictObject({
  id: z.number().int(),
  recipe_id: z.number().int(),
  ingredient_id: z.number().int(),
  step_order: z.number().int(),
  on_success: z.string(),
  parallel_group: z.number().int(),
  depth: z.number().int(),
  execution_payload_override: unknownRecordSchema.nullable().optional(),
  service_payload: unknownRecordSchema.nullable().optional(),
  execution_parameters_override: unknownRecordSchema.nullable().optional(),
  service_exec_parameters_override: unknownRecordSchema.nullable().optional(),
  expected_duration_sec_override: z.number().int().positive().nullable().optional(),
  service_exec_expected_secs: z.number().int().positive().nullable().optional(),
  timeout_duration_sec_override: z.number().int().positive().nullable().optional(),
  service_exec_timeout: z.number().int().positive().nullable().optional(),
  service_exec_expected_outcome: z.unknown().nullable().optional(),
  run_phase: z.string(),
  run_condition: z.string(),
  ingredient: ingredientRecordSchema.nullable().optional(),
});
export type RecipeStepRecord = z.infer<typeof recipeStepRecordNormalizedSchema>;
export const recipeStepRecordSchema: z.ZodType<RecipeStepRecord> = z.preprocess((input) => {
  if (!isRecord(input)) return input;
  return {
    ...input,
    execution_payload_override: input.execution_payload_override ?? input.service_payload ?? null,
    execution_parameters_override:
      input.execution_parameters_override ?? input.service_exec_parameters_override ?? null,
    expected_duration_sec_override:
      input.expected_duration_sec_override ?? input.service_exec_expected_secs ?? null,
    timeout_duration_sec_override:
      input.timeout_duration_sec_override ?? input.service_exec_timeout ?? null,
  };
}, recipeStepRecordNormalizedSchema) as z.ZodType<RecipeStepRecord>;

export const recipeIngredientStatusRecordSchema = strictObject({
  id: z.number().int(),
  recipe_id: z.number().int(),
  ingredient_id: z.number().int(),
  step_order: z.number().int(),
  on_success: z.string(),
  parallel_group: z.number().int(),
  depth: z.number().int(),
  run_phase: z.string(),
  run_condition: z.string(),
  service_type: z.string().nullable().optional(),
  service_exec: z.string().nullable().optional(),
  task_key_template: z.string().nullable().optional(),
  ingredient_purpose: z.string().nullable().optional(),
  ingredient_is_active: z.boolean(),
  ingredient_is_blocking: z.boolean(),
  expected_secs: z.number().int().nullable().optional(),
  timeout_secs: z.number().int().nullable().optional(),
});
export type RecipeIngredientStatusRecord = z.infer<typeof recipeIngredientStatusRecordSchema>;
export const recipeIngredientStatusRecordArraySchema = z.array(recipeIngredientStatusRecordSchema);

export const recipeRecordSchema = strictObject({
  id: z.number().int(),
  name: z.string(),
  description: z.string().nullable().optional(),
  enabled: z.boolean(),
  clear_timeout_sec: z.number().nullable().optional(),
  created_at: z.string(),
  updated_at: z.string(),
  deleted: z.boolean(),
  deleted_at: z.string().nullable().optional(),
  recipe_ingredients: z.array(recipeStepRecordSchema),
  communications: recipeCommunicationsRecordSchema,
  can_execute: z.boolean(),
  inactive_ingredient_ids: z.array(z.number().int()),
});
export type RecipeRecord = z.infer<typeof recipeRecordSchema>;
export const recipeRecordArraySchema = z.array(recipeRecordSchema);

export const recipeStatusRecordSchema = strictObject({
  id: z.number().int(),
  name: z.string(),
  description: z.string().nullable().optional(),
  enabled: z.boolean(),
  clear_timeout_sec: z.number().nullable().optional(),
  can_execute: z.boolean(),
  inactive_ingredient_count: z.number().int(),
  step_count: z.number().int(),
  communication_route_count: z.number().int(),
  created_at: z.string(),
  updated_at: z.string(),
});
export type RecipeStatusRecord = z.infer<typeof recipeStatusRecordSchema>;
export const recipeStatusRecordArraySchema = z.array(recipeStatusRecordSchema);

export const deleteResponseSchema = strictObject({
  status: z.string(),
  id: z.number().int(),
  message: z.string().nullable().optional(),
});
export type DeleteResponse = z.infer<typeof deleteResponseSchema>;

const communicationRouteRequestNormalizedSchema = strictObject({
  id: z.string().optional(),
  label: z.string().min(1),
  service_type: z.string().min(1),
  destination_target: z.string().optional().default(""),
  provider_config: unknownRecordSchema.optional().default({}),
  enabled: z.boolean().optional().default(true),
  position: z.number().int().positive().optional().default(1),
});
export const communicationRouteRequestSchema = z.preprocess((input) => {
  if (!isRecord(input)) return input;
  const normalized = { ...input };
  const executionTarget = normalized.execution_target;
  delete normalized.execution_target;
  return {
    ...normalized,
    service_type: normalized.service_type ?? executionTarget ?? "",
  };
}, communicationRouteRequestNormalizedSchema);
export type CommunicationRouteRequest = z.infer<typeof communicationRouteRequestSchema>;

export const communicationPolicyUpdateRequestSchema = strictObject({
  routes: z.array(communicationRouteRequestSchema),
});

export const uiOperatorActionRequestSchema = strictObject({
  action: z.string().min(1).max(120),
  surface: z.string().min(1).max(120),
  status: z.string().max(40).optional(),
  target: z.string().max(255).nullable().optional(),
  details: unknownRecordSchema.optional().default({}),
});
export type UIOperatorActionRequest = z.infer<typeof uiOperatorActionRequestSchema>;

export const uiOperatorActionResponseSchema = strictObject({
  status: z.string(),
});
export type UIOperatorActionResponse = z.infer<typeof uiOperatorActionResponseSchema>;

export const operatorAuditRecordSchema = strictObject({
  id: z.number().int(),
  req_id: z.string().nullable().optional(),
  action: z.string(),
  surface: z.string(),
  status: z.string(),
  target: z.string().nullable().optional(),
  actor_username: z.string().nullable().optional(),
  actor_role: z.string().nullable().optional(),
  details: unknownRecordSchema,
  created_at: z.string(),
});
export type OperatorAuditRecord = z.infer<typeof operatorAuditRecordSchema>;
export const operatorAuditRecordArraySchema = z.array(operatorAuditRecordSchema);

export const suppressionCreateRequestSchema = strictObject({
  name: z.string().min(1),
  starts_at: z.string().min(1),
  ends_at: z.string().min(1),
  matchers: z.array(suppressionMatcherSchema),
  reason: z.string().nullable().optional(),
  created_by: z.string().nullable().optional(),
  summary_ticket_enabled: z.boolean(),
});

export const recipeStepRequestSchema = strictObject({
  ingredient_id: z.number().int().positive(),
  step_order: z.number().int().positive(),
  on_success: z.string().optional(),
  parallel_group: z.number().int().nonnegative().optional(),
  depth: z.number().int().nonnegative().optional(),
  service_payload: unknownRecordSchema.nullable().optional(),
  execution_payload_override: unknownRecordSchema.nullable().optional(),
  service_exec_parameters_override: unknownRecordSchema.nullable().optional(),
  execution_parameters_override: unknownRecordSchema.nullable().optional(),
  service_exec_expected_secs: z.number().int().positive().nullable().optional(),
  expected_duration_sec_override: z.number().int().positive().nullable().optional(),
  service_exec_timeout: z.number().int().positive().nullable().optional(),
  timeout_duration_sec_override: z.number().int().positive().nullable().optional(),
  run_phase: z.string().optional(),
  run_condition: z.string().optional(),
});

export const recipeCommunicationsRequestSchema = strictObject({
  mode: communicationsModeSchema,
  routes: z.array(communicationRouteRequestSchema).default([]),
});

export const recipeCreateRequestSchema = strictObject({
  name: z.string().min(1),
  description: z.string().nullable().optional(),
  enabled: z.boolean().optional(),
  clear_timeout_sec: z.number().int().positive().nullable().optional(),
  recipe_ingredients: z.array(recipeStepRequestSchema).min(1),
  communications: recipeCommunicationsRequestSchema.optional(),
});

export const recipeUpdateRequestSchema = recipeCreateRequestSchema.partial();

export const authRoleBindingCreateRequestSchema = z
  .object({
    provider: providerNameSchema,
    binding_type: bindingTypeSchema,
    role: userRoleSchema,
    principal_id: z.number().int().nullable().optional(),
    external_group: z.string().nullable().optional(),
    created_by: z.string().nullable().optional(),
  })
  .superRefine((value, ctx) => {
    if (value.binding_type === "user" && value.principal_id == null) {
      ctx.addIssue({
        code: z.ZodIssueCode.custom,
        message: "principal_id is required for user bindings",
        path: ["principal_id"],
      });
    }
    if (value.binding_type === "group" && !value.external_group?.trim()) {
      ctx.addIssue({
        code: z.ZodIssueCode.custom,
        message: "external_group is required for group bindings",
        path: ["external_group"],
      });
    }
  });

export const authRoleBindingUpdateRequestSchema = strictObject({
  role: userRoleSchema.optional(),
  external_group: z.string().nullable().optional(),
});
