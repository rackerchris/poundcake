import {
  createContext,
  type FocusEvent,
  type FormEvent,
  type ReactNode,
  useContext,
  useDeferredValue,
  useEffect,
  useId,
  useRef,
  useState,
  startTransition,
} from "react";
import {
  Link,
  NavLink,
  Navigate,
  Outlet,
  Route,
  Routes,
  useLocation,
  useNavigate,
  useParams,
  useSearchParams,
} from "react-router-dom";
import {
  useMutation,
  useQuery,
  useQueryClient,
} from "@tanstack/react-query";
import { useFieldArray, useForm } from "react-hook-form";
import { zodResolver } from "@hookform/resolvers/zod";
import { z } from "zod";

import {
  apiFetch,
  apiDelete,
  apiGet,
  apiPatch,
  apiPost,
  apiPut,
  ApiError,
} from "./api";
import {
  compactJson,
  formatDate,
  formatLongDate,
  statusTone,
  titleize,
} from "./format";
import type {
  AppSettings,
  AuthMeRecord,
  AuthPrincipalRecord,
  AuthProviderRecord,
  AuthRoleBindingRecord,
  CommunicationPolicyRecord,
  CommunicationRouteRecord,
  DeleteResponse,
  DishIngredientStatusRecord,
  DishStatusRecord,
  HealthResponse,
  IncidentTimelineEvent,
  IncidentTimelineResponse,
  IngredientRecord,
  ObservabilityOverviewResponse,
  OrderStatusRecord,
  PrometheusRuleResourceRecord,
  RepoSyncResponse,
  RecipeRecord,
  ScheduledTaskStatusRecord,
  ServicePluginConfigurationRecord,
  ServicePluginSummaryRecord,
  SuppressionRecord,
} from "./contracts";
import {
  appSettingsSchema,
  authMeRecordSchema,
  authProviderRecordArraySchema,
  authPrincipalRecordArraySchema,
  authRoleBindingCreateRequestSchema,
  authRoleBindingRecordArraySchema,
  authRoleBindingRecordSchema,
  authRoleBindingUpdateRequestSchema,
  communicationActivityRecordArraySchema,
  communicationActivityStatusRecordArraySchema,
  communicationPolicyRecordSchema,
  communicationPolicyUpdateRequestSchema,
  deleteResponseSchema,
  dishIngredientStatusRecordArraySchema,
  dishStatusRecordArraySchema,
  healthResponseSchema,
  incidentTimelineResponseSchema,
  ingredientRecordArraySchema,
  ingredientRecordSchema,
  observabilityActivityStatusRecordArraySchema,
  observabilityOverviewResponseSchema,
  orderStatusRecordArraySchema,
  orderStatusRecordSchema,
  prometheusRuleListResponseSchema,
  recipeCreateRequestSchema,
  recipeRecordArraySchema,
  recipeRecordSchema,
  recipeStatusRecordArraySchema,
  recipeUpdateRequestSchema,
  repoSyncResponseSchema,
  servicePluginConfigurationRecordSchema,
  servicePluginSummaryRecordArraySchema,
  scheduledTaskStatusRecordSchema,
  scheduledTaskStatusRecordArraySchema,
  suppressionCreateRequestSchema,
  suppressionRecordArraySchema,
  suppressionRecordSchema,
  suppressionStatusRecordArraySchema,
  type UIOperatorActionRequest,
  uiOperatorActionRequestSchema,
  uiOperatorActionResponseSchema,
} from "./contracts";

const SettingsContext = createContext<AppSettings | null>(null);
const PrincipalContext = createContext<AuthMeRecord | null>(null);
const ServicePluginsContext = createContext<ServicePluginSummaryRecord[]>([]);
const ToastContext = createContext<(tone: "success" | "error", message: string) => void>(
  () => undefined,
);

interface ToastMessage {
  id: number;
  tone: "success" | "error";
  message: string;
}

const workflowStepSchema = z.object({
  ingredient_id: z.coerce.number().min(1, "Choose an ingredient template"),
  step_order: z.coerce.number().min(1),
  on_success: z.string().min(1),
  run_phase: z.string().min(1),
  run_condition: z.string().min(1),
  parallel_group: z.coerce.number().min(0),
  depth: z.coerce.number().min(0),
  operation: z.string().optional(),
  service_payload_values: z.record(z.any()).optional(),
  execution_parameters_override_text: z.string().optional(),
});

const communicationRouteSchema = z.object({
  id: z.string().optional(),
  label: z.string().min(1, "Route label is required"),
  execution_target: z.string().min(1, "Provider is required"),
  destination_target: z.string().optional(),
  provider_config: z.record(z.any()).default({}),
  enabled: z.boolean(),
  position: z.coerce.number().min(1),
});

const workflowSchema = z.object({
  name: z.string().min(1, "Recipe name is required"),
  description: z.string().optional(),
  enabled: z.boolean(),
  clear_timeout_sec: z.string().optional(),
  recipe_ingredients: z.array(workflowStepSchema).min(1, "Add at least one recipe step"),
  communications_mode: z.enum(["inherit", "local"]),
  communications_routes: z.array(communicationRouteSchema),
});

const suppressionSchema = z.object({
  name: z.string().min(1, "Suppression name is required"),
  reason: z.string().optional(),
  starts_at: z.string().min(1, "Start time is required"),
  ends_at: z.string().min(1, "End time is required"),
  scope: z.string().min(1),
  summary_ticket_enabled: z.boolean(),
  matcher_key: z.string().optional(),
  matcher_operator: z.string().min(1),
  matcher_value: z.string().optional(),
});

const communicationsPolicySchema = z.object({
  routes: z.array(communicationRouteSchema),
});

function App() {
  const [toasts, setToasts] = useState<ToastMessage[]>([]);

  function notify(tone: "success" | "error", message: string) {
    const id = Date.now() + Math.random();
    setToasts((current) => [...current, { id, tone, message }]);
  }

  useEffect(() => {
    if (!toasts.length) {
      return;
    }
    const timers = toasts.map((toast) =>
      window.setTimeout(() => {
        setToasts((current) => current.filter((item) => item.id !== toast.id));
      }, 3800),
    );
    return () => {
      timers.forEach((timer) => window.clearTimeout(timer));
    };
  }, [toasts]);

  return (
    <ToastContext.Provider value={notify}>
      <SessionGate />
      <div className="toast-stack" aria-live="polite" aria-atomic="true">
        {toasts.map((toast) => (
          <div className={`toast-card ${toast.tone}`} key={toast.id}>
            {toast.message}
          </div>
        ))}
      </div>
    </ToastContext.Provider>
  );
}

function SessionGate() {
  const location = useLocation();

  if (isLoginPath(location.pathname)) {
    return (
      <Routes>
        <Route path="/login" element={<LoginPage />} />
        <Route path="*" element={<Navigate to="/login" replace />} />
      </Routes>
    );
  }

  const bootstrapQuery = useQuery({
    queryKey: ["settings", "auth-me", "service-plugins"],
    queryFn: async () => {
      const [settings, principal, servicePlugins] = await Promise.all([
        apiGet("/api/v1/settings", appSettingsSchema),
        apiGet("/api/v1/auth/me", authMeRecordSchema),
        apiGet("/api/v1/plugins", servicePluginSummaryRecordArraySchema),
      ]);
      return { settings, principal, servicePlugins };
    },
  });

  if (bootstrapQuery.isLoading) {
    return <FullscreenState title="Loading monitoring console" message="Checking session and loading workspace state." />;
  }

  if (bootstrapQuery.isError || !bootstrapQuery.data) {
    if (bootstrapQuery.error instanceof ApiError && bootstrapQuery.error.status === 401) {
      const nextTarget = `${location.pathname}${location.search}${location.hash}`;
      return <Navigate to={`/login?next=${encodeURIComponent(nextTarget)}`} replace />;
    }
    return (
      <FullscreenState
        title="Unable to load PoundCake"
        message={getErrorMessage(bootstrapQuery.error)}
        tone="error"
      />
    );
  }

  return (
    <SettingsContext.Provider value={bootstrapQuery.data.settings}>
      <PrincipalContext.Provider value={bootstrapQuery.data.principal}>
        <ServicePluginsContext.Provider value={bootstrapQuery.data.servicePlugins}>
          <Routes>
            <Route element={<ShellLayout />}>
              <Route path="/" element={<Navigate to="/overview" replace />} />
              <Route path="/overview" element={<OverviewPage />} />
              <Route path="/orders" element={<OrdersPage />} />
              <Route path="/orders/:orderId" element={<OrdersPage />} />
              <Route path="/communication-routes" element={<CommunicationRoutesPage />} />
              <Route path="/suppressions" element={<SuppressionsPage />} />
              <Route path="/execution-activity" element={<ExecutionActivityPage />} />
              <Route path="/system-activity" element={<SystemActivityPage />} />
              <Route path="/config/alerts" element={<AlertRulesPage />} />
              <Route path="/config/alert-rules" element={<AlertRulesPage />} />
              <Route path="/config/plugins" element={<PluginsPage />} />
              <Route path="/config/plugins/:serviceType" element={<PluginsPage />} />
              <Route path="/config/communication-policy" element={<CommunicationPolicyPage />} />
              <Route path="/config/recipes" element={<RecipesPage />} />
              <Route path="/config/ingredient-templates" element={<IngredientTemplatesPage />} />
              <Route path="/config/access" element={<AccessPage />} />
              <Route path="*" element={<Navigate to="/overview" replace />} />
            </Route>
          </Routes>
        </ServicePluginsContext.Provider>
      </PrincipalContext.Provider>
    </SettingsContext.Provider>
  );
}

function LoginPage() {
  const [searchParams] = useSearchParams();
  const nextTarget = getLoginNextTarget(searchParams);
  const providersQuery = useQuery({
    queryKey: ["auth-providers"],
    queryFn: () =>
      apiFetch("/api/v1/auth/providers", authProviderRecordArraySchema, {}, { allowUnauthorized: true }),
  });
  const passwordProviders = (providersQuery.data || []).filter((provider) => provider.password_login);
  const browserProviders = (providersQuery.data || []).filter((provider) => provider.browser_login);
  const [provider, setProvider] = useState<string>("");
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [submitAttempted, setSubmitAttempted] = useState(false);
  const [sessionMessage, setSessionMessage] = useState(
    "Sign in with a configured PoundCake auth provider to open the monitoring console.",
  );

  useEffect(() => {
    if (passwordProviders.length === 1) {
      setProvider(passwordProviders[0].name);
    }
  }, [passwordProviders]);

  useEffect(() => {
    let active = true;

    fetch("/api/v1/auth/me", {
      credentials: "same-origin",
    })
      .then((response) => {
        if (!active) {
          return;
        }

        if (response.ok) {
          setSessionMessage("Active session detected. Returning you to the monitoring console.");
          window.location.replace(nextTarget);
          return;
        }

        if (response.status !== 401) {
          setSessionMessage(`Session check returned ${response.status}. You can still sign in below.`);
        }
      })
      .catch(() => {
        if (active) {
          setSessionMessage("Session check is unavailable right now. You can still sign in below.");
        }
      });

    return () => {
      active = false;
    };
  }, [nextTarget]);

  const loginMutation = useMutation({
    mutationFn: async (credentials: { provider: string; username: string; password: string }) => {
      const response = await fetch("/api/v1/auth/login", {
        method: "POST",
        credentials: "same-origin",
        headers: {
          "Content-Type": "application/json",
        },
        body: JSON.stringify(credentials),
      });

      const contentType = response.headers.get("content-type") || "";
      const body = contentType.includes("application/json")
        ? await response.json().catch(() => null)
        : await response.text().catch(() => "");

      if (!response.ok) {
        const detail =
          typeof body === "object" && body && "detail" in body
            ? String((body as { detail: unknown }).detail)
            : response.statusText;
        throw new Error(detail || "Sign in failed.");
      }

      return body;
    },
    onSuccess: () => {
      setSessionMessage("Sign-in successful. Opening your monitoring workspace.");
      window.location.replace(nextTarget);
    },
  });

  function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setSubmitAttempted(true);
    loginMutation.reset();

    if (!username.trim() || !password || !provider) {
      return;
    }

    loginMutation.mutate({
      provider,
      username: username.trim(),
      password,
    });
  }

  const credentialError =
    !provider
      ? "Choose an auth provider to continue."
      : !username.trim() || !password
        ? "Username and password are required."
        : undefined;

  function handleBrowserLogin(providerName: string) {
    window.location.assign(
      `/api/v1/auth/oidc/login?provider=${encodeURIComponent(providerName)}&next=${encodeURIComponent(nextTarget)}`,
    );
  }

  return (
    <div className="login-screen">
      <div className="login-layout">
        <section className="login-hero-panel">
          <div className="eyebrow">PoundCake</div>
          <h1>See orders, communication routes, ticket state, and plugin health in one place.</h1>
          <p>
            PoundCake&apos;s monitoring console is built for fast triage. Sign in to drill into live orders,
            verify whether tickets were created, and confirm Teams or Discord updates were delivered.
          </p>

          <div className="login-highlight-grid">
            <div className="hint-card">
              <strong>Order drilldowns</strong>
              <p>Open a single order and follow its timeline, communication routes, and latest dish outcome.</p>
            </div>
            <div className="hint-card">
              <strong>Communication visibility</strong>
              <p>Track Core ticket IDs, remote delivery status, provider references, and last errors without hunting through logs.</p>
            </div>
            <div className="hint-card">
              <strong>Clear configuration tools</strong>
              <p>Edit alert rules, recipes, and ingredient templates with inline help that explains each field in plain language.</p>
            </div>
          </div>
        </section>

        <section className="login-panel">
          <div className="eyebrow">Secure access</div>
          <h2>Sign in to the monitoring console</h2>
          <p>{sessionMessage}</p>
          <div className="login-meta">
            <span className="version-chip">Next stop: {getRouteName(nextTarget)}</span>
          </div>

          {providersQuery.isError ? (
            <PageError compact message={getErrorMessage(providersQuery.error)} />
          ) : null}

          {passwordProviders.length ? (
            <form className="form-stack" onSubmit={handleSubmit}>
              {passwordProviders.length > 1 ? (
                <FormField label="Provider">
                  <select
                    onChange={(event) => {
                      if (loginMutation.isError) {
                        loginMutation.reset();
                      }
                      setSubmitAttempted(false);
                      setProvider(event.target.value);
                    }}
                    value={provider}
                  >
                    <option value="">Choose a provider</option>
                    {passwordProviders.map((option) => (
                      <option key={option.name} value={option.name}>
                        {option.label}
                      </option>
                    ))}
                  </select>
                </FormField>
              ) : null}

              <FormField label="Username">
                <input
                  autoComplete="username"
                  autoFocus
                  onChange={(event) => {
                    if (loginMutation.isError) {
                      loginMutation.reset();
                    }
                    setSubmitAttempted(false);
                    setUsername(event.target.value);
                  }}
                  placeholder="Enter your username"
                  type="text"
                  value={username}
                />
              </FormField>

              <FormField label="Password">
                <input
                  autoComplete="current-password"
                  onChange={(event) => {
                    if (loginMutation.isError) {
                      loginMutation.reset();
                    }
                    setSubmitAttempted(false);
                    setPassword(event.target.value);
                  }}
                  placeholder="Enter your password"
                  type="password"
                  value={password}
                />
              </FormField>

              {loginMutation.isError ? <PageError compact message={getErrorMessage(loginMutation.error)} /> : null}
              {!loginMutation.isError && submitAttempted && credentialError ? (
                <div className="login-note">{credentialError}</div>
              ) : null}

              <div className="form-actions">
                <button className="primary-button" disabled={loginMutation.isPending} type="submit">
                  {loginMutation.isPending ? "Signing in..." : "Sign in"}
                </button>
              </div>
            </form>
          ) : null}

          {browserProviders.length ? (
            <div className="form-stack">
              {passwordProviders.length ? <div className="login-note">Or continue with single sign-on.</div> : null}
              <div className="form-actions">
                {browserProviders.map((browserProvider) => (
                  <button
                    className="ghost-button"
                    key={browserProvider.name}
                    type="button"
                    onClick={() => handleBrowserLogin(browserProvider.name)}
                  >
                    Sign in with {browserProvider.label}
                  </button>
                ))}
              </div>
            </div>
          ) : null}

          {!providersQuery.isError && !passwordProviders.length && !browserProviders.length ? (
            <div className="login-note">No browser-capable login providers are configured right now.</div>
          ) : null}
        </section>
      </div>
    </div>
  );
}

function ShellLayout() {
  const settings = useSettings();
  const principal = usePrincipal();
  const servicePlugins = useServicePlugins();
  const location = useLocation();

  async function handleLogout() {
    await fetch("/api/v1/auth/logout", {
      method: "POST",
      credentials: "same-origin",
    }).catch(() => undefined);
    window.location.assign("/login");
  }

  const routeName = getRouteName(location.pathname);
  const externalPlugins = servicePlugins.filter((plugin) => plugin.plugin_type === "external_plugin");
  const enabledPlugins = externalPlugins.filter((plugin) => plugin.enabled);
  const pluginHealthSummary = summarizePluginHealth(enabledPlugins);
  const pluginHealthStatus = pluginHealthSummary.notReady ? "degraded" : "active";
  const pluginHealthDetail = pluginHealthSummary.initializing
    ? `, ${pluginHealthSummary.initializing} initializing`
    : "";
  const pluginHealthLabel = enabledPlugins.length
    ? `Plugins: ${pluginHealthSummary.ready}/${enabledPlugins.length} ready${pluginHealthDetail}`
    : "Plugins: none";

  return (
    <div className="shell">
      <aside className="sidebar">
        <div className="brand-card">
          <div className="eyebrow">PoundCake</div>
          <h1>Monitoring Console</h1>
          <p>One place to triage orders, track communication routes, and manage recipe logic.</p>
          <div className="version-chip">v{settings.version}</div>
          <div className="login-note">
            {principal.display_name || principal.username} • role: {rbacRoleLabel(principal)}
          </div>
        </div>

        <nav className="nav-stack" aria-label="Primary navigation">
          <NavGroup
            title="Operations"
            items={[
              { to: "/overview", label: "Overview" },
              { to: "/orders", label: "Orders" },
              { to: "/communication-routes", label: "Communication Routes" },
              { to: "/suppressions", label: "Suppressions" },
              { to: "/execution-activity", label: "Work Execution Activity" },
              { to: "/system-activity", label: "System Activity" },
            ]}
          />
          <NavGroup
            title="Configuration"
            items={[
              { to: "/config/alerts", label: "Alerts" },
              { to: "/config/plugins", label: "Plugins" },
              { to: "/config/communication-policy", label: "Communication Policy" },
              { to: "/config/recipes", label: "Recipes" },
              { to: "/config/ingredient-templates", label: "Ingredient Templates" },
              ...(canManageAccess(principal) ? [{ to: "/config/access", label: "RBAC" }] : []),
            ]}
          />
        </nav>

        <div className="sidebar-footer">
          <button className="ghost-button" type="button" onClick={handleLogout}>
            Log out
          </button>
        </div>
      </aside>

      <div className="content-shell">
        <header className="topbar">
          <div>
            <div className="eyebrow">PoundCake</div>
            <h2>{routeName}</h2>
          </div>
          <div className="topbar-meta">
            <StatusBadge status={pluginHealthStatus}>
              {pluginHealthLabel}
            </StatusBadge>
            <StatusBadge status={settings.git_enabled ? "active" : "new"}>
              {settings.git_enabled ? "GitHub sync enabled" : "GitHub sync disabled"}
            </StatusBadge>
          </div>
        </header>

        <main className="page-content">
          <Outlet />
        </main>
      </div>
    </div>
  );
}

function OverviewPage() {
  const principal = usePrincipal();
  const servicePlugins = useServicePlugins();
  const dataQuery = useQuery({
    queryKey: ["overview-dashboard"],
    refetchInterval: 15_000,
    refetchIntervalInBackground: false,
    refetchOnWindowFocus: true,
    queryFn: async () => {
      const [health, overview, activity, incidents, dishes, communications, suppressions] =
        await Promise.all([
          apiGet("/api/v1/health/status", healthResponseSchema),
          apiGet("/api/v1/observability/overview", observabilityOverviewResponseSchema),
          apiGet("/api/v1/observability/activity/status?limit=10&order_scope=operator", observabilityActivityStatusRecordArraySchema),
          apiGet("/api/v1/orders/status?limit=8&order_scope=operator", orderStatusRecordArraySchema),
          apiGet("/api/v1/dishes/status?limit=100&order_scope=operator", dishStatusRecordArraySchema),
          apiGet("/api/v1/communications/activity/status?limit=8", communicationActivityStatusRecordArraySchema),
          apiGet("/api/v1/suppressions/status?limit=8", suppressionStatusRecordArraySchema),
        ]);
      return { health, overview, activity, incidents, dishes, communications, suppressions };
    },
  });

  if (dataQuery.isLoading) {
    return <PageLoading message="Loading overview signal, order flow, and recent work execution activity." />;
  }

  if (dataQuery.isError || !dataQuery.data) {
    return <PageError message={getErrorMessage(dataQuery.error)} />;
  }

  const { health, overview, activity, incidents, dishes, communications, suppressions } = dataQuery.data;
  const activeOrders = incidents.filter((item) => item.is_active).slice(0, 5);
  const failedCommunications = communications.filter(
    (item) => statusTone(item.remote_state || item.lifecycle_state) === "bad",
  );
  const externalPlugins = servicePlugins.filter((plugin) => plugin.plugin_type === "external_plugin");
  const enabledPlugins = externalPlugins.filter((plugin) => plugin.enabled);
  const pluginHealthSummary = summarizePluginHealth(enabledPlugins);

  return (
    <div className="page-stack">
      <section className="hero-panel">
        <div>
          <div className="eyebrow">Operations overview</div>
          <h3>What needs attention right now</h3>
          <p>
            Use this workspace to jump from system health to active orders, outbound communication routes,
            and recent dish work execution activity. Your current RBAC role controls which actions are available.
          </p>
        </div>
        <div className="hero-strip">
          <MetricPill label="Your RBAC role" value={rbacRoleLabel(principal)} />
          <MetricPill label="Open orders" value={String(activeOrders.length)} />
          <MetricPill label="Failed dishes" value={String(overview.failures.dishes_failed)} />
          <MetricPill label="Platform" value={health.status} />
        </div>
      </section>

      <div className="status-grid">
        <Link className="metric-card-link" to="/config/alerts">
          <MetricCard title="Alerts" value="Pending" tone="unknown">
            Prometheus CRD integration
          </MetricCard>
        </Link>
        <Link className="metric-card-link" to="/communication-routes">
          <MetricCard title="Communication Routes" value={String(communications.length)} tone={failedCommunications.length ? "failed" : "healthy"}>
            Failed routes: {failedCommunications.length}
          </MetricCard>
        </Link>
        <Link className="metric-card-link" to="/execution-activity">
          <MetricCard title="Dish Executions" value={String(dishes.length)} tone={overview.failures.orders_failed ? "warning" : "healthy"}>
            Order failures: {overview.failures.orders_failed}
          </MetricCard>
        </Link>
        <Link className="metric-card-link" to="/config/plugins">
          <MetricCard title="Plugins" value={String(enabledPlugins.length)} tone={pluginHealthSummary.notReady ? "warning" : "healthy"}>
            Not ready plugins: {pluginHealthSummary.notReady}
          </MetricCard>
        </Link>
      </div>

      <div className="overview-grid">
        <Panel title="Active Orders" subtitle="Click any order to open its full drilldown.">
          <div className="list-stack">
            {activeOrders.length ? (
              activeOrders.map((incident) => (
                <Link className="feed-row" to={`/orders/${incident.id}`} key={incident.id}>
                  <div>
                    <strong>{incident.alert_group_name}</strong>
                    <p>{incident.instance || "No instance"} • {incident.severity || "unknown severity"}</p>
                  </div>
                  <StatusBadge status={incident.processing_status}>{incident.processing_status}</StatusBadge>
                </Link>
              ))
            ) : (
              <EmptyState message="No active orders right now." />
            )}
          </div>
        </Panel>

        <Panel title="Recent Work Execution Activity" subtitle="The feed combines orders, communication routes, suppressions, and dish work executions.">
          <div className="list-stack">
            {activity.map((item) => (
              <Link className="feed-row" key={`${item.type}-${item.target_id}`} to={item.link_hint || "/overview"}>
                <div>
                  <div className="feed-title-row">
                    <strong>{item.title}</strong>
                    <span className="feed-type">{item.type}</span>
                  </div>
                  <p>{item.summary || "No summary available."}</p>
                </div>
                <div className="feed-meta">
                  <StatusBadge status={item.status}>{item.status}</StatusBadge>
                  <span>{formatDate(item.timestamp)}</span>
                </div>
              </Link>
            ))}
          </div>
        </Panel>

        <Panel title="Communication Routes" subtitle="Track ticketable routes and chat notifications in one feed.">
          <div className="list-stack">
            {communications.slice(0, 6).map((item) => (
              <Link className="feed-row" key={item.communication_id} to={item.reference_type === "incident" ? `/orders/${item.reference_id}` : "/communication-routes"}>
                <div>
                  <strong>{item.reference_name || item.reference_id}</strong>
                  <p>
                    {titleize(item.channel)} • {item.destination || "No destination"} •{" "}
                    {titleize(item.remote_state || item.lifecycle_state || "pending")}
                  </p>
                </div>
                <div className="feed-meta">
                  <StatusBadge status={item.remote_state || item.lifecycle_state}>{item.remote_state || item.lifecycle_state || "unknown"}</StatusBadge>
                  <span>{formatDate(item.updated_at)}</span>
                </div>
              </Link>
            ))}
          </div>
        </Panel>

        <Panel title="Suppressions" subtitle="Time-boxed monitoring suppressions and their impact.">
          <div className="list-stack">
            {suppressions.slice(0, 6).map((item) => (
              <Link className="feed-row" key={item.id} to={`/suppressions?suppression=${item.id}`}>
                <div>
                  <strong>{item.name}</strong>
                  <p>{item.reason || "No reason provided."}</p>
                </div>
                <div className="feed-meta">
                  <StatusBadge status={item.status}>{item.status}</StatusBadge>
                  <span>{formatDate(item.ends_at)}</span>
                </div>
              </Link>
            ))}
          </div>
        </Panel>
      </div>

      <Panel title="Runbook hints" subtitle="Operational hints from the observability overview endpoint.">
        <div className="hint-grid">
          {overview.failures.runbook_hints.length ? (
            overview.failures.runbook_hints.map((hint) => <div className="hint-card" key={hint}>{hint}</div>)
          ) : (
            <EmptyState message="No runbook hints at the moment." />
          )}
        </div>
      </Panel>
    </div>
  );
}

function PluginsPage() {
  const { serviceType } = useParams();
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const notify = useToast();
  const principal = usePrincipal();
  const servicePlugins = useServicePlugins();
  const selectedPlugin =
    servicePlugins.find((plugin) => plugin.service_type === serviceType) ||
    servicePlugins.find((plugin) => plugin.enabled) ||
    servicePlugins[0];
  const [enabledInput, setEnabledInput] = useState(false);
  const [intervalInput, setIntervalInput] = useState("");
  const [healthIntervalInput, setHealthIntervalInput] = useState("");
  const [queryLimitInput, setQueryLimitInput] = useState("");
  const [operatorConfigInput, setOperatorConfigInput] = useState<Record<string, string | boolean>>({});
  const [operatorCredentialKeyIdInput, setOperatorCredentialKeyIdInput] = useState("default");
  const [operatorCredentialInput, setOperatorCredentialInput] = useState("");
  const [operatorCredentialInputs, setOperatorCredentialInputs] = useState<Record<string, string>>({});
  const [operatorCredentialTouched, setOperatorCredentialTouched] = useState(false);
  const [operatorCredentialField, setOperatorCredentialField] = useState("token");
  const [scheduledTaskInputs, setScheduledTaskInputs] = useState<
    Record<number, { enabled: boolean; interval: string }>
  >({});
  const canManageAdapterConfiguration = hasRole(principal, "operator");
  const canManageAdapterCredentials = hasRole(principal, "admin");

  const scheduledTasksQuery = useQuery({
    queryKey: ["scheduled-tasks", selectedPlugin?.service_type],
    enabled: Boolean(selectedPlugin?.service_type),
    queryFn: () =>
      apiGet(
        `/api/v1/scheduled-tasks/status?service_type=${encodeURIComponent(selectedPlugin?.service_type || "")}`,
        scheduledTaskStatusRecordArraySchema,
      ),
  });

  const operatorConfigQuery = useQuery({
    queryKey: ["plugin-configuration", selectedPlugin?.service_type],
    enabled: selectedPlugin?.plugin_type === "external_plugin" && canManageAdapterConfiguration,
    queryFn: () =>
      apiGet(
        `/api/v1/plugins/${encodeURIComponent(selectedPlugin?.service_type || "")}/configuration`,
        servicePluginConfigurationRecordSchema,
      ),
  });

  useEffect(() => {
    if (!serviceType && selectedPlugin) {
      startTransition(() => {
        navigate(`/config/plugins/${encodeURIComponent(selectedPlugin.service_type)}`, { replace: true });
      });
    }
  }, [navigate, selectedPlugin, serviceType]);

  useEffect(() => {
    setEnabledInput(Boolean(selectedPlugin?.enabled));
    setIntervalInput(
      selectedPlugin?.run_interval_seconds ? String(selectedPlugin.run_interval_seconds) : "",
    );
  }, [selectedPlugin?.enabled, selectedPlugin?.run_interval_seconds, selectedPlugin?.service_type]);

  useEffect(() => {
    setQueryLimitInput(selectedPlugin?.query_limit ? String(selectedPlugin.query_limit) : "");
  }, [selectedPlugin?.query_limit, selectedPlugin?.service_type]);

  useEffect(() => {
    setHealthIntervalInput(
      selectedPlugin?.health_check_interval_seconds
        ? String(selectedPlugin.health_check_interval_seconds)
        : "",
    );
  }, [selectedPlugin?.health_check_interval_seconds, selectedPlugin?.service_type]);

  useEffect(() => {
    if (selectedPlugin?.plugin_type !== "external_plugin") {
      return;
    }
    setOperatorConfigInput(normalizeUiConfig(operatorConfigQuery.data?.config || {}));
    setOperatorCredentialKeyIdInput(operatorConfigQuery.data?.credential_key_id || "default");
    setOperatorCredentialField(defaultCredentialField(operatorConfigQuery.data?.credential_type));
    setOperatorCredentialInput("");
    setOperatorCredentialInputs({});
    setOperatorCredentialTouched(false);
  }, [
    selectedPlugin?.plugin_type,
    selectedPlugin?.service_type,
    operatorConfigQuery.data?.config,
    operatorConfigQuery.data?.credential_key_id,
    operatorConfigQuery.data?.credential_type,
  ]);

  useEffect(() => {
    const nextInputs: Record<number, { enabled: boolean; interval: string }> = {};
    for (const task of scheduledTasksQuery.data || []) {
      nextInputs[task.id] = {
        enabled: task.is_enabled,
        interval: String(task.run_interval_seconds),
      };
    }
    setScheduledTaskInputs(nextInputs);
  }, [scheduledTasksQuery.data, selectedPlugin?.service_type]);

  const updatePluginMutation = useMutation({
    mutationFn: async (values: {
      plugin?: {
        enabled?: boolean;
        run_interval_seconds?: number;
        query_limit?: number;
        health_check_interval_seconds?: number;
      };
      scheduledTasks?: Array<{
        id: number;
        is_enabled?: boolean;
        run_interval_seconds?: number;
      }>;
    }) => {
      if (!selectedPlugin) {
        throw new Error("No plugin selected");
      }
      if (values.plugin && Object.keys(values.plugin).length) {
        await apiPatch(
          `/api/v1/plugins/${encodeURIComponent(selectedPlugin.service_type)}`,
          z.unknown(),
          values.plugin,
        );
      }
      await Promise.all(
        (values.scheduledTasks || []).map((task) =>
          apiPatch(`/api/v1/scheduled-tasks/${task.id}`, z.unknown(), {
            ...(task.is_enabled === undefined ? {} : { is_enabled: task.is_enabled }),
            ...(task.run_interval_seconds === undefined
              ? {}
              : { run_interval_seconds: task.run_interval_seconds }),
          }),
        ),
      );
    },
    onSuccess: async () => {
      notify("success", "Plugin configuration updated.");
      await queryClient.invalidateQueries({ queryKey: ["settings", "auth-me", "service-plugins"] });
      await queryClient.invalidateQueries({ queryKey: ["scheduled-tasks"] });
    },
    onError: (error) => notify("error", getErrorMessage(error)),
  });

  const saveOperatorPluginConfigMutation = useMutation({
    mutationFn: async (): Promise<ServicePluginConfigurationRecord> => {
      if (!selectedPlugin) {
        throw new Error("No plugin selected");
      }
      const serviceType = selectedPlugin.service_type;
      const credentialPayload = buildCredentialPayload(
        editableOperatorCredentialRequirements,
        operatorConfigQuery.data?.credential_type,
        operatorCredentialField,
        operatorCredentialInput,
        operatorCredentialInputs,
      );
      const response = await apiPut(
        `/api/v1/plugins/${encodeURIComponent(serviceType)}/configuration`,
        servicePluginConfigurationRecordSchema,
        {
          config: serializeUiConfig(operatorConfigInput, operatorConfigQuery.data?.config_schema),
        },
      );
      if (credentialPayload) {
        return apiPut(
          `/api/v1/plugins/${encodeURIComponent(serviceType)}/credentials`,
          servicePluginConfigurationRecordSchema,
          {
            credential_type: operatorConfigQuery.data?.credential_type,
            credential_key_id: operatorCredentialKeyIdInput.trim() || "default",
            credential_payload: credentialPayload,
            rotate_credential: true,
          },
        );
      }
      return response;
    },
    onSuccess: async (response) => {
      queryClient.setQueryData(
        ["plugin-configuration", response.service_type],
        response,
      );
      setOperatorConfigInput(normalizeUiConfig(response.config || {}));
      setOperatorCredentialKeyIdInput(response.credential_key_id || "default");
      setOperatorCredentialField(defaultCredentialField(response.credential_type));
      setOperatorCredentialInput("");
      setOperatorCredentialInputs({});
      setOperatorCredentialTouched(false);
      await queryClient.invalidateQueries({ queryKey: ["plugin-configuration"] });
      await queryClient.invalidateQueries({ queryKey: ["settings", "auth-me", "service-plugins"] });
      notify("success", "Plugin connection configuration saved.");
    },
    onError: (error) => notify("error", getErrorMessage(error)),
  });

  const runScheduledTaskNowMutation = useMutation({
    mutationFn: async (task: ScheduledTaskStatusRecord) =>
      apiPost(
        `/api/v1/scheduled-tasks/${task.id}/run-now`,
        scheduledTaskStatusRecordSchema,
      ),
    onSuccess: async (task) => {
      notify("success", `${scheduledTaskRunActionLabel(task)} requested.`);
      await queryClient.invalidateQueries({ queryKey: ["scheduled-tasks"] });
      await queryClient.invalidateQueries({ queryKey: ["settings", "auth-me", "service-plugins"] });
    },
    onError: (error) => notify("error", getErrorMessage(error)),
  });

  const internalPlugins = servicePlugins.filter((plugin) => plugin.plugin_type === "internal_plugin");
  const externalPlugins = servicePlugins.filter((plugin) => plugin.plugin_type === "external_plugin");
  const enabledCount = servicePlugins.filter((plugin) => plugin.enabled).length;
  const internalCount = internalPlugins.length;
  const pluginHealthSummary = summarizePluginHealth(externalPlugins.filter((plugin) => plugin.enabled));
  const selectedScheduledTasks = scheduledTasksQuery.data || [];
  const selectedPluginIsExternal = selectedPlugin?.plugin_type === "external_plugin";
  const canUpdatePluginConfig = hasRole(principal, "operator");
  const canManageScheduledTasks = hasRole(principal, "operator");
  const selectedPluginSupportsQueryLimit =
    selectedPlugin?.service_type === "prep-chef" || selectedPlugin?.service_type === "timer";
  const selectedWorkerState = selectedPlugin?.enabled ? "enabled" : "paused";
  const currentIntervalInput = selectedPlugin?.run_interval_seconds
    ? String(selectedPlugin.run_interval_seconds)
    : "";
  const currentQueryLimitInput = selectedPlugin?.query_limit ? String(selectedPlugin.query_limit) : "";
  const currentHealthIntervalInput = selectedPlugin?.health_check_interval_seconds
    ? String(selectedPlugin.health_check_interval_seconds)
    : "";
  const parsedInterval = Number.parseInt(intervalInput, 10);
  const parsedQueryLimit = Number.parseInt(queryLimitInput, 10);
  const parsedHealthInterval = Number.parseInt(healthIntervalInput, 10);
  const intervalChanged = Boolean(selectedPlugin?.config_editable) && intervalInput.trim() !== currentIntervalInput;
  const queryLimitChanged =
    Boolean(selectedPluginSupportsQueryLimit) &&
    queryLimitInput.trim() !== currentQueryLimitInput;
  const healthIntervalChanged =
    Boolean(selectedPluginIsExternal) &&
    healthIntervalInput.trim() !== currentHealthIntervalInput;
  const operatorConfig = operatorConfigQuery.data?.config || {};
  const editableOperatorCredentialRequirements = editableCredentialRequirements(
    operatorConfigQuery.data?.credential_requirements,
  );
  const canEditAdapterCredentials = Boolean(
    canManageAdapterCredentials && editableOperatorCredentialRequirements.length,
  );
  const credentialDirty = Boolean(
    operatorCredentialTouched &&
      (credentialInputHasNewValue(operatorCredentialInput) ||
        Object.values(operatorCredentialInputs).some((value) => credentialInputHasNewValue(value))),
  );
  const operatorConfigDirty = Boolean(
    selectedPluginIsExternal &&
      comparableOperatorConfig(
        serializeUiConfig(operatorConfigInput, operatorConfigQuery.data?.config_schema),
        operatorConfigQuery.data?.config_schema,
      ) !==
        comparableOperatorConfig(operatorConfig, operatorConfigQuery.data?.config_schema),
  );
  const savedCredentialKeyId = operatorConfigQuery.data?.credential_key_id || "default";
  const requestedCredentialKeyId = operatorCredentialKeyIdInput.trim() || "default";
  const operatorCredentialDirty = Boolean(
    selectedPluginIsExternal &&
      canEditAdapterCredentials &&
      (requestedCredentialKeyId !== savedCredentialKeyId || credentialDirty),
  );
  const enabledChanged =
    Boolean(selectedPlugin?.config_editable || selectedPluginIsExternal) &&
    enabledInput !== Boolean(selectedPlugin?.enabled);
  const pluginConfigDirty = Boolean(
    enabledChanged || intervalChanged || queryLimitChanged || healthIntervalChanged,
  );
  const scheduledTaskConfigDirty = selectedScheduledTasks.some((task) => {
    const input = scheduledTaskInputs[task.id];
    return Boolean(
      input &&
        (input.enabled !== task.is_enabled || input.interval.trim() !== String(task.run_interval_seconds)),
    );
  });
  const canSavePluginPage = Boolean(
    (pluginConfigDirty && canUpdatePluginConfig) ||
      (scheduledTaskConfigDirty && canManageScheduledTasks),
  );
  const canSaveOperatorPluginConfig = Boolean(
    selectedPluginIsExternal &&
      canManageAdapterConfiguration &&
      (operatorConfigDirty || operatorCredentialDirty),
  );
  const operatorCredentialRequired = hasRequiredCredentialRequirement(
    editableOperatorCredentialRequirements,
  );
  const canUseSavedAdapterState = Boolean(
    !selectedPluginIsExternal ||
      (!operatorCredentialRequired || operatorConfigQuery.data?.credential_configured) &&
        !operatorConfigDirty &&
        !operatorCredentialDirty &&
        !saveOperatorPluginConfigMutation.isPending,
  );

  const savePluginConfig = () => {
    if (!selectedPlugin) {
      return;
    }
    const payload: {
      enabled?: boolean;
      run_interval_seconds?: number;
      query_limit?: number;
      health_check_interval_seconds?: number;
    } = {};

    if (selectedPlugin.config_editable || selectedPluginIsExternal) {
      if (enabledChanged) {
        payload.enabled = enabledInput;
      }
    }

    if (selectedPlugin.config_editable) {
      if (!Number.isFinite(parsedInterval) || parsedInterval < 1) {
        notify("error", "Run interval must be at least 1 second.");
        return;
      }
      if (intervalChanged) {
        payload.run_interval_seconds = parsedInterval;
      }
      if (selectedPluginSupportsQueryLimit) {
        if (!Number.isFinite(parsedQueryLimit) || parsedQueryLimit < 1) {
          notify("error", "Query limit must be at least 1.");
          return;
        }
        if (queryLimitChanged) {
          payload.query_limit = parsedQueryLimit;
        }
      }
    }

    if (selectedPluginIsExternal) {
      if (!Number.isFinite(parsedHealthInterval) || parsedHealthInterval < 1) {
        notify("error", "Health check interval must be at least 1 second.");
        return;
      }
      if (healthIntervalChanged) {
        payload.health_check_interval_seconds = parsedHealthInterval;
      }
    }

    const scheduledTaskUpdates: Array<{
      id: number;
      is_enabled?: boolean;
      run_interval_seconds?: number;
    }> = [];
    for (const task of selectedScheduledTasks) {
      const input = scheduledTaskInputs[task.id];
      if (!input) {
        continue;
      }
      const taskPayload: {
        id: number;
        is_enabled?: boolean;
        run_interval_seconds?: number;
      } = { id: task.id };
      if (input.enabled !== task.is_enabled) {
        taskPayload.is_enabled = input.enabled;
      }
      if (input.interval.trim() !== String(task.run_interval_seconds)) {
        const parsedTaskInterval = Number.parseInt(input.interval, 10);
        if (!Number.isFinite(parsedTaskInterval) || parsedTaskInterval < 1) {
          notify("error", `Scheduled task ${task.task_key} interval must be at least 1 second.`);
          return;
        }
        taskPayload.run_interval_seconds = parsedTaskInterval;
      }
      if (Object.keys(taskPayload).length > 1) {
        scheduledTaskUpdates.push(taskPayload);
      }
    }

    if (scheduledTaskUpdates.length && !canManageScheduledTasks) {
      notify("error", "Only operators can update scheduled task frequencies.");
      return;
    }

    if (!Object.keys(payload).length && !scheduledTaskUpdates.length) {
      notify("success", "No plugin changes to save.");
      return;
    }
    updatePluginMutation.mutate({
      plugin: Object.keys(payload).length ? payload : undefined,
      scheduledTasks: scheduledTaskUpdates,
    });
  };
  const pluginGroups = [
    { title: "Internal plugins", plugins: internalPlugins },
    { title: "External plugins", plugins: externalPlugins },
  ];

  return (
    <div className="page-stack">
      <PageHeader
        title="Plugins"
        description="Inspect enabled service plugins, health state, credentials, ingredient templates, and shared helper capability contracts."
      />

      <div className="status-grid">
        <MetricCard title="Registered" value={String(servicePlugins.length)} tone={servicePlugins.length ? "healthy" : "unknown"}>
          Plugins in catalog
        </MetricCard>
        <MetricCard title="Enabled" value={String(enabledCount)} tone={enabledCount ? "healthy" : "unknown"}>
          Active service plugins
        </MetricCard>
        <MetricCard title="Internal" value={String(internalCount)} tone={internalCount ? "healthy" : "unknown"}>
          PoundCake workers
        </MetricCard>
        <MetricCard title="Not ready" value={String(pluginHealthSummary.notReady)} tone={pluginHealthSummary.notReady ? "warning" : "healthy"}>
          Initializing or unhealthy
        </MetricCard>
      </div>

      <div className="master-detail">
        <Panel title="Plugin catalog" subtitle={`${servicePlugins.length} plugin(s) registered.`}>
          <div className="list-stack incident-list">
            {servicePlugins.length ? (
              pluginGroups.map((group) => group.plugins.length ? (
                <section className="plugin-catalog-section" key={group.title}>
                  <div className="plugin-catalog-heading">{group.title}</div>
                  <div className="list-stack">
                    {group.plugins.map((plugin) => (
                      <button
                        className={`incident-row ${selectedPlugin?.service_type === plugin.service_type ? "active" : ""}`}
                        key={plugin.service_type}
                        type="button"
                        onClick={() => startTransition(() => navigate(`/config/plugins/${encodeURIComponent(plugin.service_type)}`))}
                      >
                        <div>
                          <strong>{titleize(plugin.service_type)}</strong>
                          <p>
                            {plugin.plugin_type === "internal_plugin" ? "internal" : "external"} • {formatPluginTier(plugin.plugin_tier)} •{" "}
                            {plugin.ingredient_template_count} ingredient template(s) • {plugin.recipe_template_count} recipe template(s)
                          </p>
                        </div>
                        <div className="feed-meta">
                          {plugin.plugin_type === "external_plugin" ? (
                            <StatusBadge status={plugin.health_status}>{plugin.health_status}</StatusBadge>
                          ) : null}
                          <span>{plugin.enabled ? "enabled" : "disabled"}</span>
                        </div>
                      </button>
                    ))}
                  </div>
                </section>
              ) : null)
            ) : (
              <EmptyState message="No service plugins are registered." />
            )}
          </div>
        </Panel>

        <Panel title="Plugin details" subtitle="Runtime metadata and helper capability visibility.">
          {selectedPlugin ? (
            <div className="detail-stack">
              <section className="detail-hero">
                <div>
                  <div className="eyebrow">Service plugin</div>
                  <h3>{titleize(selectedPlugin.service_type)}</h3>
                  <p>{selectedPlugin.plugin_log_key || selectedPlugin.service_type}</p>
                </div>
                <div className="hero-strip">
                  {selectedPluginIsExternal ? (
                    <>
                      <MetricPill label="Health" value={selectedPlugin.health_status} />
                      <MetricPill label="Credentials" value={selectedPlugin.credential_status} />
                      <MetricPill
                        label="Health interval"
                        value={selectedPlugin.health_check_interval_seconds ? `${selectedPlugin.health_check_interval_seconds}s` : "-"}
                      />
                    </>
                  ) : (
                    <>
                      <MetricPill label="State" value={selectedWorkerState} />
                      <MetricPill label="Run interval" value={selectedPlugin.run_interval_seconds ? `${selectedPlugin.run_interval_seconds}s` : "-"} />
                      {selectedPluginSupportsQueryLimit ? (
                        <MetricPill label="Limit" value={selectedPlugin.query_limit ? String(selectedPlugin.query_limit) : "-"} />
                      ) : null}
                    </>
                  )}
                  <MetricPill label="Type" value={selectedPlugin.plugin_type === "internal_plugin" ? "internal" : "external"} />
                  <MetricPill label="Ingredients" value={String(selectedPlugin.ingredient_template_count)} />
                </div>
              </section>

              {selectedPlugin.config_editable || selectedPluginIsExternal || selectedScheduledTasks.length ? (
                <section className="plugin-config-panel">
                  <div className="panel-head">
                    <div>
                      <h4>Plugin controls</h4>
                      <p>Adjust worker cadence and plugin-owned scheduled task frequencies for the selected registered plugin.</p>
                    </div>
                    <button
                      className="primary-button"
                      disabled={updatePluginMutation.isPending || !canSavePluginPage}
                      type="button"
                      onClick={savePluginConfig}
                    >
                      {updatePluginMutation.isPending ? "Saving..." : "Save changes"}
                    </button>
                  </div>
                  <div className="plugin-config-grid">
                    {selectedPlugin.config_editable ? (
                      <>
                        <FormField label="Worker state" help="Paused internal plugins stop their worker loop but remain registered.">
                          <label className="toggle-row plugin-toggle-field">
                            <input
                              checked={enabledInput}
                              disabled={updatePluginMutation.isPending || !canUpdatePluginConfig}
                              type="checkbox"
                              onChange={(event) => setEnabledInput(event.target.checked)}
                            />
                            <span>{enabledInput ? "Enabled" : "Paused"}</span>
                          </label>
                        </FormField>
                        <FormField label="Run interval seconds" help="How often this internal worker wakes up to inspect its queue.">
                          <input
                            disabled={updatePluginMutation.isPending || !canUpdatePluginConfig}
                            min={1}
                            type="number"
                            value={intervalInput}
                            onChange={(event) => setIntervalInput(event.target.value)}
                          />
                        </FormField>
                      </>
                    ) : null}
                    {selectedPluginIsExternal ? (
                      <FormField label="Adapter state" help="Disabled adapters keep queued work cached until the adapter is enabled or the execution times out.">
                        <label className="toggle-row plugin-toggle-field">
                          <input
                            checked={enabledInput}
                            disabled={updatePluginMutation.isPending || !canUpdatePluginConfig}
                            type="checkbox"
                            onChange={(event) => setEnabledInput(event.target.checked)}
                          />
                          <span>{enabledInput ? "Enabled" : "Disabled"}</span>
                        </label>
                      </FormField>
                    ) : null}
                    {selectedPluginSupportsQueryLimit ? (
                      <FormField label="Query limit">
                        <input
                          disabled={updatePluginMutation.isPending || !canUpdatePluginConfig}
                          min={1}
                          type="number"
                          value={queryLimitInput}
                          onChange={(event) => setQueryLimitInput(event.target.value)}
                        />
                      </FormField>
                    ) : null}
                    {selectedPluginIsExternal ? (
                      <FormField label="Health check interval seconds" help="How often Dishwasher injects this plugin's health check as an order.">
                        <input
                          disabled={updatePluginMutation.isPending || !canUpdatePluginConfig}
                          min={1}
                          type="number"
                          value={healthIntervalInput}
                          onChange={(event) => setHealthIntervalInput(event.target.value)}
                        />
                      </FormField>
                    ) : null}
                  </div>
                  {selectedScheduledTasks.length ? (
                    <div className="scheduled-task-controls">
                      <div className="section-heading compact">
                        <div>
                          <h4>Scheduled tasks</h4>
                          <p>Dishwasher uses these intervals to inject plugin-owned recurring work as orders.</p>
                        </div>
                        <StatusBadge status={canManageScheduledTasks ? "healthy" : "unknown"}>
                          {canManageScheduledTasks ? "operator editable" : "operator only"}
                        </StatusBadge>
                      </div>
                      <div className="scheduled-task-list">
                        {selectedScheduledTasks.map((task) => {
                          const taskInput = scheduledTaskInputs[task.id] || {
                            enabled: task.is_enabled,
                            interval: String(task.run_interval_seconds),
                          };
                          const taskConfigDirty = Boolean(
                            taskInput.enabled !== task.is_enabled ||
                              taskInput.interval.trim() !== String(task.run_interval_seconds),
                          );
                          const canRunTaskNow = Boolean(
                            canManageScheduledTasks &&
                              canUseSavedAdapterState &&
                              (!selectedPluginIsExternal || selectedPlugin.enabled) &&
                              isOperatorRunnableScheduledTask(task) &&
                              !taskConfigDirty &&
                              !updatePluginMutation.isPending &&
                              !runScheduledTaskNowMutation.isPending,
                          );
                          const blockedRunMessage = scheduledTaskRunBlockedMessage({
                            adapterConfigDirty: operatorConfigDirty,
                            canUseSavedAdapterState,
                            pluginEnabled: !selectedPluginIsExternal || Boolean(selectedPlugin.enabled),
                            task,
                            taskConfigDirty,
                          });
                          const runTaskTitle = blockedRunMessage || task.run_now_description;
                          return (
                            <div className="scheduled-task-row" key={task.id}>
                              <div className="scheduled-task-main">
                                <strong>{task.task_key}</strong>
                                <p>
                                  {task.task_type} • {task.service_exec || "plugin task"} • {scheduledTaskStateLabel(task)}
                                </p>
                              </div>
                              <label className="toggle-row scheduled-task-toggle">
                                <input
                                  checked={taskInput.enabled}
                                  disabled={updatePluginMutation.isPending || !canManageScheduledTasks}
                                  type="checkbox"
                                  onChange={(event) =>
                                    setScheduledTaskInputs((current) => ({
                                      ...current,
                                      [task.id]: {
                                        ...taskInput,
                                        enabled: event.target.checked,
                                      },
                                    }))
                                  }
                                />
                                <span>{taskInput.enabled ? "Enabled" : "Paused"}</span>
                              </label>
                              <FormField label="Interval seconds">
                                <input
                                  disabled={updatePluginMutation.isPending || !canManageScheduledTasks}
                                  min={1}
                                  type="number"
                                  value={taskInput.interval}
                                  onChange={(event) =>
                                    setScheduledTaskInputs((current) => ({
                                      ...current,
                                      [task.id]: {
                                        ...taskInput,
                                        interval: event.target.value,
                                      },
                                    }))
                                  }
                                />
                              </FormField>
                              <button
                                className="ghost-button"
                                disabled={
                                  !canRunTaskNow
                                }
                                title={runTaskTitle}
                                type="button"
                                onClick={() => {
                                  if (!canRunTaskNow) {
                                    notify("error", blockedRunMessage || `${scheduledTaskRunActionLabel(task)} is not ready to run.`);
                                    return;
                                  }
                                  runScheduledTaskNowMutation.mutate(task);
                                }}
                              >
                                {scheduledTaskRunActionLabel(task)}
                              </button>
                            </div>
                          );
                        })}
                      </div>
                    </div>
                  ) : scheduledTasksQuery.isLoading ? (
                    <div className="helper-card">
                      <strong>Loading scheduled tasks</strong>
                      <p>Checking for plugin-owned scheduled work.</p>
                    </div>
                  ) : null}
                  <div className="plugin-config-footer">
                    {selectedPluginIsExternal ? (
                      <StatusBadge status={selectedPlugin.health_check_enabled === false ? "disabled" : selectedPlugin.health_status}>
                        {selectedPlugin.health_check_enabled === false ? "health disabled" : `health ${selectedPlugin.health_status}`}
                      </StatusBadge>
                    ) : null}
                    {!canUpdatePluginConfig && !canManageScheduledTasks ? (
                      <p>Your role can inspect plugin controls but cannot change them.</p>
                    ) : !pluginConfigDirty && !scheduledTaskConfigDirty ? (
                      <p>No unsaved plugin control changes.</p>
                    ) : (
                      <p>Unsaved plugin control changes.</p>
                    )}
                  </div>
                </section>
              ) : null}

              {selectedPluginIsExternal ? (
                <section className="plugin-config-panel">
                  <div className="panel-head">
                    <div>
                      <h4>Adapter connection</h4>
                      <p>Operator-owned connection settings and the admin-owned Bakery bootstrap credential.</p>
                    </div>
                    <StatusBadge
                      status={
                        canManageAdapterCredentials
                          ? operatorConfigQuery.data?.credential_configured
                            ? "healthy"
                            : "unknown"
                          : "unknown"
                      }
                    >
                      {canManageAdapterCredentials
                        ? operatorConfigQuery.data?.credential_configured
                          ? "bootstrap credential ready"
                          : "bootstrap credential missing"
                        : "operator config"}
                    </StatusBadge>
                  </div>
                  {canManageAdapterConfiguration ? (
                    <>
                      <div className="plugin-config-grid">
                        {operatorConfigFields(operatorConfigQuery.data?.config_schema, operatorConfigInput).map((field) => (
                          <FormField label={field.label} key={field.name}>
                            {field.type === "boolean" ? (
                              <label className="toggle-row plugin-toggle-field">
                                <input
                                  checked={Boolean(operatorConfigInput[field.name])}
                                  disabled={saveOperatorPluginConfigMutation.isPending}
                                  type="checkbox"
                                  onChange={(event) =>
                                    setOperatorConfigInput((current) => ({
                                      ...current,
                                      [field.name]: event.target.checked,
                                    }))
                                  }
                                />
                                <span>{operatorConfigInput[field.name] ? "Enabled" : "Disabled"}</span>
                              </label>
                            ) : (
                              <input
                                disabled={saveOperatorPluginConfigMutation.isPending}
                                type={
                                  field.name.includes("url")
                                    ? "url"
                                    : field.type === "number" || field.type === "integer"
                                      ? "number"
                                      : "text"
                                }
                                value={String(operatorConfigInput[field.name] ?? "")}
                                onChange={(event) =>
                                  setOperatorConfigInput((current) => ({
                                    ...current,
                                    [field.name]: event.target.value,
                                  }))
                                }
                              />
                            )}
                          </FormField>
                        ))}
                        {canEditAdapterCredentials ? (
                          <>
                            <FormField
                              label={credentialSlotLabel(operatorConfigQuery.data?.credential_type)}
                              help={credentialSlotHelp(operatorConfigQuery.data?.credential_type)}
                            >
                              <input
                                autoComplete="off"
                                disabled={saveOperatorPluginConfigMutation.isPending}
                                name={`credential-key-id-${operatorConfigQuery.data?.credential_type || "adapter"}`}
                                value={operatorCredentialKeyIdInput}
                                onChange={(event) => {
                                  setOperatorCredentialTouched(true);
                                  setOperatorCredentialKeyIdInput(event.target.value);
                                }}
                              />
                            </FormField>
                            {credentialPayloadFields(
                              editableOperatorCredentialRequirements,
                              operatorConfigQuery.data?.credential_type,
                            ).length === 1 ? (
                              <>
                                <FormField label="Credential field">
                                  <select
                                    disabled={saveOperatorPluginConfigMutation.isPending}
                                    name={`credential-field-${operatorConfigQuery.data?.credential_type || "adapter"}`}
                                    value={operatorCredentialField}
                                    onChange={(event) => {
                                      setOperatorCredentialTouched(true);
                                      setOperatorCredentialField(event.target.value);
                                    }}
                                  >
                                    {credentialFieldOptions(operatorConfigQuery.data?.credential_type).map((option) => (
                                      <option value={option.value} key={option.value}>{option.label}</option>
                                    ))}
                                  </select>
                                </FormField>
                                <FormField label={credentialValueLabel(operatorConfigQuery.data?.credential_type)}>
                                  <input
                                    autoComplete="new-password"
                                    disabled={
                                      saveOperatorPluginConfigMutation.isPending ||
                                      !operatorConfigQuery.data?.credential_type
                                    }
                                    name={`credential-value-${operatorConfigQuery.data?.credential_type || "adapter"}`}
                                    placeholder={operatorConfigQuery.data?.credential_configured ? "Leave blank to keep existing" : ""}
                                    type="password"
                                    value={operatorCredentialInput}
                                    onFocus={() => setOperatorCredentialTouched(true)}
                                    onChange={(event) => setOperatorCredentialInput(event.target.value)}
                                  />
                                </FormField>
                              </>
                            ) : (
                              credentialPayloadFields(
                                editableOperatorCredentialRequirements,
                                operatorConfigQuery.data?.credential_type,
                              ).map((field) => (
                                <FormField label={field.label} help={field.help} key={field.name}>
                                  <input
                                    autoComplete="new-password"
                                    disabled={saveOperatorPluginConfigMutation.isPending}
                                    name={`credential-${operatorConfigQuery.data?.credential_type || "adapter"}-${field.name}`}
                                    placeholder={operatorConfigQuery.data?.credential_configured ? "Leave blank to keep existing" : ""}
                                    type="password"
                                    value={operatorCredentialInputs[field.name] || ""}
                                    onFocus={() => setOperatorCredentialTouched(true)}
                                    onChange={(event) =>
                                      setOperatorCredentialInputs((current) => ({
                                        ...current,
                                        [field.name]: event.target.value,
                                      }))
                                    }
                                  />
                                </FormField>
                              ))
                            )}
                          </>
                        ) : null}
                      </div>
                      <div className="plugin-action-row">
                        <button
                          className="primary-button"
                          disabled={!canSaveOperatorPluginConfig || saveOperatorPluginConfigMutation.isPending}
                          type="button"
                          onClick={() => saveOperatorPluginConfigMutation.mutate()}
                        >
                          {saveOperatorPluginConfigMutation.isPending ? "Saving..." : "Save"}
                        </button>
                      </div>
                    </>
                  ) : null}
                  <div className="plugin-config-footer">
                    <p>
                      {canManageAdapterConfiguration
                        ? operatorConfigDirty || operatorCredentialDirty
                          ? "Unsaved adapter connection changes."
                          : "No unsaved adapter connection changes."
                        : "Only operators can view and change adapter connection settings."}
                    </p>
                  </div>
                </section>
              ) : null}

              <div className="kv-grid">
                <KeyValue label="Service type" value={selectedPlugin.service_type} />
                <KeyValue label="Short ID" value={selectedPlugin.plugin_short_id || "-"} />
                <KeyValue label="Type" value={selectedPlugin.plugin_type} />
                <KeyValue label="Tier" value={formatPluginTier(selectedPlugin.plugin_tier)} />
                <KeyValue label="Enabled" value={String(selectedPlugin.enabled)} />
                <KeyValue label="Run interval" value={selectedPlugin.run_interval_seconds ? `${selectedPlugin.run_interval_seconds} seconds` : "-"} />
                {selectedPluginSupportsQueryLimit ? (
                  <KeyValue label="Query limit" value={selectedPlugin.query_limit ? String(selectedPlugin.query_limit) : "-"} />
                ) : null}
                {selectedPluginIsExternal ? (
                  <>
                    <KeyValue label="Latency" value={selectedPlugin.health_latency_ms === null || selectedPlugin.health_latency_ms === undefined ? "-" : `${selectedPlugin.health_latency_ms} ms`} />
                    <KeyValue label="Consecutive failures" value={String(selectedPlugin.consecutive_failures)} />
                    <KeyValue label="Health order" value={selectedPlugin.health_check_order_id ? `#${selectedPlugin.health_check_order_id}` : "-"} />
                    <KeyValue label="Health task" value={selectedPlugin.health_check_task_id ? `#${selectedPlugin.health_check_task_id}` : "-"} />
                    <KeyValue label="Health interval" value={selectedPlugin.health_check_interval_seconds ? `${selectedPlugin.health_check_interval_seconds} seconds` : "-"} />
                    <KeyValue label="Health state" value={selectedPlugin.health_check_state || "idle"} />
                    <KeyValue label="Last health check" value={formatLongDate(selectedPlugin.last_health_check_at)} />
                    <KeyValue label="Last healthy check" value={formatLongDate(selectedPlugin.last_success_at)} />
                    <KeyValue label="Next health check" value={formatLongDate(selectedPlugin.next_health_check_at)} />
                    <KeyValue label="Last credential bootstrap" value={formatLongDate(selectedPlugin.last_credential_bootstrap_at)} />
                    <KeyValue label="Last credential rotation" value={formatLongDate(selectedPlugin.last_credential_rotation_at)} />
                    <KeyValue label="Helper available" value={String(selectedPlugin.helper_available)} />
                  </>
                ) : (
                  <>
                    <KeyValue label="State" value={selectedWorkerState} />
                    <KeyValue label="Ingredients" value={String(selectedPlugin.ingredient_template_count)} />
                    <KeyValue label="Recipes" value={String(selectedPlugin.recipe_template_count)} />
                  </>
                )}
              </div>

              <DetailList>
                <DetailRow label="Status message" value={selectedPlugin.status_message || "-"} />
                {selectedPluginIsExternal ? (
                  <>
                    <DetailRow label="Health message" value={selectedPlugin.health_message || "-"} />
                    <DetailRow label="Health error" value={selectedPlugin.health_error_code || "-"} />
                    <DetailRow label="Credential error" value={selectedPlugin.credential_error || "-"} />
                  </>
                ) : null}
              </DetailList>

              {selectedPluginIsExternal ? (
                <div className="grid-two">
                  <HelperCapabilityPanel
                    title="Provided helpers"
                    emptyMessage="This plugin does not expose shared helper capabilities."
                    capabilities={selectedPlugin.helper_capabilities}
                  />
                  <HelperRequirementPanel
                    required={selectedPlugin.required_helper_capabilities}
                    missing={selectedPlugin.missing_helper_capabilities}
                  />
                </div>
              ) : null}
            </div>
          ) : (
            <EmptyState message="Select a plugin to inspect its registration details." />
          )}
        </Panel>
      </div>
    </div>
  );
}

function HelperCapabilityPanel({
  title,
  capabilities,
  emptyMessage,
}: {
  title: string;
  capabilities: string[];
  emptyMessage: string;
}) {
  return (
    <div className="helper-card">
      <strong>{title}</strong>
      {capabilities.length ? (
        <div className="chip-list">
          {capabilities.map((capability) => (
            <span className="mini-chip" key={capability}>{capability}</span>
          ))}
        </div>
      ) : (
        <p>{emptyMessage}</p>
      )}
    </div>
  );
}

function HelperRequirementPanel({
  required,
  missing,
}: {
  required: Record<string, string[]>;
  missing: Record<string, string[]>;
}) {
  const providerNames = Array.from(new Set([...Object.keys(required), ...Object.keys(missing)])).sort();

  return (
    <div className="helper-card">
      <strong>Required helpers</strong>
      {providerNames.length ? (
        <div className="helper-requirement-list">
          {providerNames.map((provider) => (
            <div className="helper-requirement-row" key={provider}>
              <div>
                <span>{titleize(provider)}</span>
                <div className="chip-list">
                  {(required[provider] || []).map((capability) => (
                    <span className="mini-chip" key={`${provider}-${capability}`}>{capability}</span>
                  ))}
                </div>
              </div>
              {(missing[provider] || []).length ? (
                <StatusBadge status="unhealthy">missing {(missing[provider] || []).length}</StatusBadge>
              ) : (
                <StatusBadge status="healthy">satisfied</StatusBadge>
              )}
            </div>
          ))}
        </div>
      ) : (
        <p>No helper dependencies declared.</p>
      )}
    </div>
  );
}

function summarizeDishIngredients(
  ingredients: Array<Pick<DishIngredientStatusRecord, "service_exec_status" | "service_exec_run_time">>,
) {
  const statuses = ingredients.map((ingredient) => ingredient.service_exec_status).filter(Boolean);
  const terminalRank = ["errored", "failed", "timeout", "canceled", "running", "dispatched", "pending", "succeeded"];
  const status =
    terminalRank.find((candidate) => statuses.includes(candidate)) ||
    statuses[0] ||
    "";
  const runTime = ingredients.reduce<number | null>((total, ingredient) => {
    if (ingredient.service_exec_run_time === null || ingredient.service_exec_run_time === undefined) {
      return total;
    }
    return (total || 0) + ingredient.service_exec_run_time;
  }, null);
  return {
    status,
    runTime,
  };
}

function displayDishStatus(
  dish: DishStatusRecord,
  ingredientSummary?: ReturnType<typeof summarizeDishIngredients>,
): string {
  return (
    dish.dish_exec_status ||
    ingredientSummary?.status ||
    (dish.processing_status === "complete" ? "succeeded" : dish.processing_status) ||
    "pending"
  );
}

function displayDishDuration(
  dish: DishStatusRecord,
  ingredientSummary?: ReturnType<typeof summarizeDishIngredients>,
): string {
  const duration =
    dish.work_execution_time_secs ??
    ingredientSummary?.runTime ??
    dish.run_time_secs ??
    elapsedSeconds(dish.started_at, dish.completed_at);
  return duration === null ? "Pending" : `${duration}s`;
}

function displayDishWallTime(dish: DishStatusRecord): string {
  const duration = dish.run_time_secs ?? elapsedSeconds(dish.started_at, dish.completed_at);
  return duration === null ? "Pending" : `${duration}s`;
}

function elapsedSeconds(start?: string | null, end?: string | null): number | null {
  if (!start || !end) {
    return null;
  }
  const startTime = new Date(start).getTime();
  const endTime = new Date(end).getTime();
  if (Number.isNaN(startTime) || Number.isNaN(endTime) || endTime < startTime) {
    return null;
  }
  return Math.max(0, Math.round((endTime - startTime) / 1000));
}

function OrdersPage() {
  const { orderId } = useParams();
  const navigate = useNavigate();
  const [searchParams] = useSearchParams();
  const [statusFilter, setStatusFilter] = useState("");
  const [search, setSearch] = useState("");
  const deferredSearch = useDeferredValue(search);

  const incidentsQuery = useQuery({
    queryKey: ["incidents"],
    queryFn: () => apiGet(
      "/api/v1/orders/status?limit=100&order_scope=operator",
      orderStatusRecordArraySchema,
    ),
  });

  const selectedId = orderId ? Number(orderId) : null;
  const selectedFromRoute = selectedId && incidentsQuery.data
    ? incidentsQuery.data.find((item) => item.id === selectedId)
    : undefined;

  const selectedIncidentQuery = useQuery({
    queryKey: ["incident", selectedId],
    enabled: Boolean(selectedId) && Boolean(incidentsQuery.data) && !selectedFromRoute,
    queryFn: () => apiGet(`/api/v1/orders/${selectedId}/status`, orderStatusRecordSchema),
  });

  const incidentRows = incidentsQuery.data || [];
  const filtered = incidentRows.filter((incident) => {
    if (statusFilter && incident.processing_status !== statusFilter) {
      return false;
    }
    if (!deferredSearch) {
      return true;
    }
    const haystack = [
      incident.alert_group_name,
      incident.instance,
      incident.severity,
      incident.req_id,
    ]
      .filter(Boolean)
      .join(" ")
      .toLowerCase();
    return haystack.includes(deferredSearch.toLowerCase());
  });

  const activeSelection = selectedFromRoute || selectedIncidentQuery.data || filtered[0];
  const timelineTargetId = activeSelection?.id || selectedId;
  const timelineQuery = useQuery({
    queryKey: ["incident-timeline", timelineTargetId],
    enabled: Boolean(timelineTargetId),
    queryFn: () => apiGet(`/api/v1/orders/${timelineTargetId}/timeline`, incidentTimelineResponseSchema),
  });

  useEffect(() => {
    if (!selectedId && activeSelection) {
      startTransition(() => {
        navigate(`/orders/${activeSelection.id}`, { replace: true });
      });
    }
  }, [activeSelection, navigate, selectedId]);

  if (incidentsQuery.isLoading) {
    return <PageLoading message="Loading orders and current dish state." />;
  }

  if (incidentsQuery.isError || !incidentsQuery.data) {
    return <PageError message={getErrorMessage(incidentsQuery.error)} />;
  }

  return (
    <div className="page-stack">
      <PageHeader
        title="Orders"
        description="Track webhook orders created from alerts, then drill into dish execution and communication routes."
      />

      <div className="toolbar">
        <label>
          Lifecycle
          <select value={statusFilter} onChange={(event) => setStatusFilter(event.target.value)}>
            <option value="">All</option>
            <option value="new">New</option>
            <option value="processing">Processing</option>
            <option value="complete">Complete</option>
            <option value="failed">Failed</option>
            <option value="canceled">Canceled</option>
          </select>
        </label>
        <label className="toolbar-search">
          Search
          <input
            value={search}
            onChange={(event) => setSearch(event.target.value)}
            placeholder="Alert name, instance, request id"
          />
        </label>
      </div>

      <div className="master-detail">
        <Panel title="Order Queue" subtitle={`${filtered.length} orders in view.`}>
          <div className="list-stack incident-list">
            {filtered.length ? (
              filtered.map((incident) => (
                <button
                  className={`incident-row ${timelineTargetId === incident.id ? "active" : ""}`}
                  key={incident.id}
                  type="button"
                  onClick={() => startTransition(() => navigate(`/orders/${incident.id}`))}
                >
                  <div>
                    <strong>{incident.alert_group_name}</strong>
                    <p>
                      {incident.instance || "No instance"} • {incident.severity || "unknown severity"} •{" "}
                      {incident.communication_route_count} route(s)
                    </p>
                  </div>
                  <div className="feed-meta">
                    <StatusBadge status={incident.processing_status}>{incident.processing_status}</StatusBadge>
                    <span>{formatDate(incident.updated_at)}</span>
                  </div>
                </button>
              ))
            ) : (
              <EmptyState message="No orders match the current filters." />
            )}
          </div>
        </Panel>

        <Panel title="Order Drilldown" subtitle="Status, ticketing, chat routes, and full timeline in one place.">
          {!timelineTargetId ? (
            <EmptyState message="Select an order to inspect its current state." />
          ) : timelineQuery.isLoading || selectedIncidentQuery.isLoading ? (
            <EmptyState message="Select an order to inspect its current state." />
          ) : timelineQuery.isError || selectedIncidentQuery.isError || !timelineQuery.data ? (
            <PageError message={getErrorMessage(timelineQuery.error || selectedIncidentQuery.error)} compact />
          ) : (
            <IncidentDetail
              data={timelineQuery.data as IncidentTimelineResponse}
              highlightedCommunicationId={searchParams.get("communication") || undefined}
              highlightedDishId={searchParams.get("dish") || undefined}
            />
          )}
        </Panel>
      </div>
    </div>
  );
}

function IncidentDetail({
  data,
  highlightedCommunicationId,
  highlightedDishId,
}: {
  data: IncidentTimelineResponse;
  highlightedCommunicationId?: string;
  highlightedDishId?: string;
}) {
  const order = data.order;

  return (
    <div className="detail-stack">
      <section className="detail-hero">
        <div>
          <div className="eyebrow">Order #{order.id}</div>
          <h3>{order.alert_group_name}</h3>
          <p>
            {order.instance || "No instance"} • {order.severity || "unknown severity"} • started{" "}
            {formatLongDate(order.starts_at)}
          </p>
        </div>
        <div className="drilldown-status-list">
          <StatusListItem label="Lifecycle" value={order.processing_status} />
          <StatusListItem label="Alert state" value={order.alert_status} />
          <StatusListItem label="Lifetime" value={order.order_lifetime_secs === null || order.order_lifetime_secs === undefined ? "Running" : `${order.order_lifetime_secs}s`} />
          <StatusListItem label="Recipe outcome" value={order.remediation_outcome} />
          <StatusListItem label="Routes" value={String(order.communication_route_count)} />
        </div>
      </section>

      <div className="kv-grid">
        <KeyValue label="Request ID" value={order.req_id} />
        <KeyValue label="Counter" value={String(order.counter)} />
        <KeyValue label="Order lifetime" value={order.order_lifetime_secs === null || order.order_lifetime_secs === undefined ? "Running" : `${order.order_lifetime_secs}s`} />
        <KeyValue label="Auto-close eligible" value={String(order.auto_close_eligible)} />
        <KeyValue label="Clear deadline" value={formatLongDate(order.clear_deadline_at)} />
      </div>

      <section>
        <div className="section-heading">
          <h4>Timeline</h4>
          <p>Dish steps, communication updates, and order state transitions in chronological order.</p>
        </div>
        {highlightedDishId ? (
          <div className="helper-card">
            <strong>Selected dish execution</strong>
            <p>Timeline events related to dish #{highlightedDishId} are highlighted below.</p>
          </div>
        ) : null}
        <div className="timeline">
          {data.events.map((event, index) => (
            <div
              className={`timeline-row ${
                isTimelineEventHighlighted(event, highlightedCommunicationId, highlightedDishId) ? "highlighted" : ""
              }`}
              key={`${event.event_type}-${index}-${event.timestamp}`}
            >
              <div className="timeline-dot" />
              <div className="timeline-body">
                <div className="timeline-head">
                  <strong>{event.title}</strong>
                  <div className="feed-meta">
                    <StatusBadge status={event.status}>{event.status}</StatusBadge>
                    <span>{formatDate(event.timestamp)}</span>
                  </div>
                </div>
                <p>{titleize(event.event_type)}</p>
                {Object.keys(event.details || {}).length > 0 ? (
                  <pre className="json-block">{compactJson(event.details)}</pre>
                ) : null}
              </div>
            </div>
          ))}
        </div>
      </section>
    </div>
  );
}

function CommunicationRoutesPage() {
  const [search, setSearch] = useState("");
  const [statusFilter, setStatusFilter] = useState("");
  const [channelFilter, setChannelFilter] = useState("");
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const deferredSearch = useDeferredValue(search);

  const query = useQuery({
    queryKey: ["communications-activity"],
    queryFn: () => apiGet("/api/v1/communications/activity?limit=200", communicationActivityRecordArraySchema),
  });

  if (query.isLoading) {
    return <PageLoading message="Loading ticketing and chat delivery history." />;
  }

  if (query.isError || !query.data) {
    return <PageError message={getErrorMessage(query.error)} />;
  }

  const rows = query.data.filter((item) => {
    if (statusFilter && statusFilter !== (item.remote_state || item.lifecycle_state || "")) {
      return false;
    }
    if (channelFilter && channelFilter !== item.channel) {
      return false;
    }
    if (!deferredSearch) {
      return true;
    }
    const haystack = [
      item.reference_name,
      item.destination,
      item.ticket_id,
      item.provider_reference_id,
      item.channel,
    ]
      .filter(Boolean)
      .join(" ")
      .toLowerCase();
    return haystack.includes(deferredSearch.toLowerCase());
  });

  const selected = rows.find((item) => item.communication_id === selectedId) || rows[0];
  const channels = Array.from(new Set(query.data.map((item) => item.channel))).sort();

  return (
    <div className="page-stack">
      <PageHeader
        title="Communication Routes"
        description="Unified outbound history for ticketing and chat channels, with ticket numbers, provider references, and latest delivery state."
      />
      <div className="toolbar">
        <label>
          Channel
          <select value={channelFilter} onChange={(event) => setChannelFilter(event.target.value)}>
            <option value="">All</option>
            {channels.map((channel) => (
              <option value={channel} key={channel}>
                {titleize(channel)}
              </option>
            ))}
          </select>
        </label>
        <label>
          Status
          <select value={statusFilter} onChange={(event) => setStatusFilter(event.target.value)}>
            <option value="">All</option>
            {Array.from(new Set(query.data.map((item) => item.remote_state || item.lifecycle_state || "unknown"))).map((status) => (
              <option value={status} key={status}>
                {status}
              </option>
            ))}
          </select>
        </label>
        <label className="toolbar-search">
          Search
          <input value={search} onChange={(event) => setSearch(event.target.value)} placeholder="Destination, ticket number, order" />
        </label>
      </div>

      <div className="master-detail">
        <Panel title="Outbound history" subtitle={`${rows.length} records in view.`}>
          <div className="list-stack incident-list">
            {rows.map((item) => (
              <button
                className={`incident-row ${selected?.communication_id === item.communication_id ? "active" : ""}`}
                key={item.communication_id}
                type="button"
                onClick={() => setSelectedId(item.communication_id)}
              >
                <div>
                  <strong>{item.reference_name || item.reference_id}</strong>
                  <p>
                    {titleize(item.channel)} • {item.destination || "No destination"} •{" "}
                    {item.ticket_id || item.provider_reference_id || "Pending reference"}
                  </p>
                </div>
                <div className="feed-meta">
                  <StatusBadge status={item.remote_state || item.lifecycle_state}>
                    {item.remote_state || item.lifecycle_state || "unknown"}
                  </StatusBadge>
                  <span>{formatDate(item.updated_at)}</span>
                </div>
              </button>
            ))}
          </div>
        </Panel>

        <Panel title="Selected route" subtitle="Current status, provider references, and last known error.">
          {selected ? (
            <div className="detail-stack">
              <DetailList>
                <DetailRow label="Reference type" value={selected.reference_type} />
                <DetailRow label="Channel" value={titleize(selected.channel)} />
                <DetailRow label="Destination" value={selected.destination || "-"} />
                <DetailRow label="Ticket number" value={selected.ticket_id || "-"} />
                <DetailRow label="Provider reference" value={selected.provider_reference_id || "-"} />
                <DetailRow label="Operation ID" value={selected.operation_id || "-"} />
                <DetailRow label="Lifecycle state" value={selected.lifecycle_state || "-"} />
                <DetailRow label="Remote state" value={selected.remote_state || "-"} />
                <DetailRow label="Writable" value={selected.writable === null || selected.writable === undefined ? "-" : String(selected.writable)} />
                <DetailRow label="Reopenable" value={selected.reopenable === null || selected.reopenable === undefined ? "-" : String(selected.reopenable)} />
                <DetailRow label="Last update" value={formatLongDate(selected.updated_at)} />
                <DetailRow label="Last error" value={selected.last_error || "-"} />
              </DetailList>
            </div>
          ) : (
            <EmptyState message="Select a communication record to inspect it." />
          )}
        </Panel>
      </div>
    </div>
  );
}

function SuppressionsPage() {
  const notify = useToast();
  const principal = usePrincipal();
  const queryClient = useQueryClient();
  const [searchParams] = useSearchParams();
  const canEdit = canManageSuppressions(principal);

  const suppressionsQuery = useQuery({
    queryKey: ["suppressions"],
    queryFn: () => apiGet("/api/v1/suppressions?limit=100", suppressionRecordArraySchema),
  });
  const recipesQuery = useQuery({
    queryKey: ["suppression-recipes"],
    queryFn: () => apiGet("/api/v1/recipes/status?limit=500", recipeStatusRecordArraySchema),
  });

  const form = useForm<z.infer<typeof suppressionSchema>>({
    resolver: zodResolver(suppressionSchema),
    defaultValues: {
      name: "",
      reason: "",
      starts_at: "",
      ends_at: "",
      scope: "matchers",
      summary_ticket_enabled: true,
      matcher_key: "alertname",
      matcher_operator: "eq",
      matcher_value: "",
    },
  });
  const matcherKey = form.watch("matcher_key");
  const matcherOperator = form.watch("matcher_operator");
  const recipeNameOptions = (recipesQuery.data || [])
    .map((recipe) => recipe.name)
    .filter(Boolean)
    .sort((left, right) => left.localeCompare(right));

  const createMutation = useMutation({
    mutationFn: async (values: z.infer<typeof suppressionSchema>) => {
      const request = suppressionCreateRequestSchema.parse({
        name: values.name,
        reason: values.reason || null,
        starts_at: values.starts_at,
        ends_at: values.ends_at,
        scope: values.scope,
        enabled: true,
        created_by: "ui-v2",
        summary_ticket_enabled: values.summary_ticket_enabled,
        matchers:
          values.scope === "matchers" && values.matcher_key
            ? [
                {
                  label_key: values.matcher_key,
                  operator: values.matcher_operator,
                  value: values.matcher_value || null,
                },
              ]
            : [],
      });
      return apiPost("/api/v1/suppressions", suppressionRecordSchema, request);
    },
    onSuccess: async () => {
      notify("success", "Suppression created.");
      form.reset({
        name: "",
        reason: "",
        starts_at: "",
        ends_at: "",
        scope: "matchers",
        summary_ticket_enabled: true,
        matcher_key: "alertname",
        matcher_operator: "eq",
        matcher_value: "",
      });
      await queryClient.invalidateQueries({ queryKey: ["suppressions"] });
    },
    onError: (error) => notify("error", getErrorMessage(error)),
  });

  const cancelMutation = useMutation({
    mutationFn: (id: number) => apiPost(`/api/v1/suppressions/${id}/cancel`, suppressionRecordSchema),
    onSuccess: async () => {
      notify("success", "Suppression canceled.");
      await queryClient.invalidateQueries({ queryKey: ["suppressions"] });
    },
    onError: (error) => notify("error", getErrorMessage(error)),
  });

  if (suppressionsQuery.isLoading) {
    return <PageLoading message="Loading suppression windows and impact status." />;
  }

  if (suppressionsQuery.isError || !suppressionsQuery.data) {
    return <PageError message={getErrorMessage(suppressionsQuery.error)} />;
  }

  const focusedId = searchParams.get("suppression");

  return (
    <div className="page-stack">
      <PageHeader
        title="Suppressions"
        description="Manage temporary monitoring suppressions and see which windows are active, scheduled, or already expired."
      />

      <div className="editor-grid">
        <Panel title="Create suppression" subtitle="Use clear dates and matcher scope so operators know exactly what is being muted.">
          {!canEdit ? (
            <div className="helper-card">
              <strong>Read-only access</strong>
              <p>Your role can review suppressions but cannot create or cancel them.</p>
            </div>
          ) : null}
          <form className="form-stack" onSubmit={form.handleSubmit((values) => createMutation.mutate(values))}>
            <fieldset disabled={!canEdit}>
            <FormField label="Suppression name" help="Use a human-readable maintenance or outage label.">
              <input {...form.register("name")} placeholder="Database maintenance" />
              <FieldError message={form.formState.errors.name?.message} />
            </FormField>
            <FormField label="Reason" help="Explain why alerts are being suppressed and who requested it.">
              <textarea {...form.register("reason")} rows={3} />
            </FormField>
            <div className="grid-two">
              <FormField label="Starts at" help="Start of the suppression window in local time.">
                <input type="datetime-local" {...form.register("starts_at")} />
                <FieldError message={form.formState.errors.starts_at?.message} />
              </FormField>
              <FormField label="Ends at" help="End of the suppression window in local time.">
                <input type="datetime-local" {...form.register("ends_at")} />
                <FieldError message={form.formState.errors.ends_at?.message} />
              </FormField>
            </div>
            <div className="grid-two">
              <FormField label="Scope" help="Matcher scope targets alerts by label rather than silencing everything globally.">
                <select {...form.register("scope")}>
                  <option value="matchers">Matchers</option>
                  <option value="all">All</option>
                </select>
              </FormField>
              <FormField label="Summary communication" help="Enable this when you want the suppression lifecycle summarized into a ticket.">
                <label className="toggle-row">
                  <input type="checkbox" {...form.register("summary_ticket_enabled")} />
                  <span>Send summary communication</span>
                </label>
              </FormField>
            </div>
            <div className="grid-three">
              <FormField label="Matcher key" help="The alert label to match, such as alertname or cluster.">
                <input {...form.register("matcher_key")} list="suppression-matcher-key-options" />
                <datalist id="suppression-matcher-key-options">
                  <option value="alertname" />
                  <option value="recipe.name" />
                  <option value="cluster" />
                  <option value="namespace" />
                  <option value="instance" />
                  <option value="severity" />
                </datalist>
              </FormField>
              <FormField label="Operator" help="eq matches exact values; regex allows pattern matching.">
                <select {...form.register("matcher_operator")}>
                  <option value="eq">eq</option>
                  <option value="neq">neq</option>
                  <option value="regex">regex</option>
                  <option value="nregex">nregex</option>
                  <option value="exists">exists</option>
                  <option value="not_exists">not_exists</option>
                </select>
              </FormField>
              <SuppressionMatcherValueField
                form={form}
                matcherKey={matcherKey}
                matcherOperator={matcherOperator}
                recipeNames={recipeNameOptions}
                recipesLoading={recipesQuery.isLoading}
              />
            </div>
            <div className="form-actions">
              <button className="primary-button" disabled={createMutation.isPending} type="submit">
                {createMutation.isPending ? "Creating..." : "Create suppression"}
              </button>
            </div>
            </fieldset>
          </form>
        </Panel>

        <Panel title="Suppression windows" subtitle="Click any window to see its current status and cancel active ones.">
          <div className="list-stack">
            {suppressionsQuery.data.map((item) => (
              <div className={`feed-row card-row ${focusedId === String(item.id) ? "highlighted" : ""}`} key={item.id}>
                <div>
                  <strong>{item.name}</strong>
                  <p>
                    {item.reason || "No reason provided."} • {formatDate(item.starts_at)} to{" "}
                    {formatDate(item.ends_at)}
                  </p>
                </div>
                <div className="feed-meta">
                  <StatusBadge status={item.status}>{item.status}</StatusBadge>
                  <button
                    className="ghost-button"
                    disabled={!canEdit || cancelMutation.isPending || item.status === "canceled"}
                    type="button"
                    onClick={() => {
                      if (window.confirm(`Cancel suppression "${item.name}"?`)) {
                        cancelMutation.mutate(item.id);
                      }
                    }}
                  >
                    Cancel
                  </button>
                </div>
              </div>
            ))}
          </div>
        </Panel>
      </div>
    </div>
  );
}

function SuppressionMatcherValueField({
  form,
  matcherKey,
  matcherOperator,
  recipeNames,
  recipesLoading,
}: {
  form: ReturnType<typeof useForm<z.infer<typeof suppressionSchema>>>;
  matcherKey?: string;
  matcherOperator?: string;
  recipeNames: string[];
  recipesLoading: boolean;
}) {
  const usesRecipeName = matcherKey === "recipe.name";
  const valueNotUsed = matcherOperator === "exists" || matcherOperator === "not_exists";
  const exactRecipeMatch = usesRecipeName && (matcherOperator === "eq" || matcherOperator === "neq");

  useEffect(() => {
    if (exactRecipeMatch && recipeNames.length === 1 && !form.getValues("matcher_value")) {
      form.setValue("matcher_value", recipeNames[0]);
    }
  }, [exactRecipeMatch, form, recipeNames]);

  if (valueNotUsed) {
    return (
      <FormField label="Matcher value" help="Value is ignored for exists and not_exists operators.">
        <input disabled value="" />
      </FormField>
    );
  }

  if (exactRecipeMatch) {
    return (
      <FormField label="Matcher value" help="Choose the recipe name this suppression should target.">
        <select {...form.register("matcher_value")} disabled={recipesLoading || !recipeNames.length}>
          <option value="">{recipesLoading ? "Loading recipes..." : "Choose a recipe"}</option>
          {recipeNames.map((name) => (
            <option key={name} value={name}>
              {name}
            </option>
          ))}
        </select>
      </FormField>
    );
  }

  if (usesRecipeName) {
    return (
      <FormField label="Matcher value" help="Use a recipe name or pattern. Known recipes are available as autocomplete suggestions.">
        <input {...form.register("matcher_value")} list="suppression-recipe-name-options" />
        <datalist id="suppression-recipe-name-options">
          {recipeNames.map((name) => (
            <option key={name} value={name} />
          ))}
        </datalist>
      </FormField>
    );
  }

  return (
    <FormField label="Matcher value" help="Leave value blank for exists and not_exists operators.">
      <input {...form.register("matcher_value")} />
    </FormField>
  );
}

function dishIngredientStatusDetails(ingredient: DishIngredientStatusRecord): Record<string, unknown> {
  const details: Record<string, unknown> = {
    role: ingredient.execution_role,
    operation: ingredient.operation,
    result: ingredient.result_status,
    message: ingredient.result_message,
    summary: ingredient.result_summary,
  };
  return Object.fromEntries(
    Object.entries(details).filter(([, value]) => {
      if (value === undefined || value === null || value === "") {
        return false;
      }
      if (typeof value === "object" && !Array.isArray(value) && Object.keys(value).length === 0) {
        return false;
      }
      return true;
    }),
  );
}

function hasDishIngredientStatusDetails(ingredient: DishIngredientStatusRecord): boolean {
  return Object.keys(dishIngredientStatusDetails(ingredient)).length > 0;
}

function ExecutionActivityPage() {
  const [searchParams, setSearchParams] = useSearchParams();
  const [statusFilter, setStatusFilter] = useState("");
  const [phaseFilter, setPhaseFilter] = useState("");
  const [search, setSearch] = useState("");
  const deferredSearch = useDeferredValue(search);
  const query = useQuery({
    queryKey: ["activity-dishes", "operator"],
    queryFn: () => apiGet("/api/v1/dishes/status?limit=100&order_scope=operator", dishStatusRecordArraySchema),
  });

  const selectedDishId = searchParams.get("dish");
  const activityRows = query.data || [];
  const rows = activityRows.filter((item) => {
    if (statusFilter && statusFilter !== (item.dish_exec_status || item.processing_status || "")) {
      return false;
    }
    if (phaseFilter && phaseFilter !== item.run_phase) {
      return false;
    }
    if (!deferredSearch) {
      return true;
    }
    const haystack = [
      item.recipe_name,
      item.dish_exec_status,
      item.run_phase,
      item.order_id ? `order ${item.order_id}` : "",
    ]
      .filter(Boolean)
      .join(" ")
      .toLowerCase();
    return haystack.includes(deferredSearch.toLowerCase());
  });

  const selected =
    (selectedDishId ? activityRows.find((item) => String(item.id) === selectedDishId) : undefined) || rows[0];
  const selectedIngredientsQuery = useQuery({
    queryKey: ["activity-dish-ingredients", selected?.id],
    enabled: Boolean(selected?.id),
    queryFn: () =>
      apiGet(
        `/api/v1/dishes/${selected?.id}/ingredient-status`,
        dishIngredientStatusRecordArraySchema,
      ),
  });
  const selectedIngredientRows = selectedIngredientsQuery.data || [];
  const selectedIngredientSummary = summarizeDishIngredients(selectedIngredientRows);
  const selectedStatus = selected ? displayDishStatus(selected, selectedIngredientSummary) : "";
  const selectedDuration = selected ? displayDishDuration(selected, selectedIngredientSummary) : "Pending";

  useEffect(() => {
    if (!selectedDishId && rows[0]) {
      const nextParams = new URLSearchParams(searchParams);
      nextParams.set("dish", String(rows[0].id));
      setSearchParams(nextParams, { replace: true });
    }
  }, [rows, searchParams, selectedDishId, setSearchParams]);

  function selectDish(dishId: number) {
    const nextParams = new URLSearchParams(searchParams);
    nextParams.set("dish", String(dishId));
    setSearchParams(nextParams);
  }

  if (query.isLoading) {
    return <PageLoading message="Loading dish work execution activity." />;
  }

  if (query.isError || !query.data) {
    return <PageError message={getErrorMessage(query.error)} />;
  }

  return (
    <div className="page-stack">
      <PageHeader
        title="Work Execution Activity"
        description="Dish work execution history across orders, with quick links back to the originating order when one exists."
      />
      <div className="toolbar">
        <label>
          Phase
          <select value={phaseFilter} onChange={(event) => setPhaseFilter(event.target.value)}>
            <option value="">All</option>
            {Array.from(new Set(query.data.map((item) => item.run_phase))).sort().map((phase) => (
              <option key={phase} value={phase}>
                {titleize(phase)}
              </option>
            ))}
          </select>
        </label>
        <label>
          Status
          <select value={statusFilter} onChange={(event) => setStatusFilter(event.target.value)}>
            <option value="">All</option>
            {Array.from(new Set(query.data.map((item) => item.dish_exec_status || item.processing_status || "unknown"))).sort().map((status) => (
              <option key={status} value={status}>
                {titleize(status)}
              </option>
            ))}
          </select>
        </label>
        <label className="toolbar-search">
          Search
          <input
            value={search}
            onChange={(event) => setSearch(event.target.value)}
            placeholder="Recipe, phase, status, order"
          />
        </label>
      </div>

      <div className="master-detail">
        <Panel title="Dish Work Executions" subtitle={`${rows.length} activity records in view.`}>
          <div className="list-stack incident-list">
            {rows.length ? (
              rows.map((item) => (
                <button
                  className={`incident-row ${selected?.id === item.id ? "active" : ""}`}
                  key={item.id}
                  type="button"
                  onClick={() => selectDish(item.id)}
                >
                  <div>
                    <strong>{item.recipe_name || `Recipe #${item.recipe_id}`}</strong>
                    <p>
                      {titleize(item.run_phase)} • {item.dish_exec_status || item.processing_status || "Work execution pending"} •{" "}
                      {item.order_id ? `Order #${item.order_id}` : "No order linked"}
                    </p>
                  </div>
                  <div className="feed-meta">
                    <StatusBadge status={displayDishStatus(item)}>
                      {displayDishStatus(item)}
                    </StatusBadge>
                    <span>{formatDate(item.updated_at)}</span>
                  </div>
                </button>
              ))
            ) : (
              <EmptyState message="No dish executions match the current filters." />
            )}
          </div>
        </Panel>

        <Panel title="Work Execution Drilldown" subtitle="Dish details, order linkage, and the latest work execution outcome.">
          {selected ? (
            <div className="detail-stack">
              <section className="detail-hero">
                <div>
                  <div className="eyebrow">Dish #{selected.id}</div>
                  <h3>{selected.recipe_name || `Recipe #${selected.recipe_id}`}</h3>
                  <p>
                    {titleize(selected.run_phase)} phase • updated{" "}
                    {formatLongDate(selected.updated_at)}
                  </p>
                </div>
                <div className="drilldown-status-list">
                  <StatusListItem label="Processing" value={selected.processing_status} />
                  <StatusListItem label="Work execution" value={selectedStatus} />
                  <StatusListItem label="Work time" value={selectedDuration} />
                  <StatusListItem label="Order" value={selected.order_id ? `#${selected.order_id}` : "Unlinked"} />
                </div>
              </section>

              <div className="form-actions">
                {selected.order_id ? (
                  <Link className="primary-button" to={`/orders/${selected.order_id}?dish=${selected.id}`}>
                    Open order drilldown
                  </Link>
                ) : null}
              </div>

              <div className="kv-grid">
                <KeyValue label="Recipe" value={selected.recipe_name || `Recipe #${selected.recipe_id}`} />
                <KeyValue label="Order" value={selected.order_id ? `Order #${selected.order_id}` : "-"} />
                <KeyValue label="Phase" value={titleize(selected.run_phase)} />
                <KeyValue label="Processing status" value={selected.processing_status} />
                <KeyValue label="Work execution status" value={selectedStatus || "-"} />
                <KeyValue label="Expected work time" value={selected.expected_run_secs ? `${selected.expected_run_secs}s` : "-"} />
                <KeyValue label="Work execution time" value={selectedDuration} />
                <KeyValue label="Wall time" value={displayDishWallTime(selected)} />
                <KeyValue label="Started" value={formatLongDate(selected.started_at)} />
                <KeyValue label="Completed" value={formatLongDate(selected.completed_at)} />
                <KeyValue label="Created" value={formatLongDate(selected.created_at)} />
                <KeyValue label="Updated" value={formatLongDate(selected.updated_at)} />
              </div>

              {selectedIngredientsQuery.isLoading ? (
                <div className="helper-card">
                  <strong>Loading dish-step outcomes</strong>
                  <p>Fetching dish-step work execution details for this dish.</p>
                </div>
              ) : (
                <div className="helper-card">
                  <strong>Sanitized execution status</strong>
                  <p>
                    Reader-safe dish-step status includes phase, operation, result, and adapter-provided summaries after control-plane sanitization.
                  </p>
                </div>
              )}
              {selectedIngredientRows.length ? (
                <section>
                  <div className="section-heading">
                    <h4>Dish Steps</h4>
                    <p>
                      Step-level work execution state and sanitized evidence reported by Cook, Expediter, and adapter reconciliation.
                    </p>
                  </div>
                  {selected.work_execution_groups?.length ? (
                    <div className="compact-metric-row">
                      {selected.work_execution_groups.map((group) => (
                        <MetricPill
                          key={`${group.depth}-${group.parallel_group}`}
                          label={`Depth ${group.depth} / group ${group.parallel_group}`}
                          value={`${group.total_seconds}s across ${group.rows}`}
                        />
                      ))}
                    </div>
                  ) : null}
                  <div className="list-stack">
                    {selectedIngredientRows.map((ingredient) => (
                      <div
                        className="feed-row"
                        key={ingredient.id}
                      >
                        <div className="execution-history-head">
                          <div>
                            <strong>
                              {ingredient.execution_role ? `${titleize(ingredient.execution_role)}: ` : ""}
                              {ingredient.task_key || `${ingredient.service_type}:${ingredient.service_exec}`}
                            </strong>
                            <p>
                              {ingredient.service_type || "unknown"} • {ingredient.service_exec || "unknown"} • attempt{" "}
                              {ingredient.attempt}
                            </p>
                          </div>
                          <div className="feed-meta">
                            <StatusBadge status={ingredient.service_exec_status}>{ingredient.service_exec_status}</StatusBadge>
                            <span>{ingredient.service_exec_run_time === null || ingredient.service_exec_run_time === undefined ? "Pending" : `${ingredient.service_exec_run_time}s`}</span>
                          </div>
                        </div>
                        {hasDishIngredientStatusDetails(ingredient) ? (
                          <pre className="json-block">
                            {compactJson(dishIngredientStatusDetails(ingredient))}
                          </pre>
                        ) : null}
                      </div>
                    ))}
                  </div>
                </section>
              ) : null}
            </div>
          ) : (
            <EmptyState message="Select a dish execution to inspect its details." />
          )}
        </Panel>
      </div>
    </div>
  );
}

function SystemActivityPage() {
  const [search, setSearch] = useState("");
  const deferredSearch = useDeferredValue(search);
  const query = useQuery({
    queryKey: ["system-activity"],
    queryFn: async () => {
      const [tasks, orders, dishes] = await Promise.all([
        apiGet("/api/v1/scheduled-tasks/status", scheduledTaskStatusRecordArraySchema),
        apiGet("/api/v1/orders/status?limit=100&order_scope=system", orderStatusRecordArraySchema),
        apiGet("/api/v1/dishes/status?limit=100&order_scope=system", dishStatusRecordArraySchema),
      ]);
      return { tasks, orders, dishes };
    },
  });

  if (query.isLoading) {
    return <PageLoading message="Loading system activity." />;
  }

  if (query.isError || !query.data) {
    return <PageError message={getErrorMessage(query.error)} />;
  }

  const { tasks, orders, dishes } = query.data;
  const normalizedSearch = deferredSearch.trim().toLowerCase();
  const visibleOrders = orders.filter((order) => {
    if (!normalizedSearch) return true;
    return [
      order.alert_group_name,
      order.req_id,
      order.order_type,
      order.processing_status,
    ]
      .join(" ")
      .toLowerCase()
      .includes(normalizedSearch);
  });
  const visibleDishes = dishes.filter((dish) => {
    if (!normalizedSearch) return true;
    return [
      dish.recipe_name,
      dish.order_type,
      dish.processing_status,
      dish.dish_exec_status,
      dish.order_id ? `order ${dish.order_id}` : "",
    ]
      .filter(Boolean)
      .join(" ")
      .toLowerCase()
      .includes(normalizedSearch);
  });
  const visibleTasks = tasks.filter((task) => {
    if (!normalizedSearch) return true;
    return [
      task.task_key,
      task.task_type,
      task.service_type,
      task.service_exec,
      task.status,
      task.last_status,
    ]
      .filter(Boolean)
      .join(" ")
      .toLowerCase()
      .includes(normalizedSearch);
  });

  return (
    <div className="page-stack">
      <PageHeader
        title="System Activity"
        description="Internal scheduled work, plugin health checks, sync jobs, and their execution history."
      />
      <div className="toolbar">
        <label className="toolbar-search">
          Search
          <input
            value={search}
            onChange={(event) => setSearch(event.target.value)}
            placeholder="Task, plugin, recipe, request id"
          />
        </label>
      </div>

      <div className="dashboard-grid">
        <MetricCard title="Scheduled Tasks" value={String(visibleTasks.length)} tone="neutral" />
        <MetricCard title="System Orders" value={String(visibleOrders.length)} tone="neutral" />
        <MetricCard title="System Executions" value={String(visibleDishes.length)} tone="neutral" />
      </div>

      <div className="content-grid two-column">
        <Panel title="Scheduled Tasks" subtitle={`${visibleTasks.length} task definitions in view.`}>
          <div className="list-stack">
            {visibleTasks.length ? (
              visibleTasks.map((task) => (
                <div className="feed-row" key={task.id}>
                  <div>
                    <strong>{task.task_key}</strong>
                    <p>
                      {titleize(task.task_type)} • {task.service_type || "core"} • every{" "}
                      {task.run_interval_seconds}s
                    </p>
                  </div>
                  <div className="feed-meta">
                    <StatusBadge status={task.status}>{task.status}</StatusBadge>
                    <span>{task.next_run_at ? formatDate(task.next_run_at) : "Not scheduled"}</span>
                  </div>
                </div>
              ))
            ) : (
              <EmptyState message="No scheduled tasks match the current search." />
            )}
          </div>
        </Panel>

        <Panel title="System Orders" subtitle={`${visibleOrders.length} internal orders in view.`}>
          <div className="list-stack">
            {visibleOrders.length ? (
              visibleOrders.map((order) => (
                <div className="feed-row" key={order.id}>
                  <div>
                    <strong>{order.alert_group_name}</strong>
                    <p>
                      {titleize(order.order_type)} • request {order.req_id}
                    </p>
                  </div>
                  <div className="feed-meta">
                    <StatusBadge status={order.processing_status}>{order.processing_status}</StatusBadge>
                    <Link className="ghost-button" to={`/orders/${order.id}`}>
                      Open order
                    </Link>
                  </div>
                </div>
              ))
            ) : (
              <EmptyState message="No system orders match the current search." />
            )}
          </div>
        </Panel>
      </div>

      <Panel title="System Work Executions" subtitle={`${visibleDishes.length} execution records in view.`}>
        <div className="list-stack">
          {visibleDishes.length ? (
            visibleDishes.map((dish) => (
              <div className="feed-row" key={dish.id}>
                <div>
                  <strong>{dish.recipe_name || `Recipe #${dish.recipe_id}`}</strong>
                  <p>
                    {titleize(dish.run_phase)} • {dish.order_id ? `Order #${dish.order_id}` : "No order linked"} •{" "}
                    {titleize(dish.order_type)}
                  </p>
                </div>
                <div className="feed-meta">
                  <StatusBadge status={displayDishStatus(dish)}>
                    {displayDishStatus(dish)}
                  </StatusBadge>
                  <Link
                    className="ghost-button"
                    to={dish.order_id ? `/orders/${dish.order_id}?dish=${dish.id}` : `/execution-activity?dish=${dish.id}`}
                  >
                    Open execution
                  </Link>
                </div>
              </div>
            ))
          ) : (
            <EmptyState message="No system executions match the current search." />
          )}
        </div>
      </Panel>
    </div>
  );
}

function AlertRulesPage() {
  const settings = useSettings();
  const servicePlugins = useServicePlugins();
  const k8sPlugin = servicePlugins.find((plugin) => plugin.service_type === "k8s");
  const [namespace, setNamespace] = useState(settings.prometheus_crd_namespace || "monitoring");
  const [search, setSearch] = useState("");
  const [selectedName, setSelectedName] = useState("");

  const query = useQuery({
    queryKey: ["prometheus-rules", namespace],
    queryFn: () =>
      apiGet(
        `/api/v1/plugins/k8s/prometheus-rules?namespace=${encodeURIComponent(namespace)}`,
        prometheusRuleListResponseSchema,
      ),
    enabled: Boolean(k8sPlugin && namespace.trim()),
  });

  if (!k8sPlugin) {
    return <PageError message="The Kubernetes service plugin is not registered." />;
  }

  const response = query.data;
  const rows = (response?.items || []).filter((item) => {
    const haystack = [
      item.name,
      item.namespace,
      ...item.groups.map((group) => group.name),
      ...item.groups.flatMap((group) => group.alert_names),
      ...item.groups.flatMap((group) => group.recording_names),
    ].join(" ").toLowerCase();
    return haystack.includes(search.trim().toLowerCase());
  });
  const selected = rows.find((item) => item.name === selectedName) || rows[0] || response?.items[0];

  return (
    <div className="page-stack">
      <PageHeader
        title="Alerts"
        description="Inspect PrometheusRule CRDs through the registered Kubernetes service plugin."
      />

      <div className="toolbar">
        <label>
          Namespace
          <input value={namespace} onChange={(event) => setNamespace(event.target.value)} />
        </label>
        <label className="toolbar-search">
          Search
          <input
            value={search}
            onChange={(event) => setSearch(event.target.value)}
            placeholder="Rule, group, alert, record"
          />
        </label>
        <button type="button" onClick={() => query.refetch()} disabled={query.isFetching}>
          {query.isFetching ? "Refreshing..." : "Refresh"}
        </button>
      </div>

      {query.isError ? <PageError message={getErrorMessage(query.error)} /> : null}

      <div className="metric-grid">
        <MetricCard title="Resources" value={String(response?.resource_count || 0)} tone={k8sPlugin.health_status}>
          PrometheusRule CRDs
        </MetricCard>
        <MetricCard title="Groups" value={String(response?.group_count || 0)} tone="healthy">
          Rule groups
        </MetricCard>
        <MetricCard title="Alerts" value={String(response?.alert_count || 0)} tone="healthy">
          Alerting rules
        </MetricCard>
        <MetricCard title="Records" value={String(response?.recording_count || 0)} tone="unknown">
          Recording rules
        </MetricCard>
      </div>

      <div className="master-detail">
        <Panel
          title="PrometheusRule resources"
          subtitle={response ? `${rows.length} of ${response.resource_count} resource(s) in ${response.namespace}.` : "Loading resources."}
        >
          {query.isLoading ? (
            <PageLoading message="Loading PrometheusRule resources." />
          ) : rows.length ? (
            <div className="list-stack incident-list">
              {rows.map((item) => (
                <button
                  className={`incident-row ${selected?.name === item.name ? "active" : ""}`}
                  key={`${item.namespace}/${item.name}`}
                  type="button"
                  onClick={() => setSelectedName(item.name)}
                >
                  <div>
                    <strong>{item.name}</strong>
                    <p>
                      {item.namespace} • {item.group_count} group(s) • {item.rule_count} rule(s)
                    </p>
                  </div>
                  <div className="feed-meta">
                    <StatusBadge status={item.alert_count ? "healthy" : "unknown"}>{item.alert_count} alerts</StatusBadge>
                    <span>{item.recording_count} records</span>
                  </div>
                </button>
              ))}
            </div>
          ) : (
            <EmptyState message="No PrometheusRule resources match the current namespace and search." />
          )}
        </Panel>

        <Panel title="Resource detail" subtitle="Groups, rule names, labels, and raw CRD payload.">
          {selected ? <PrometheusRuleDetail item={selected} /> : <EmptyState message="Select a PrometheusRule resource." />}
        </Panel>
      </div>
    </div>
  );
}

function PrometheusRuleDetail({ item }: { item: PrometheusRuleResourceRecord }) {
  return (
    <div className="detail-stack">
      <section className="detail-hero">
        <div>
          <div className="eyebrow">{item.namespace}</div>
          <h3>{item.name}</h3>
          <p>
            {item.group_count} group(s) • {item.rule_count} rule(s)
          </p>
        </div>
        <div className="hero-strip">
          <MetricPill label="Alerts" value={String(item.alert_count)} />
          <MetricPill label="Records" value={String(item.recording_count)} />
          <MetricPill label="Labels" value={String(Object.keys(item.labels || {}).length)} />
        </div>
      </section>

      <section className="detail-section">
        <h4>Rule groups</h4>
        <div className="list-stack">
          {item.groups.length ? (
            item.groups.map((group) => (
              <div className="preview-card" key={group.name}>
                <div className="feed-row">
                  <div>
                    <strong>{group.name}</strong>
                    <p>
                      {group.rule_count} rule(s) • {group.alert_count} alert(s) • {group.recording_count} record(s)
                    </p>
                  </div>
                </div>
                <div className="chip-list">
                  {[...group.alert_names, ...group.recording_names].slice(0, 24).map((name) => (
                    <span className="mini-chip" key={`${group.name}-${name}`}>{name}</span>
                  ))}
                </div>
              </div>
            ))
          ) : (
            <EmptyState message="This resource does not contain rule groups." />
          )}
        </div>
      </section>

      <section className="detail-section">
        <h4>Labels</h4>
        <pre className="json-block">{compactJson(item.labels)}</pre>
      </section>

      <section className="detail-section">
        <h4>Raw CRD</h4>
        <pre className="json-block">{compactJson(item.raw)}</pre>
      </section>
    </div>
  );
}

function CommunicationPolicyPage() {
  const notify = useToast();
  const principal = usePrincipal();
  const queryClient = useQueryClient();
  const canEdit = canManageGlobalCommunications(principal);

  const policyQuery = useQuery({
    queryKey: ["communications-policy"],
    queryFn: () => apiGet("/api/v1/communications/policy", communicationPolicyRecordSchema),
  });

  const form = useForm<z.infer<typeof communicationsPolicySchema>>({
    resolver: zodResolver(communicationsPolicySchema),
    defaultValues: {
      routes: [],
    },
  });

  const routes = useFieldArray({
    control: form.control,
    name: "routes",
  });

  useEffect(() => {
    if (!policyQuery.data) {
      return;
    }
    form.reset({
      routes: policyQuery.data.routes.map((route) => ({
        id: route.id,
        label: route.label,
        execution_target: route.execution_target,
        destination_target: route.destination_target || "",
        provider_config: normalizeProviderConfigForForm(route.execution_target, route.provider_config),
        enabled: route.enabled,
        position: route.position,
      })),
    });
  }, [form, policyQuery.data]);

  const saveMutation = useMutation({
    mutationFn: async (values: z.infer<typeof communicationsPolicySchema>) => {
      const actionDetails = {
        route_count: values.routes.length,
        enabled_route_count: values.routes.filter((route) => route.enabled).length,
        service_types: Array.from(new Set(values.routes.map((route) => route.execution_target))).sort(),
      };
      logOperatorAction({
        surface: "configuration.global_communications",
        action: "save_global_policy",
        status: "attempt",
        details: actionDetails,
      });
      const request = communicationPolicyUpdateRequestSchema.parse({
        routes: values.routes.map((route, index) => ({
          id: route.id || undefined,
          label: route.label,
          execution_target: route.execution_target,
          destination_target: route.destination_target || "",
          provider_config: route.provider_config || {},
          enabled: route.enabled,
          position: index + 1,
        })),
      });
      return apiPut("/api/v1/communications/policy", communicationPolicyRecordSchema, request);
    },
    onSuccess: async (policy, values) => {
      logOperatorAction({
        surface: "configuration.global_communications",
        action: "save_global_policy",
        status: "success",
        details: {
          route_count: policy.routes.length,
          enabled_route_count: policy.routes.filter((route) => route.enabled).length,
          requested_route_count: values.routes.length,
        },
      });
      notify("success", "Communication policy updated.");
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: ["communications-policy"] }),
        queryClient.invalidateQueries({ queryKey: ["settings"] }),
        queryClient.invalidateQueries({ queryKey: ["workflows"] }),
      ]);
    },
    onError: (error, values) => {
      logOperatorAction({
        surface: "configuration.global_communications",
        action: "save_global_policy",
        status: "failure",
        details: {
          route_count: values.routes.length,
          enabled_route_count: values.routes.filter((route) => route.enabled).length,
          error: getErrorMessage(error),
        },
      });
      notify("error", getErrorMessage(error));
    },
  });

  if (policyQuery.isLoading) {
    return <PageLoading message="Loading communication policy." />;
  }

  if (policyQuery.isError || !policyQuery.data) {
    return <PageError message={getErrorMessage(policyQuery.error)} />;
  }

  const watchedRoutes = form.watch("routes");
  const enabledCount = watchedRoutes.filter((route) => route.enabled).length;
  const communicationTargetOptions = communicationTargetsFromPolicy(
    policyQuery.data,
    watchedRoutes,
  );
  const defaultCommunicationTarget = communicationTargetOptions[0] || "";

  return (
    <div className="page-stack">
      <PageHeader
        title="Communication Policy"
        description="Define the communication routes inherited by recipes that do not supply a recipe-specific override."
      />
      <div className="editor-grid">
        <Panel
          title="Default route set"
          subtitle="This policy is optional. If it is empty, enabled recipes must define recipe-specific communication routes."
        >
          {!canEdit ? (
            <div className="helper-card">
              <strong>Read-only access</strong>
              <p>Your role can review the communication policy but only admins can change it.</p>
            </div>
          ) : null}
          <form className="form-stack" onSubmit={form.handleSubmit((values) => saveMutation.mutate(values))}>
            <fieldset disabled={!canEdit}>
            <div className="builder-header">
              <div>
                <h4>Communication routes</h4>
                <p>Any enabled route counts. Core, Teams, Discord, and mixed route sets are all valid.</p>
              </div>
              <button
                className="ghost-button"
                disabled={!canEdit || !defaultCommunicationTarget}
                type="button"
                onClick={() => routes.append({ ...emptyCommunicationRoute(defaultCommunicationTarget), position: routes.fields.length + 1 })}
              >
                Add route
              </button>
            </div>

            <div className="builder-stack">
              {routes.fields.length ? (
                routes.fields.map((field, index) => (
                  <div className="builder-card" key={field.id}>
                    <div className="builder-card-head">
                      <strong>Route {index + 1}</strong>
                      <div className="inline-actions">
                        <button className="ghost-button" type="button" onClick={() => moveCommunicationRoute(routes, index, -1)}>
                          Up
                        </button>
                        <button className="ghost-button" type="button" onClick={() => moveCommunicationRoute(routes, index, 1)}>
                          Down
                        </button>
                        <button className="danger-button" type="button" onClick={() => routes.remove(index)}>
                          Remove
                        </button>
                      </div>
                    </div>
                    <div className="grid-two">
                      <FormField label="Route label" help="Give the route a plain-language name that operators will recognize in recipe and communication views.">
                        <input {...form.register(`routes.${index}.label` as const)} placeholder="Primary on-call route" />
                        <FieldError message={form.formState.errors.routes?.[index]?.label?.message} />
                      </FormField>
                      <FormField label="Provider" help="Choose the communication provider or destination type such as rackspace_core, teams, or discord.">
                        <select
                          {...form.register(`routes.${index}.execution_target` as const, {
                            onChange: () => form.setValue(`routes.${index}.provider_config` as any, {}),
                          })}
                        >
                          {!communicationTargetOptions.length ? (
                            <option value="">No communication providers advertised</option>
                          ) : null}
                          {communicationTargetOptions.map((target) => (
                            <option key={target} value={target}>
                              {titleize(target)}
                            </option>
                          ))}
                        </select>
                      </FormField>
                    </div>
                    <div className="grid-two">
                      <FormField label="Destination" help="Optional route target such as a queue, channel, room, or project. Leave blank when the provider uses its own default destination.">
                        <input {...form.register(`routes.${index}.destination_target` as const)} placeholder="ops-alerts" />
                      </FormField>
                      <FormField label="Enabled" help="Disabled routes are kept in the policy but are ignored by runtime dispatch.">
                        <label className="toggle-row">
                          <input type="checkbox" {...form.register(`routes.${index}.enabled` as const)} />
                          <span>Route is enabled</span>
                        </label>
                      </FormField>
                    </div>
                    <CommunicationRouteProviderConfigFields
                      form={form as ReturnType<typeof useForm<any>>}
                      basePath={`routes.${index}`}
                      executionTarget={watchedRoutes[index]?.execution_target || field.execution_target}
                    />
                  </div>
                ))
              ) : (
                <EmptyState message="No communication routes are configured. Enabled recipes will need recipe-specific communication routes." />
              )}
            </div>

            <div className="preview-card">
              <div className="eyebrow">Policy preview</div>
              <p>
                {enabledCount
                  ? `${enabledCount} enabled route(s) will open on escalation, open then close after successful auto-remediation clears, and post clear notifications after escalation clears.`
                  : "This communication policy is empty. Enabled recipes must define recipe-specific communication routes to be valid."}
              </p>
            </div>

            <div className="form-actions">
              <button className="primary-button" disabled={saveMutation.isPending} type="submit">
                {saveMutation.isPending ? "Saving..." : "Save communication policy"}
              </button>
            </div>
            </fieldset>
          </form>
        </Panel>

        <HelpRail
          title="Communication policy help"
          items={[
            {
              label: "Policy-provided providers",
              description: "Provider choices come from enabled service plugin communication routes advertised through the communication policy endpoint.",
            },
            {
              label: "Fixed lifecycle",
              description: "Inherited and recipe-specific policies share the same runtime lifecycle: open on escalation, open plus close after successful resolve, and notify only after escalation clears.",
            },
            {
              label: "Any route type",
              description: "Ticket-backed and chat-only destinations are both valid. PoundCake tracks ticket numbers when available and generic provider references otherwise.",
            },
          ]}
        />
      </div>

      <Panel title="Lifecycle summary" subtitle="What PoundCake does with the effective communication policy at runtime.">
        <div className="kv-grid">
          {Object.entries(policyQuery.data.lifecycle_summary).map(([key, value]) => (
            <KeyValue key={key} label={titleize(key.replace(/_/g, " "))} value={value} />
          ))}
        </div>
      </Panel>
    </div>
  );
}

function RecipesPage() {
  const notify = useToast();
  const principal = usePrincipal();
  const settings = useSettings();
  const queryClient = useQueryClient();
  const [editorOpen, setEditorOpen] = useState(false);
  const [editingWorkflow, setEditingWorkflow] = useState<RecipeRecord | null>(null);
  const [mode, setMode] = useState<"simple" | "advanced">("simple");
  const canEdit = canManageWorkflows(principal);
  const canClear = canManageRepoSyncClear(principal);

  const recipesQuery = useQuery({
    queryKey: ["workflows"],
    queryFn: () => apiGet("/api/v1/recipes/?limit=200", recipeRecordArraySchema),
  });
  const actionsQuery = useQuery({
    queryKey: ["actions"],
    queryFn: () => apiGet("/api/v1/service-registry/ingredients?limit=500", ingredientRecordArraySchema),
  });
  const policyQuery = useQuery({
    queryKey: ["communications-policy"],
    queryFn: () => apiGet("/api/v1/communications/policy", communicationPolicyRecordSchema),
  });

  const form = useForm<z.infer<typeof workflowSchema>>({
    resolver: zodResolver(workflowSchema),
    defaultValues: {
      name: "",
      description: "",
      enabled: true,
      clear_timeout_sec: "",
      communications_mode: settings.global_communications_configured ? "inherit" : "local",
      communications_routes: [],
      recipe_ingredients: [
        {
          ingredient_id: 0,
          step_order: 1,
          on_success: "continue",
          run_phase: "both",
          run_condition: "always",
          parallel_group: 0,
          depth: 0,
          operation: "",
          service_payload_values: {},
          execution_parameters_override_text: "",
        },
      ],
    },
  });

  const steps = useFieldArray({
    control: form.control,
    name: "recipe_ingredients",
  });
  const communicationRoutes = useFieldArray({
    control: form.control,
    name: "communications_routes",
  });

  const refreshWorkflows = async () => {
    await queryClient.invalidateQueries({ queryKey: ["workflows"] });
    await queryClient.refetchQueries({
      queryKey: ["workflows"],
      exact: true,
      type: "active",
    });
  };

  const refreshActions = async () => {
    await queryClient.invalidateQueries({ queryKey: ["actions"] });
    await queryClient.refetchQueries({
      queryKey: ["actions"],
      exact: true,
      type: "active",
    });
  };

  const refreshWorkflowAndActionInventories = async () => {
    await Promise.all([refreshWorkflows(), refreshActions()]);
  };

  const openCreateWorkflowDialog = () => {
    setEditingWorkflow(null);
    resetWorkflowForm(form, steps, communicationRoutes, settings.global_communications_configured);
    setMode("simple");
    setEditorOpen(true);
  };

  const openEditWorkflowDialog = (workflow: RecipeRecord) => {
    setEditingWorkflow(workflow);
    setEditorOpen(true);
  };

  const closeWorkflowDialog = () => {
    setEditorOpen(false);
    setEditingWorkflow(null);
    resetWorkflowForm(form, steps, communicationRoutes, settings.global_communications_configured);
    setMode("simple");
  };

  useEffect(() => {
    if (!editingWorkflow) {
      return;
    }
    form.reset({
      name: editingWorkflow.name,
      description: editingWorkflow.description || "",
      enabled: editingWorkflow.enabled,
      clear_timeout_sec: editingWorkflow.clear_timeout_sec ? String(editingWorkflow.clear_timeout_sec) : "",
      communications_mode: editingWorkflow.communications.mode,
      communications_routes:
        editingWorkflow.communications.mode === "local"
          ? editingWorkflow.communications.routes.map((route) => ({
              id: route.id,
              label: route.label,
              execution_target: route.execution_target,
              destination_target: route.destination_target || "",
              provider_config: normalizeProviderConfigForForm(route.execution_target, route.provider_config),
              enabled: route.enabled,
              position: route.position,
            }))
          : [],
      recipe_ingredients: editingWorkflow.recipe_ingredients.map((step) => ({
        ...workflowStepFormDefaults(
          actionsQuery.data?.find((action) => action.id === step.ingredient_id),
          step.service_exec_parameters_override || step.execution_parameters_override || null,
          step.service_payload || null,
        ),
        ingredient_id: step.ingredient_id,
        step_order: step.step_order,
        on_success: step.on_success,
        run_phase: step.run_phase,
        run_condition: step.run_condition,
        parallel_group: step.parallel_group,
        depth: step.depth,
        execution_parameters_override_text: step.execution_parameters_override
          ? compactJson(step.execution_parameters_override)
          : "",
      })),
    });
  }, [actionsQuery.data, editingWorkflow, form]);

  useEffect(() => {
    if (editingWorkflow || settings.global_communications_configured) {
      return;
    }
    if (form.getValues("communications_mode") === "inherit") {
      form.setValue("communications_mode", "local");
    }
  }, [editingWorkflow, form, settings.global_communications_configured]);

  const saveMutation = useMutation({
    mutationFn: async (values: z.infer<typeof workflowSchema>) => {
      if (values.enabled && values.communications_mode === "inherit" && !settings.global_communications_configured) {
        throw new Error("Configure a communication policy or switch this recipe to recipe-specific communication routes.");
      }
      if (values.enabled && values.communications_mode === "local" && !values.communications_routes.some((route) => route.enabled)) {
        throw new Error("Enabled recipes need at least one enabled recipe-specific communication route.");
      }
      const payload = {
        name: values.name,
        description: values.description || null,
        enabled: values.enabled,
        clear_timeout_sec: values.clear_timeout_sec ? Number(values.clear_timeout_sec) : null,
        communications: {
          mode: values.communications_mode,
          routes:
            values.communications_mode === "local"
              ? values.communications_routes.map((route, index) => ({
                  id: route.id || undefined,
                  label: route.label,
                  execution_target: route.execution_target,
                  destination_target: route.destination_target || "",
                  provider_config: route.provider_config || {},
                  enabled: route.enabled,
                  position: index + 1,
                }))
              : [],
        },
        recipe_ingredients: values.recipe_ingredients.map((step, index) => ({
          ingredient_id: Number(step.ingredient_id),
          step_order: index + 1,
          on_success: step.on_success,
          parallel_group: step.parallel_group,
          depth: step.depth,
          service_payload: buildServicePayloadForStep(
            step,
            actionsQuery.data?.find((action) => action.id === Number(step.ingredient_id)),
          ),
          service_exec_parameters_override: buildExecutionParametersForStep(
            step,
            actionsQuery.data?.find((action) => action.id === Number(step.ingredient_id)),
            parseOptionalJson(step.execution_parameters_override_text, "Step override JSON") || null,
          ),
          run_phase: step.run_phase,
          run_condition: step.run_condition,
        })),
      };
      if (editingWorkflow) {
        return apiPut(
          `/api/v1/recipes/${editingWorkflow.id}`,
          recipeRecordSchema,
          recipeUpdateRequestSchema.parse(payload),
        );
      }
      return apiPost("/api/v1/recipes/", recipeRecordSchema, recipeCreateRequestSchema.parse(payload));
    },
    onSuccess: async () => {
      notify("success", editingWorkflow ? "Recipe updated." : "Recipe created.");
      closeWorkflowDialog();
      await Promise.all([
        refreshWorkflows(),
        queryClient.invalidateQueries({ queryKey: ["communications-policy"] }),
      ]);
    },
    onError: (error) => notify("error", getErrorMessage(error)),
  });

  const deleteMutation = useMutation({
    mutationFn: (workflowId: number) => apiDelete(`/api/v1/recipes/${workflowId}`, deleteResponseSchema),
    onSuccess: async () => {
      notify("success", "Recipe deleted.");
      closeWorkflowDialog();
      await refreshWorkflows();
    },
    onError: (error) => notify("error", getErrorMessage(error)),
  });

  const exportMutation = useMutation({
    mutationFn: () => apiPost("/api/v1/repo-sync/workflow-actions/export", repoSyncResponseSchema),
    onSuccess: async (result) => {
      notify("success", formatRepoSyncMessage(result));
      await refreshWorkflowAndActionInventories();
    },
    onError: (error) => notify("error", getErrorMessage(error)),
  });

  const importMutation = useMutation({
    mutationFn: () => apiPost("/api/v1/repo-sync/workflow-actions/import", repoSyncResponseSchema),
    onSuccess: async (result) => {
      notify("success", formatRepoSyncMessage(result));
      closeWorkflowDialog();
      await refreshWorkflowAndActionInventories();
    },
    onError: (error) => notify("error", getErrorMessage(error)),
  });

  const clearMutation = useMutation({
    mutationFn: () => apiDelete("/api/v1/repo-sync/workflow-actions", repoSyncResponseSchema),
    onSuccess: async (result) => {
      notify("success", formatRepoSyncMessage(result));
      closeWorkflowDialog();
      await refreshWorkflowAndActionInventories();
    },
    onError: (error) => notify("error", getErrorMessage(error)),
  });

  if (recipesQuery.isLoading || actionsQuery.isLoading || policyQuery.isLoading) {
    return <PageLoading message="Loading recipes, ingredient templates, and builder controls." />;
  }

  if (recipesQuery.isError || actionsQuery.isError || policyQuery.isError || !recipesQuery.data || !actionsQuery.data || !policyQuery.data) {
    return <PageError message={getErrorMessage(recipesQuery.error || actionsQuery.error || policyQuery.error)} />;
  }

  const availableActions = actionsQuery.data.filter((action) => !isCommunicationAction(action));
  const watchedSteps = form.watch("recipe_ingredients");
  const watchedCommunicationMode = form.watch("communications_mode");
  const watchedCommunicationRoutes = form.watch("communications_routes");
  const communicationTargetOptions = communicationTargetsFromPolicy(
    policyQuery.data,
    watchedCommunicationRoutes,
  );
  const defaultCommunicationTarget = communicationTargetOptions[0] || "";
  const workflowPreview = buildWorkflowPreview(
    watchedSteps,
    availableActions,
    form.watch("name"),
    watchedCommunicationMode,
    watchedCommunicationMode === "local" ? watchedCommunicationRoutes : policyQuery.data.routes,
  );

  return (
    <div className="page-stack">
      <PageHeader
        title="Recipes"
        description="Build reusable remediation and utility recipes, then choose whether they inherit the communication policy or define recipe-specific routes."
      />
      <WorkflowRepoSyncPanel
        canClear={canClear}
        canEdit={canEdit}
        isPending={exportMutation.isPending || importMutation.isPending || clearMutation.isPending}
        onClear={() => clearMutation.mutate()}
        onExport={() => exportMutation.mutate()}
        onImport={() => importMutation.mutate()}
        settings={settings}
      />
      <Panel
        title="Recipe Inventory"
        subtitle={`${recipesQuery.data.length} recipes loaded. Select a recipe to edit it or remove it when it is no longer used.`}
        actions={
          <button
            className="primary-button"
            disabled={!canEdit}
            type="button"
            onClick={openCreateWorkflowDialog}
          >
            Create recipe
          </button>
        }
      >
        {!canEdit ? (
          <div className="helper-card">
            <strong>Read-only access</strong>
            <p>Your role can inspect recipes, but only operators can change them.</p>
          </div>
        ) : null}
        <div className="table-wrap">
          <table>
            <thead>
              <tr>
                <th>Name</th>
                <th>Description</th>
                <th>Enabled</th>
                <th>Communication Routes</th>
                <th>Steps</th>
                <th>Updated</th>
                <th />
              </tr>
            </thead>
            <tbody>
              {recipesQuery.data.map((workflow) => (
                <tr className={workflow.enabled ? undefined : "workflow-row-disabled"} key={workflow.id}>
                  <td className="text-cell strong-cell">{workflow.name}</td>
                  <td className="text-cell muted-cell">{workflow.description || "-"}</td>
                  <td>
                    <StatusBadge status={workflow.enabled ? "active" : "canceled"}>
                      {workflow.enabled ? "enabled" : "disabled"}
                    </StatusBadge>
                  </td>
                  <td>
                    {workflow.communications.mode === "local"
                      ? `${workflow.communications.routes.filter((route) => route.enabled).length} recipe route(s)`
                      : workflow.communications.routes.filter((route) => route.enabled).length
                        ? `${workflow.communications.routes.filter((route) => route.enabled).length} policy route(s)`
                        : "none"}
                  </td>
                  <td>{workflow.recipe_ingredients.length}</td>
                  <td>{formatDate(workflow.updated_at)}</td>
                  <td className="action-cell">
                    <button
                      className="ghost-button"
                      disabled={!canEdit}
                      type="button"
                      onClick={() => openEditWorkflowDialog(workflow)}
                    >
                      Edit
                    </button>
                    <button
                      className="danger-button"
                      disabled={!canEdit}
                      type="button"
                      onClick={() => {
                        if (window.confirm(`Delete recipe "${workflow.name}"?`)) {
                          deleteMutation.mutate(workflow.id);
                        }
                      }}
                    >
                      Delete
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </Panel>
      {editorOpen ? (
        <div aria-modal="true" className="dialog-backdrop" role="dialog">
          <div className="dialog-card dialog-card-wide">
            <div className="panel-head">
              <div>
                <h4>{editingWorkflow ? `Edit ${editingWorkflow.name}` : "Create recipe"}</h4>
                <p>Simple mode keeps the common path short. Advanced mode exposes execution plumbing when you need it.</p>
              </div>
            </div>
            {!canEdit ? (
              <div className="helper-card">
                <strong>Read-only access</strong>
                <p>Your role can inspect recipes, but only operators can change them.</p>
              </div>
            ) : null}
            <div className="mode-toggle">
              <button
                className={mode === "simple" ? "primary-button" : "ghost-button"}
                disabled={!canEdit}
                onClick={() => setMode("simple")}
                type="button"
              >
                Simple
              </button>
              <button
                className={mode === "advanced" ? "primary-button" : "ghost-button"}
                disabled={!canEdit}
                onClick={() => setMode("advanced")}
                type="button"
              >
                Advanced
              </button>
            </div>

            <form className="form-stack" onSubmit={form.handleSubmit((values) => saveMutation.mutate(values))}>
              <fieldset disabled={!canEdit}>
                <div className="grid-two">
                  <FormField label="Recipe name" help="Use the alert or handling pattern name operators will recognize.">
                    <input {...form.register("name")} placeholder="Node filesystem response" />
                    <FieldError message={form.formState.errors.name?.message} />
                  </FormField>
                  <FormField label="Resolve wait (sec)" help="How long PoundCake should wait before resolve-side handling applies.">
                    <input {...form.register("clear_timeout_sec")} placeholder="300" />
                  </FormField>
                </div>
                <FormField label="Description" help="Explain the intent so responders can understand why this recipe exists.">
                  <textarea {...form.register("description")} rows={3} />
                </FormField>
                <FormField label="Enabled" help="Disable a recipe without deleting it when you want to keep its history and structure.">
                  <label className="toggle-row">
                    <input type="checkbox" {...form.register("enabled")} />
                    <span>Recipe is enabled</span>
                  </label>
                </FormField>

                <div className="builder-header">
                  <div>
                    <h4>Communication Routes</h4>
                    <p>Communication routes are policy-driven. Use the inherited policy or replace it with recipe-specific routes.</p>
                  </div>
                </div>

                <div className="builder-stack">
                  <div className="builder-card">
                    <div className="grid-two">
                      <FormField label="Communications source" help="Use the inherited route set when possible, or replace it entirely for this recipe with recipe-specific communication routes.">
                        <select {...form.register("communications_mode")}>
                          <option value="inherit">Use communication policy</option>
                          <option value="local">Use recipe-specific routes</option>
                        </select>
                      </FormField>
                      <div className="helper-card">
                        <strong>Current effective policy</strong>
                        <p>
                          {watchedCommunicationMode === "inherit"
                            ? policyQuery.data.configured
                              ? `${policyQuery.data.routes.filter((route) => route.enabled).length} inherited route(s) are available to this recipe.`
                              : "No communication routes are configured yet. Switch this recipe to recipe-specific routes before enabling it."
                            : `${watchedCommunicationRoutes.filter((route) => route.enabled).length} recipe-specific route(s) are currently enabled.`}
                        </p>
                      </div>
                    </div>

                    {watchedCommunicationMode === "inherit" ? (
                      policyQuery.data.routes.length ? (
                        <div className="route-grid">
                          {policyQuery.data.routes.map((route) => (
                            <div className="route-card" key={route.id}>
                              <div className="route-card-head">
                                <strong>{route.label}</strong>
                                <StatusBadge status={route.enabled ? "active" : "canceled"}>
                                  {route.enabled ? "enabled" : "disabled"}
                                </StatusBadge>
                              </div>
                              <KeyValue label="Provider" value={route.execution_target} />
                              <KeyValue label="Destination" value={route.destination_target || "-"} />
                              <DetailList compact>
                                <DetailRow label="Route config" value={providerConfigSummary(route.execution_target, route.provider_config)} />
                              </DetailList>
                            </div>
                          ))}
                        </div>
                      ) : (
                        <EmptyState message="No communication routes are configured. Use recipe-specific routes or configure the communication policy first." />
                      )
                    ) : (
                      <>
                        <div className="builder-header">
                          <div>
                            <h4>Recipe-specific routes</h4>
                            <p>These routes replace the inherited policy entirely for this recipe.</p>
                          </div>
                          <button
                            className="ghost-button"
                            disabled={!defaultCommunicationTarget}
                            type="button"
                            onClick={() =>
                              communicationRoutes.append({
                                ...emptyCommunicationRoute(defaultCommunicationTarget),
                                position: communicationRoutes.fields.length + 1,
                              })
                            }
                          >
                            Add route
                          </button>
                        </div>

                        <div className="builder-stack">
                          {communicationRoutes.fields.length ? (
                            communicationRoutes.fields.map((field, index) => (
                              <div className="builder-card" key={field.id}>
                                <div className="builder-card-head">
                                  <strong>Route {index + 1}</strong>
                                  <div className="inline-actions">
                                    <button className="ghost-button" type="button" onClick={() => moveCommunicationRoute(communicationRoutes, index, -1)}>
                                      Up
                                    </button>
                                    <button className="ghost-button" type="button" onClick={() => moveCommunicationRoute(communicationRoutes, index, 1)}>
                                      Down
                                    </button>
                                    <button className="danger-button" type="button" onClick={() => communicationRoutes.remove(index)}>
                                      Remove
                                    </button>
                                  </div>
                                </div>
                                <div className="grid-two">
                                  <FormField label="Route label" help="Name this route the way operators will recognize it later in order and communication history.">
                                    <input {...form.register(`communications_routes.${index}.label` as const)} placeholder="Primary escalation route" />
                                    <FieldError message={form.formState.errors.communications_routes?.[index]?.label?.message} />
                                  </FormField>
                                  <FormField label="Provider" help="Provider or destination type such as rackspace_core, teams, or discord.">
                                    <select
                                      {...form.register(`communications_routes.${index}.execution_target` as const, {
                                        onChange: () =>
                                          form.setValue(`communications_routes.${index}.provider_config` as any, {}),
                                      })}
                                    >
                                      {!communicationTargetOptions.length ? (
                                        <option value="">No communication providers advertised</option>
                                      ) : null}
                                      {communicationTargetOptions.map((target) => (
                                        <option key={target} value={target}>
                                          {titleize(target)}
                                        </option>
                                      ))}
                                    </select>
                                  </FormField>
                                </div>
                                <div className="grid-two">
                                  <FormField label="Destination" help="Optional queue, channel, project, or room for this route.">
                                    <input {...form.register(`communications_routes.${index}.destination_target` as const)} placeholder="ops-alerts" />
                                  </FormField>
                                  <FormField label="Enabled" help="Disabled routes stay in the recipe definition but do not run.">
                                    <label className="toggle-row">
                                      <input type="checkbox" {...form.register(`communications_routes.${index}.enabled` as const)} />
                                      <span>Route is enabled</span>
                                    </label>
                                  </FormField>
                                </div>
                                <CommunicationRouteProviderConfigFields
                                  form={form as ReturnType<typeof useForm<any>>}
                                  basePath={`communications_routes.${index}`}
                                  executionTarget={watchedCommunicationRoutes[index]?.execution_target || field.execution_target}
                                />
                              </div>
                            ))
                          ) : (
                            <EmptyState message="Add at least one route if this recipe should override the communication policy." />
                          )}
                        </div>
                      </>
                    )}
                  </div>
                </div>

                <div className="builder-header">
                  <div>
                    <h4>Recipe Steps</h4>
                    <p>Recipe steps should focus on remediation and utility logic. Communication routes are managed separately above.</p>
                  </div>
                  <button
                    className="ghost-button"
                    type="button"
                    onClick={() =>
                      steps.append({
                        ingredient_id: 0,
                        step_order: steps.fields.length + 1,
                        on_success: "continue",
                        run_phase: "both",
                        run_condition: "always",
                        parallel_group: 0,
                        depth: 0,
                        operation: "",
                        service_payload_values: {},
                        execution_parameters_override_text: "",
                      })
                    }
                  >
                    Add step
                  </button>
                </div>

                <div className="builder-stack">
                  {steps.fields.map((field, index) => (
                    <div className="builder-card" key={field.id}>
                      <div className="builder-card-head">
                        <strong>Step {index + 1}</strong>
                        <div className="inline-actions">
                          <button className="ghost-button" type="button" onClick={() => moveField(steps, index, -1)}>
                            Up
                          </button>
                          <button className="ghost-button" type="button" onClick={() => moveField(steps, index, 1)}>
                            Down
                          </button>
                          <button className="danger-button" type="button" onClick={() => steps.remove(index)}>
                            Remove
                          </button>
                        </div>
                      </div>
                      <div className="grid-three">
                        <FormField label="Ingredient Template" help="The reusable ingredient template this recipe step will run.">
                          <select
                            {...form.register(`recipe_ingredients.${index}.ingredient_id` as const, {
                              valueAsNumber: true,
                              onChange: (event) => {
                                const action = availableActions.find((item) => item.id === Number(event.target.value));
                                const defaults = workflowStepFormDefaults(action);
                                form.setValue(`recipe_ingredients.${index}.operation` as const, defaults.operation || "");
                                form.setValue(
                                  `recipe_ingredients.${index}.service_payload_values` as const,
                                  defaults.service_payload_values || {},
                                );
                              },
                            })}
                          >
                            <option value={0}>Choose an ingredient template</option>
                            {availableActions.map((action) => (
                              <option key={action.id} value={action.id}>
                                {action.task_key_template} ({titleize(action.execution_target)})
                              </option>
                            ))}
                          </select>
                        </FormField>
                        <OperationField
                          action={availableActions.find((item) => item.id === Number(watchedSteps[index]?.ingredient_id))}
                          form={form}
                          index={index}
                        />
                        <FormField label="Run phase" help="Choose whether this runs when the alert fires, resolves, escalates, or both.">
                          <select {...form.register(`recipe_ingredients.${index}.run_phase` as const)}>
                            <option value="firing">firing</option>
                            <option value="escalation">escalation</option>
                            <option value="resolving">resolving</option>
                            <option value="both">both</option>
                          </select>
                        </FormField>
                        <FormField label="Run condition" help="Fine-grained control over whether this step runs after success, failure, timeout, or always.">
                          <select {...form.register(`recipe_ingredients.${index}.run_condition` as const)}>
                            <option value="always">always</option>
                            <option value="remediation_failed">remediation_failed</option>
                            <option value="clear_timeout_expired">clear_timeout_expired</option>
                            <option value="resolved_after_success">resolved_after_success</option>
                            <option value="resolved_after_failure">resolved_after_failure</option>
                            <option value="resolved_after_no_remediation">resolved_after_no_remediation</option>
                            <option value="resolved_after_timeout">resolved_after_timeout</option>
                          </select>
                        </FormField>
                      </div>
                      <ServicePayloadFields
                        action={availableActions.find((item) => item.id === Number(watchedSteps[index]?.ingredient_id))}
                        form={form}
                        index={index}
                      />
                      <div className="grid-two">
                        <FormField label="On success" help="Continue keeps the recipe moving; stop ends the recipe after this step succeeds.">
                          <select {...form.register(`recipe_ingredients.${index}.on_success` as const)}>
                            <option value="continue">continue</option>
                            <option value="stop">stop</option>
                          </select>
                        </FormField>
                        {mode === "advanced" ? (
                          <FormField label="Override JSON" help="Optional execution-parameter override for this specific step invocation.">
                            <textarea {...form.register(`recipe_ingredients.${index}.execution_parameters_override_text` as const)} rows={3} />
                          </FormField>
                        ) : (
                          <div className="helper-card">
                            <strong>Plain-English step</strong>
                            <p>{describeWorkflowStep(watchedSteps[index], availableActions)}</p>
                          </div>
                        )}
                      </div>
                      {mode === "advanced" ? (
                        <div className="grid-two">
                          <FormField label="Parallel group" help="Steps with the same group can be planned together. Use 0 for default sequential handling.">
                            <input
                              type="number"
                              min={0}
                              {...form.register(`recipe_ingredients.${index}.parallel_group` as const, {
                                valueAsNumber: true,
                              })}
                            />
                          </FormField>
                          <FormField label="Depth" help="Execution depth for more advanced branching patterns. Leave at 0 for standard ordered recipes.">
                            <input
                              type="number"
                              min={0}
                              {...form.register(`recipe_ingredients.${index}.depth` as const, {
                                valueAsNumber: true,
                              })}
                            />
                          </FormField>
                        </div>
                      ) : null}
                    </div>
                  ))}
                </div>

                <div className="preview-card">
                  <div className="eyebrow">Recipe preview</div>
                  <p>{workflowPreview}</p>
                </div>

                <div className="form-actions">
                  <button className="ghost-button" type="button" onClick={closeWorkflowDialog}>
                    Cancel
                  </button>
                  <button className="primary-button" disabled={saveMutation.isPending} type="submit">
                    {saveMutation.isPending ? "Saving..." : editingWorkflow ? "Save recipe" : "Create recipe"}
                  </button>
                </div>
              </fieldset>
            </form>
          </div>
        </div>
      ) : null}
    </div>
  );
}

function IngredientTemplatesPage() {
  const actionsQuery = useQuery({
    queryKey: ["actions"],
    queryFn: () => apiGet("/api/v1/service-registry/ingredients?limit=500", ingredientRecordArraySchema),
  });

  if (actionsQuery.isLoading) {
    return <PageLoading message="Loading ingredient templates." />;
  }

  if (actionsQuery.isError || !actionsQuery.data) {
    return <PageError message={getErrorMessage(actionsQuery.error)} />;
  }

  return (
    <div className="page-stack">
      <PageHeader
        title="Ingredient Templates"
        description="Plugin-provided ingredient templates for recipe steps. Manage templates through plugin manifest registration. Communication routes live in the communication policy and recipe communication sections."
      />
      <Panel
        title="Ingredient Template Inventory"
        subtitle={`${actionsQuery.data.length} ingredient templates loaded. Recipes use these as reusable step capabilities. Templates are managed through plugin manifest registration.`}
      >
        <div className="table-wrap">
          <table>
            <thead>
              <tr>
                <th>Name</th>
                <th>Target</th>
                <th>Engine</th>
                <th>Purpose</th>
                <th>Blocking</th>
                <th>Updated</th>
              </tr>
            </thead>
            <tbody>
              {actionsQuery.data.map((action) => (
                <tr key={action.id}>
                  <td>{action.task_key_template}</td>
                  <td>{action.destination_target ? `${action.execution_target}:${action.destination_target}` : action.execution_target}</td>
                  <td>{action.execution_engine}</td>
                  <td>{action.execution_purpose}</td>
                  <td>{String(action.is_blocking)}</td>
                  <td>{formatDate(action.updated_at)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </Panel>
    </div>
  );
}

function AccessPage() {
  const principal = usePrincipal();
  const settings = useSettings();
  const notify = useToast();
  const queryClient = useQueryClient();
  const [provider, setProvider] = useState<string>(
    settings.auth_providers.find((item) => item.name !== "local" && item.name !== "service")?.name || "",
  );
  const [bindingType, setBindingType] = useState<"group" | "user">("group");
  const [role, setRole] = useState<"reader" | "operator" | "admin">("reader");
  const [externalGroup, setExternalGroup] = useState("");
  const [selectedPrincipalId, setSelectedPrincipalId] = useState("");
  const [search, setSearch] = useState("");
  const deferredSearch = useDeferredValue(search);

  const providers = settings.auth_providers.filter((item) => item.name !== "service");
  const externalProviders = providers.filter((item) => item.name !== "local");

  useEffect(() => {
    if (externalProviders.length === 0) {
      if (provider) setProvider("");
      return;
    }
    if (!provider || !externalProviders.some((item) => item.name === provider)) {
      setProvider(externalProviders[0].name);
    }
  }, [externalProviders, provider]);

  const principalsQuery = useQuery({
    queryKey: ["auth-principals", provider, deferredSearch],
    queryFn: () =>
      apiGet(
        `/api/v1/auth/principals?limit=200${provider ? `&provider=${encodeURIComponent(provider)}` : ""}${deferredSearch ? `&search=${encodeURIComponent(deferredSearch)}` : ""}`,
        authPrincipalRecordArraySchema,
      ),
    placeholderData: (previousData) => previousData,
    enabled: canManageAccess(principal),
  });
  const bindingsQuery = useQuery({
    queryKey: ["auth-bindings"],
    queryFn: () => apiGet("/api/v1/auth/bindings", authRoleBindingRecordArraySchema),
    enabled: canManageAccess(principal),
  });

  const createMutation = useMutation({
    mutationFn: async () => {
      const payload =
        bindingType === "group"
          ? {
              provider,
              binding_type: "group",
              role,
              external_group: externalGroup,
            }
          : {
              provider,
              binding_type: "user",
              role,
              principal_id: Number(selectedPrincipalId),
            };
      return apiPost(
        "/api/v1/auth/bindings",
        authRoleBindingRecordSchema,
        authRoleBindingCreateRequestSchema.parse(payload),
      );
    },
    onSuccess: async () => {
      notify("success", "Role binding created.");
      setExternalGroup("");
      setSelectedPrincipalId("");
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: ["auth-bindings"] }),
        queryClient.invalidateQueries({ queryKey: ["auth-principals"] }),
      ]);
    },
    onError: (error) => notify("error", getErrorMessage(error)),
  });

  const updateMutation = useMutation({
    mutationFn: ({ id, nextRole }: { id: number; nextRole: string }) =>
      apiPatch(
        `/api/v1/auth/bindings/${id}`,
        authRoleBindingRecordSchema,
        authRoleBindingUpdateRequestSchema.parse({ role: nextRole }),
      ),
    onSuccess: async () => {
      notify("success", "Role binding updated.");
      await queryClient.invalidateQueries({ queryKey: ["auth-bindings"] });
    },
    onError: (error) => notify("error", getErrorMessage(error)),
  });

  const deleteMutation = useMutation({
    mutationFn: (id: number) => apiDelete(`/api/v1/auth/bindings/${id}`, deleteResponseSchema),
    onSuccess: async () => {
      notify("success", "Role binding deleted.");
      await queryClient.invalidateQueries({ queryKey: ["auth-bindings"] });
    },
    onError: (error) => notify("error", getErrorMessage(error)),
  });

  if (!canManageAccess(principal)) {
    return <PageError message="You do not have access to manage PoundCake RBAC policies." />;
  }

  if (bindingsQuery.isLoading || (principalsQuery.isLoading && !principalsQuery.data)) {
    return <PageLoading message="Loading provider status, observed principals, and RBAC policies." />;
  }

  if (principalsQuery.isError || bindingsQuery.isError || !principalsQuery.data || !bindingsQuery.data) {
    return <PageError message={getErrorMessage(principalsQuery.error || bindingsQuery.error)} />;
  }

  const principalFilter = search.trim().toLowerCase();
  const visiblePrincipals = (principalsQuery.data || []).filter((item) => {
    if (!principalFilter) {
      return true;
    }
    const haystack = [item.username, item.display_name, item.subject_id]
      .filter(Boolean)
      .join(" ")
      .toLowerCase();
    return haystack.includes(principalFilter);
  });
  const hasExternalProviders = externalProviders.length > 0;
  const createDisabled =
    createMutation.isPending
    || !hasExternalProviders
    || (bindingType === "group" ? !externalGroup.trim() : !selectedPrincipalId);

  return (
    <div className="page-stack">
      <PageHeader
        title="RBAC Policies"
        description="Manage provider-backed RBAC policy bindings for readers, operators, and admins. External providers are enabled at deploy time; this page manages who gets access after they appear in PoundCake."
      />

      <div className="status-grid">
        {providers.map((item) => (
          <MetricCard key={item.name} title={item.label} value={titleize(item.login_mode)} tone="active">
            {describeAuthProviderModes(item)}
          </MetricCard>
        ))}
      </div>

      <div className="editor-grid">
        <Panel title="Create RBAC policy" subtitle="Bind either an observed user or an external group to a PoundCake role.">
          <div className="form-stack">
            {!hasExternalProviders ? (
              <EmptyState message="No external auth providers are enabled yet. Add Active Directory, Auth0, or Azure AD in Helm auth values, redeploy PoundCake, then create bindings here." />
            ) : null}
            <div className="grid-two">
              <FormField
                label="Provider"
                help="Providers are enabled in Helm and appear here after redeploy. The local admin account is always available for recovery, but role bindings only apply to external providers."
              >
                <select
                  disabled={!hasExternalProviders}
                  value={provider}
                  onChange={(event) => setProvider(event.target.value)}
                >
                  {externalProviders.map((item) => (
                    <option key={item.name} value={item.name}>
                      {item.label}
                    </option>
                  ))}
                </select>
              </FormField>
              <FormField
                label="Binding type"
                help="Use group bindings to pre-provision access before a user logs in. Use observed user bindings after someone has already authenticated successfully."
              >
                <select value={bindingType} onChange={(event) => setBindingType(event.target.value as "group" | "user")}>
                  <option value="group">Group</option>
                  <option value="user">Observed user</option>
                </select>
              </FormField>
            </div>
            <div className="grid-two">
              <FormField
                label="Role"
                help="Readers observe redacted status. Operators manage recipes, suppressions, runtime cadence, and non-secret adapter settings. Admins manage credentials and RBAC."
              >
                <select value={role} onChange={(event) => setRole(event.target.value as "reader" | "operator" | "admin")}>
                  <option value="reader">Reader</option>
                  <option value="operator">Operator</option>
                  <option value="admin">Admin</option>
                </select>
              </FormField>
              <FormField
                label="Principal search"
                help="Observed users appear here after a successful login through Auth0, Azure AD, or Active Directory. Search narrows the stored principal list before you choose a user binding."
              >
                <input value={search} onChange={(event) => setSearch(event.target.value)} placeholder="Filter observed users" />
              </FormField>
            </div>

            {bindingType === "group" ? (
              <FormField
                label="External group"
                help="Enter the group name exactly as PoundCake sees it after provider normalization. For Active Directory this is usually the extracted CN; for Auth0 and Azure AD it is usually the exact group claim value."
              >
                <input
                  value={externalGroup}
                  onChange={(event) => setExternalGroup(event.target.value)}
                  placeholder="monitoring-operators"
                />
              </FormField>
            ) : (
              <FormField
                label="Observed user"
                help="Choose a user who has already logged in and been recorded by PoundCake. If the person is missing here, have them authenticate once or create a group binding instead."
              >
                <select
                  disabled={!hasExternalProviders}
                  value={selectedPrincipalId}
                  onChange={(event) => setSelectedPrincipalId(event.target.value)}
                >
                  <option value="">Choose a user</option>
                  {visiblePrincipals.map((item) => (
                    <option key={item.id} value={item.id}>
                      {item.display_name || item.username}
                    </option>
                  ))}
                </select>
              </FormField>
            )}

            <div className="form-actions">
              <button
                className="primary-button"
                disabled={createDisabled}
                type="button"
                onClick={() => createMutation.mutate()}
              >
                {createMutation.isPending ? "Saving..." : "Create policy"}
              </button>
            </div>
          </div>
        </Panel>

        <HelpRail
          title="RBAC help"
          items={[
            {
              label: "operator role",
              description: "Operators author recipes, manage suppressions, and tune safe runtime cadence and limits.",
            },
            {
              label: "admin role",
              description: "Admins manage RBAC, adapter credentials, ingredient template writes, and scheduled task payload definitions.",
            },
            {
              label: "service role",
              description: "Internal services own raw runtime execution detail through scoped HMAC routes.",
            },
            {
              label: "local admin",
              description: "The local bootstrap account reports the admin role and stays outside provider role bindings.",
            },
            {
              label: "Adding providers",
              description: "Auth0, Azure AD, and Active Directory are enabled through Helm auth settings and secrets, then they appear here for binding management. This page does not create provider connections by itself.",
            },
          ]}
        />
      </div>

      <Panel title="RBAC policies" subtitle="Change roles inline or remove bindings that are no longer needed.">
        <div className="table-wrap">
          <table>
            <thead>
              <tr>
                <th>Provider</th>
                <th>Type</th>
                <th>Target</th>
                <th>Role</th>
                <th>Created by</th>
                <th>Updated</th>
                <th />
              </tr>
            </thead>
            <tbody>
              {bindingsQuery.data.map((binding) => (
                <tr key={binding.id}>
                  <td>{binding.provider}</td>
                  <td>{binding.binding_type}</td>
                  <td>{binding.binding_type === "group" ? binding.external_group || "-" : binding.principal?.display_name || binding.principal?.username || "-"}</td>
                  <td>
                    <select
                      value={binding.role}
                      onChange={(event) => updateMutation.mutate({ id: binding.id, nextRole: event.target.value })}
                    >
                      <option value="reader">Reader</option>
                      <option value="operator">Operator</option>
                      <option value="admin">Admin</option>
                    </select>
                  </td>
                  <td>{binding.created_by || "-"}</td>
                  <td>{formatDate(binding.updated_at)}</td>
                  <td className="action-cell">
                    <button
                      className="danger-button"
                      disabled={deleteMutation.isPending}
                      type="button"
                      onClick={() => {
                        if (window.confirm("Delete this RBAC policy?")) {
                          deleteMutation.mutate(binding.id);
                        }
                      }}
                    >
                      Delete
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </Panel>

      <Panel title="Observed principals" subtitle="Users appear here after a successful external-provider login.">
        <div className="table-wrap">
          <table>
            <thead>
              <tr>
                <th>Provider</th>
                <th>User</th>
                <th>Groups</th>
                <th>Last seen</th>
              </tr>
            </thead>
            <tbody>
              {principalsQuery.data.map((item) => (
                <tr key={item.id}>
                  <td>{item.provider}</td>
                  <td>{item.display_name || item.username}</td>
                  <td>{item.groups.length ? item.groups.join(", ") : "-"}</td>
                  <td>{formatDate(item.last_seen_at)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </Panel>
    </div>
  );
}

function NavGroup({
  title,
  items,
}: {
  title: string;
  items: Array<{ to: string; label: string }>;
}) {
  return (
    <div className="nav-group">
      <div className="nav-group-title">{title}</div>
      <div className="nav-group-links">
        {items.map((item) => (
          <NavLink
            className={({ isActive }) => `nav-link ${isActive ? "active" : ""}`}
            key={item.to}
            to={item.to}
          >
            {item.label}
          </NavLink>
        ))}
      </div>
    </div>
  );
}

function PageHeader({ title, description }: { title: string; description: string }) {
  return (
    <section className="page-header">
      <div className="eyebrow">PoundCake workspace</div>
      <h3>{title}</h3>
      <p>{description}</p>
    </section>
  );
}

function WorkflowRepoSyncPanel({
  canClear,
  settings,
  canEdit,
  isPending,
  onExport,
  onImport,
  onClear,
}: {
  settings: AppSettings;
  canClear: boolean;
  canEdit: boolean;
  isPending: boolean;
  onExport: () => void;
  onImport: () => void;
  onClear: () => void;
}) {
  return (
    <Panel
      title="Repo sync"
      subtitle="Import and export recipes and ingredient templates together from one place so template dependencies are loaded before recipes."
    >
      {!settings.git_enabled ? (
        <EmptyState message="Git integration is disabled. Set git.enabled, git.repoUrl, git.workflowsPath, and git.actionsPath in Helm before using repo import/export." />
      ) : (
        <div className="form-stack">
          <div className="helper-card">
            <strong>Configured repository</strong>
            <p>{formatRepoLocation(settings.git_repo_url, settings.git_branch)}</p>
            <p>Recipes directory: {settings.git_workflows_path || "-"}</p>
            <p>Ingredient templates directory: {settings.git_actions_path || "-"}</p>
            <p>Import loads ingredient templates first and then recipes so step references can resolve in the same run.</p>
          </div>
          {!canClear ? (
            <div className="helper-card">
              <strong>Admin access required for clear</strong>
              <p>Only admins can clear recipes and ingredient templates.</p>
            </div>
          ) : null}
          <div className="form-actions">
            <button className="ghost-button" disabled={!canEdit || isPending} type="button" onClick={onExport}>
              {isPending ? "Working..." : "Export to repo"}
            </button>
            <button className="ghost-button" disabled={!canEdit || isPending} type="button" onClick={onImport}>
              {isPending ? "Working..." : "Import from repo"}
            </button>
            <DangerConfirmButton
              dangerMessage="This removes every user-visible recipe and ingredient template currently stored in PoundCake. Import from repo does not delete missing items automatically."
              disabled={!canClear || isPending}
              isPending={isPending}
              label="Clear recipes and templates"
              title="Clear recipes and ingredient templates?"
              onConfirm={onClear}
            />
          </div>
          <div className="login-note">
            Repo sync for recipes and ingredient templates lives here. Use clear first when you want the repo to become the full visible recipe and template set.
          </div>
        </div>
      )}
    </Panel>
  );
}

function DangerConfirmButton({
  title,
  label,
  dangerMessage,
  disabled,
  isPending,
  onConfirm,
}: {
  title: string;
  label: string;
  dangerMessage: string;
  disabled: boolean;
  isPending: boolean;
  onConfirm: () => void;
}) {
  const [open, setOpen] = useState(false);
  const [confirmation, setConfirmation] = useState("");
  const confirmed = confirmation.trim().toLowerCase() === "yes";

  function closeDialog() {
    setOpen(false);
    setConfirmation("");
  }

  function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!confirmed || isPending) {
      return;
    }
    closeDialog();
    onConfirm();
  }

  return (
    <>
      <button className="danger-button" disabled={disabled} type="button" onClick={() => setOpen(true)}>
        {isPending ? "Working..." : label}
      </button>
      {open ? (
        <div aria-modal="true" className="dialog-backdrop" role="dialog">
          <div className="dialog-card">
            <div className="panel-head">
              <div>
                <h4>{title}</h4>
                <p className="danger-note">{dangerMessage}</p>
              </div>
            </div>
            <form className="form-stack" onSubmit={handleSubmit}>
              <div className="helper-card">
                <strong>Danger zone</strong>
                <p>Type "yes" to continue.</p>
              </div>
              <input
                autoFocus
                className="dialog-input"
                onChange={(event) => setConfirmation(event.target.value)}
                placeholder="yes"
                value={confirmation}
              />
              <div className="form-actions">
                <button className="ghost-button" type="button" onClick={closeDialog}>
                  Cancel
                </button>
                <button className="danger-button" disabled={!confirmed || isPending} type="submit">
                  {isPending ? "Working..." : label}
                </button>
              </div>
            </form>
          </div>
        </div>
      ) : null}
    </>
  );
}

function HelpRail({
  title,
  items,
}: {
  title: string;
  items: Array<{ label: string; description: string }>;
}) {
  return (
    <aside className="help-rail">
      <div className="eyebrow">Page guide</div>
      <h4>{title}</h4>
      <div className="help-list">
        {items.map((item) => (
          <div className="help-item" key={item.label}>
            <strong>{item.label}</strong>
            <p>{item.description}</p>
          </div>
        ))}
      </div>
    </aside>
  );
}

function Panel({
  title,
  subtitle,
  actions,
  children,
}: {
  title: string;
  subtitle?: string;
  actions?: React.ReactNode;
  children: React.ReactNode;
}) {
  return (
    <section className="panel-card">
      <div className="panel-head">
        <div>
          <h4>{title}</h4>
          {subtitle ? <p>{subtitle}</p> : null}
        </div>
        {actions ? <div>{actions}</div> : null}
      </div>
      {children}
    </section>
  );
}

function MetricCard({
  title,
  value,
  tone,
  children,
}: {
  title: string;
  value: string;
  tone: string;
  children?: React.ReactNode;
}) {
  return (
    <div className={`metric-card tone-${statusTone(tone)}`}>
      <span>{title}</span>
      <strong>{value}</strong>
      <p>{children}</p>
    </div>
  );
}

function MetricPill({ label, value }: { label: string; value: string }) {
  return (
    <div className="metric-pill">
      <span>{label}</span>
      <strong>{titleize(value)}</strong>
    </div>
  );
}

function StatusListItem({ label, value }: { label: string; value: string }) {
  return (
    <div className="status-list-item">
      <span>{label}</span>
      <strong>{titleize(value)}</strong>
    </div>
  );
}

function formatPluginTier(value: ServicePluginSummaryRecord["plugin_tier"]): string {
  return value === "supported" ? "Supported" : "Community";
}

function scheduledTaskStateLabel(task: ScheduledTaskStatusRecord) {
  if (!task.is_enabled || task.status === "disabled") {
    return "paused";
  }
  if (task.status === "idle") {
    return "waiting for next run";
  }
  return titleize(task.status);
}

function isOperatorRunnableScheduledTask(task: ScheduledTaskStatusRecord) {
  return Boolean(
    task.source === "plugin_manifest" &&
      task.is_enabled &&
      (task.status === "idle" || task.status === "queued") &&
      (task.task_type === "plugin_health_check" || task.task_type === "service_execution") &&
      task.service_type &&
      task.service_exec,
  );
}

function scheduledTaskRunActionLabel(task: ScheduledTaskStatusRecord) {
  return task.run_now_label || "Run task";
}

function scheduledTaskRunBlockedMessage({
  adapterConfigDirty,
  canUseSavedAdapterState,
  pluginEnabled,
  task,
  taskConfigDirty,
}: {
  adapterConfigDirty: boolean;
  canUseSavedAdapterState: boolean;
  pluginEnabled: boolean;
  task: ScheduledTaskStatusRecord;
  taskConfigDirty: boolean;
}) {
  const label = scheduledTaskRunActionLabel(task);
  if (taskConfigDirty) {
    return "Save scheduled task changes before running this task.";
  }
  if (adapterConfigDirty || !canUseSavedAdapterState) {
    return "Save adapter connection changes before running this task.";
  }
  if (!pluginEnabled) {
    return "Enable the adapter before running this task.";
  }
  if (task.source !== "plugin_manifest") {
    return `${label} is only available for plugin-advertised scheduled tasks.`;
  }
  if (!task.is_enabled || task.status === "disabled") {
    return `${label} is paused. Enable the scheduled task before running it.`;
  }
  if (task.status === "queued") {
    return "";
  }
  if (task.status !== "idle") {
    return `${label} is already ${scheduledTaskStateLabel(task)}.`;
  }
  if (
    (task.task_type !== "plugin_health_check" && task.task_type !== "service_execution") ||
    !task.service_type ||
    !task.service_exec
  ) {
    return `${label} is not available for this scheduled task.`;
  }
  return "";
}

function StatusBadge({
  children,
  status,
}: {
  children: React.ReactNode;
  status?: string | null;
}) {
  return <span className={`status-badge tone-${statusTone(status)}`}>{children}</span>;
}

function FormField({
  label,
  help,
  children,
}: {
  label: string;
  help?: string;
  children: React.ReactNode;
}) {
  return (
    <label className="form-field">
      <span className="field-label">
        {label}
        {help ? <HelpBubble help={help} label={label} /> : null}
      </span>
      {children}
    </label>
  );
}

function HelpBubble({ label, help }: { label: string; help: string }) {
  const [open, setOpen] = useState(false);
  const tooltipId = useId();

  function handleBlur(event: FocusEvent<HTMLSpanElement>) {
    if (!event.currentTarget.contains(event.relatedTarget as Node | null)) {
      setOpen(false);
    }
  }

  return (
    <span
      className="help-bubble"
      onBlur={handleBlur}
      onMouseEnter={() => setOpen(true)}
      onMouseLeave={() => setOpen(false)}
    >
      <button
        aria-describedby={open ? tooltipId : undefined}
        aria-expanded={open}
        aria-label={`Help for ${label}`}
        className="help-dot"
        onClick={() => setOpen((current) => !current)}
        onFocus={() => setOpen(true)}
        type="button"
      >
        ?
      </button>
      <span className={`help-popover ${open ? "open" : ""}`} id={tooltipId} role="tooltip">
        {help}
      </span>
    </span>
  );
}

function FieldError({ message }: { message?: string }) {
  return message ? <span className="field-error">{message}</span> : null;
}

function KeyValue({ label, value }: { label: string; value: string }) {
  return (
    <div className="kv-item">
      <span>{label}</span>
      <strong>{value}</strong>
    </div>
  );
}

function DetailList({ children, compact = false }: { children: ReactNode; compact?: boolean }) {
  return <div className={`detail-list ${compact ? "compact" : ""}`}>{children}</div>;
}

function DetailRow({ label, value }: { label: string; value: string }) {
  return (
    <div className="detail-row">
      <span>{label}</span>
      <strong>{value}</strong>
    </div>
  );
}

function EmptyState({ message }: { message: string }) {
  return <div className="empty-state">{message}</div>;
}

function PageLoading({ message }: { message: string }) {
  return <div className="loading-card">{message}</div>;
}

function PageError({ message, compact = false }: { message: string; compact?: boolean }) {
  return <div className={`error-card ${compact ? "compact" : ""}`}>{message}</div>;
}

function FullscreenState({
  title,
  message,
  tone = "neutral",
}: {
  title: string;
  message: string;
  tone?: "neutral" | "error";
}) {
  return (
    <div className="fullscreen-state">
      <div className={`fullscreen-card ${tone}`}>
        <div className="eyebrow">PoundCake</div>
        <h1>{title}</h1>
        <p>{message}</p>
      </div>
    </div>
  );
}

function useSettings() {
  const settings = useContext(SettingsContext);
  if (!settings) {
    throw new Error("Settings context is missing");
  }
  return settings;
}

function usePrincipal() {
  const principal = useContext(PrincipalContext);
  if (!principal) {
    throw new Error("Principal context is missing");
  }
  return principal;
}

function useServicePlugins() {
  return useContext(ServicePluginsContext);
}

function useToast() {
  return useContext(ToastContext);
}

function summarizePluginHealth(plugins: ServicePluginSummaryRecord[]) {
  const ready = plugins.filter((plugin) => plugin.health_status === "healthy").length;
  const initializing = plugins.filter((plugin) => plugin.health_status === "initializing").length;
  return {
    ready,
    initializing,
    notReady: plugins.length - ready,
  };
}

function hasRole(
  principal: AuthMeRecord,
  role: "reader" | "operator" | "admin",
) {
  if (principal.role === "service") {
    return false;
  }
  const order = {
    reader: 0,
    operator: 1,
    admin: 2,
  } as const;
  return order[principal.role] >= order[role];
}

function rbacRoleLabel(principal: AuthMeRecord) {
  return titleize(principal.role);
}

function canManageSuppressions(principal: AuthMeRecord) {
  return hasRole(principal, "operator");
}

function canManageWorkflows(principal: AuthMeRecord) {
  return hasRole(principal, "operator");
}



function canManageRepoSyncClear(principal: AuthMeRecord) {
  return hasRole(principal, "admin");
}

function canManageGlobalCommunications(principal: AuthMeRecord) {
  return hasRole(principal, "admin");
}

function canManageAccess(principal: AuthMeRecord) {
  return hasRole(principal, "admin");
}

function getRouteName(pathname: string): string {
  if (pathname.startsWith("/orders")) return "Orders";
  if (pathname.startsWith("/communication-routes")) return "Communication Routes";
  if (pathname.startsWith("/suppressions")) return "Suppressions";
  if (pathname.startsWith("/execution-activity")) return "Work Execution Activity";
  if (pathname.startsWith("/system-activity")) return "System Activity";
  if (pathname.startsWith("/config/alerts")) return "Alerts";
  if (pathname.startsWith("/config/alert-rules")) return "Alert Rules";
  if (pathname.startsWith("/config/plugins")) return "Plugins";
  if (pathname.startsWith("/config/communication-policy")) return "Communication Policy";
  if (pathname.startsWith("/config/recipes")) return "Recipes";
  if (pathname.startsWith("/config/ingredient-templates")) return "Ingredient Templates";
  if (pathname.startsWith("/config/access")) return "RBAC";
  return "Overview";
}

function isLoginPath(pathname: string): boolean {
  const normalized = pathname.replace(/\/+$/, "") || "/";
  return normalized === "/login";
}

function getLoginNextTarget(searchParams: URLSearchParams): string {
  const raw = searchParams.get("next");
  if (!raw || !raw.startsWith("/")) {
    return "/overview";
  }
  if (raw === "/login" || raw.startsWith("/login?")) {
    return "/overview";
  }
  return raw;
}

function isTimelineEventHighlighted(
  event: IncidentTimelineEvent,
  highlightedCommunicationId?: string,
  highlightedDishId?: string,
): boolean {
  if (highlightedDishId && event.correlation_ids.dish_id === highlightedDishId) {
    return true;
  }
  if (
    highlightedCommunicationId
    && (event.correlation_ids.bakery_operation_id === highlightedCommunicationId
      || event.correlation_ids.bakery_ticket_id === highlightedCommunicationId)
  ) {
    return true;
  }
  return false;
}

function getErrorMessage(error: unknown): string {
  if (error && typeof error === "object" && "message" in error) {
    return String((error as { message: unknown }).message);
  }
  return "Something went wrong.";
}

function logOperatorAction(payload: UIOperatorActionRequest): void {
  void apiPost(
    "/api/v1/ui/operator-actions",
    uiOperatorActionResponseSchema,
    uiOperatorActionRequestSchema.parse(payload),
  ).catch(() => undefined);
}

function isGatewayTimeoutError(error: unknown): boolean {
  return error instanceof ApiError && error.status === 504;
}

function wait(ms: number): Promise<void> {
  return new Promise((resolve) => window.setTimeout(resolve, ms));
}

function formatRepoLocation(repoUrl: string | null, branch: string | null): string {
  if (!repoUrl) {
    return "Repository URL is not configured.";
  }
  return branch ? `${repoUrl} • branch ${branch}` : repoUrl;
}

function formatRepoSyncMessage(result: RepoSyncResponse): string {
  let message = result.message;
  if (result.warnings?.length) {
    message = `${message} ${result.warnings.join(" ")}`;
  }
  if (result.pull_request?.url) {
    return `${message} Pull request created.`;
  }
  if (result.branch) {
    return `${message} Branch ${result.branch} created.`;
  }
  return message;
}

function describeAuthProviderModes(provider: AuthProviderRecord): string {
  if (provider.password_login) {
    return "Password login enabled.";
  }
  if (provider.browser_login && provider.device_login) {
    return "Browser login and CLI device login enabled.";
  }
  if (provider.browser_login) {
    return "Browser login enabled.";
  }
  if (provider.device_login) {
    return "CLI device login enabled.";
  }
  return "Provider available.";
}

function parseJsonObject(value?: string, label?: string): Record<string, unknown> | undefined {
  if (!value || !value.trim()) {
    return undefined;
  }
  try {
    const parsed = JSON.parse(value);
    if (!parsed || Array.isArray(parsed) || typeof parsed !== "object") {
      throw new Error();
    }
    return parsed as Record<string, unknown>;
  } catch {
    throw new Error(`${label || "JSON"} must be a valid object.`);
  }
}

function parseOptionalJson(value?: string, label?: string): Record<string, unknown> | undefined {
  if (!value || !value.trim()) {
    return undefined;
  }
  try {
    const parsed = JSON.parse(value);
    if (Array.isArray(parsed) || typeof parsed !== "object" || parsed === null) {
      throw new Error();
    }
    return parsed as Record<string, unknown>;
  } catch {
    throw new Error(`${label || "JSON"} must be a valid object.`);
  }
}

function normalizeProviderConfigForForm(
  executionTarget: string,
  providerConfig: Record<string, unknown> | undefined,
): Record<string, unknown> {
  const config = { ...(providerConfig || {}) };
  if (executionTarget === "github") {
    if (Array.isArray(config.labels)) {
      config.labels = config.labels.join(", ");
    }
    if (Array.isArray(config.assignees)) {
      config.assignees = config.assignees.join(", ");
    }
  }
  return config;
}

function emptyCommunicationRoute(executionTarget = "") {
  return {
    id: crypto.randomUUID(),
    label: "",
    execution_target: executionTarget,
    destination_target: "",
    provider_config: {},
    enabled: true,
    position: 1,
  };
}

function communicationTargetsFromPolicy(
  policy: Pick<CommunicationPolicyRecord, "routes"> & Partial<Pick<CommunicationPolicyRecord, "available_routes">>,
  currentRoutes: Array<{ execution_target?: string }> = [],
): string[] {
  const targets = [
    ...(policy.available_routes || []),
    ...(policy.routes || []),
    ...currentRoutes,
  ]
    .map((route) => route.execution_target)
    .filter((target): target is string => Boolean(target && target.trim()));
  return Array.from(new Set(targets)).sort((left, right) => left.localeCompare(right));
}

function providerConfigSummary(
  executionTarget: string,
  providerConfig: Record<string, unknown> | undefined,
): string {
  const config = providerConfig || {};
  const pairs = Object.entries(config)
    .filter(([, value]) => value !== null && value !== undefined && value !== "")
    .map(([key, value]) => `${key}=${Array.isArray(value) ? value.join(",") : String(value)}`);
  if (!pairs.length) {
    return executionTarget === "teams" || executionTarget === "discord"
      ? "No provider config required"
      : "No provider config set";
  }
  return pairs.join(" | ");
}

function CommunicationRouteProviderConfigFields({
  form,
  basePath,
  executionTarget,
}: {
  form: ReturnType<typeof useForm<any>>;
  basePath: string;
  executionTarget: string;
}) {
  const register = (suffix: string) => form.register(`${basePath}.provider_config.${suffix}` as any);

  if (executionTarget === "rackspace_core") {
    return (
      <div className="grid-two">
        <FormField label="Account number" help="Required Rackspace Core account number for this route.">
          <input {...register("account_number")} placeholder="1234567" />
        </FormField>
        <FormField label="Queue" help="Optional Core queue. Bakery defaults still apply when this is blank.">
          <input {...register("queue")} placeholder="Example Support" />
        </FormField>
        <FormField label="Subcategory" help="Optional Core subcategory. Bakery defaults still apply when this is blank.">
          <input {...register("subcategory")} placeholder="Monitoring" />
        </FormField>
        <FormField label="Source" help="Optional Core source label such as Automation.">
          <input {...register("source")} placeholder="Automation" />
        </FormField>
      </div>
    );
  }

  if (executionTarget === "servicenow") {
    return (
      <div className="grid-two">
        <FormField label="Urgency" help="Optional ServiceNow urgency value.">
          <input {...register("urgency")} placeholder="3" />
        </FormField>
        <FormField label="Impact" help="Optional ServiceNow impact value.">
          <input {...register("impact")} placeholder="3" />
        </FormField>
      </div>
    );
  }

  if (executionTarget === "jira") {
    return (
      <div className="grid-two">
        <FormField label="Project key" help="Required Jira project key for this route.">
          <input {...register("project_key")} placeholder="OPS" />
        </FormField>
        <FormField label="Issue type" help="Optional Jira issue type.">
          <input {...register("issue_type")} placeholder="Task" />
        </FormField>
      </div>
    );
  }

  if (executionTarget === "github") {
    return (
      <div className="grid-two">
        <FormField label="Owner" help="Required GitHub org or user name.">
          <input {...register("owner")} placeholder="rackerlabs" />
        </FormField>
        <FormField label="Repo" help="Required GitHub repository name.">
          <input {...register("repo")} placeholder="poundcake" />
        </FormField>
        <FormField label="Labels" help="Optional comma-separated GitHub labels.">
          <input {...register("labels")} placeholder="alert, monitoring" />
        </FormField>
        <FormField label="Assignees" help="Optional comma-separated GitHub assignees.">
          <input {...register("assignees")} placeholder="octocat" />
        </FormField>
      </div>
    );
  }

  if (executionTarget === "pagerduty") {
    return (
      <div className="grid-two">
        <FormField label="Service ID" help="Required PagerDuty service id.">
          <input {...register("service_id")} placeholder="PXXXXXX" />
        </FormField>
        <FormField label="From email" help="Required PagerDuty sender email.">
          <input {...register("from_email")} placeholder="alerts@example.com" />
        </FormField>
        <FormField label="Urgency" help="Optional PagerDuty urgency override.">
          <input {...register("urgency")} placeholder="high" />
        </FormField>
      </div>
    );
  }

  return (
    <div className="helper-card">
      <strong>Provider config</strong>
      <p>This provider does not require extra route configuration.</p>
    </div>
  );
}

function moveField(
  fieldArray: ReturnType<typeof useFieldArray<z.infer<typeof workflowSchema>, "recipe_ingredients">>,
  index: number,
  direction: number,
) {
  const nextIndex = index + direction;
  if (nextIndex < 0 || nextIndex >= fieldArray.fields.length) {
    return;
  }
  fieldArray.move(index, nextIndex);
}

function OperationField({
  action,
  form,
  index,
}: {
  action: IngredientRecord | undefined;
  form: ReturnType<typeof useForm<z.infer<typeof workflowSchema>>>;
  index: number;
}) {
  const operations = getAllowedOperations(action);
  if (operations.length <= 1) {
    return null;
  }
  return (
    <FormField label="Operation" help="The adapter operation this ingredient template will perform.">
      <select {...form.register(`recipe_ingredients.${index}.operation` as const)}>
        {operations.map((operation) => (
          <option key={operation} value={operation}>
            {operationLabel(action, operation)}
          </option>
        ))}
      </select>
    </FormField>
  );
}

function ServicePayloadFields({
  action,
  form,
  index,
}: {
  action: IngredientRecord | undefined;
  form: ReturnType<typeof useForm<z.infer<typeof workflowSchema>>>;
  index: number;
}) {
  const operation = form.watch(`recipe_ingredients.${index}.operation` as const);
  const payloadSchema = operationPayloadSchema(action, String(operation || "")) || action?.payload_schema;
  const properties = schemaProperties(payloadSchema);
  const keys = Object.keys(properties);
  if (!action || keys.length === 0) {
    return null;
  }
  return (
    <div className="grid-two">
      {keys.map((key) => {
        const property = properties[key];
        const fieldPath = `recipe_ingredients.${index}.service_payload_values.${key}` as const;
        const label = titleize(key.replace(/_/g, " "));
        if (property.type === "boolean") {
          return (
            <FormField key={key} label={label} help="Boolean payload value from this ingredient template.">
              <label className="toggle-row">
                <input type="checkbox" {...form.register(fieldPath)} />
                <span>Enabled</span>
              </label>
            </FormField>
          );
        }
        if (property.type === "object" || property.type === "array") {
          const watchedValue = form.watch(fieldPath);
          return (
            <FormField key={key} label={label} help="JSON payload value from this ingredient template.">
              <textarea
                value={
                  typeof watchedValue === "string"
                    ? watchedValue
                    : compactJson((watchedValue || {}) as Record<string, unknown>)
                }
                onChange={(event) => {
                  form.setValue(fieldPath, event.target.value);
                }}
                onBlur={(event) => {
                  try {
                    form.setValue(
                      fieldPath,
                      parseOptionalJson(event.target.value, `${label} JSON`) || {},
                    );
                  } catch {
                    form.setValue(fieldPath, event.target.value);
                  }
                }}
                rows={3}
              />
            </FormField>
          );
        }
        return (
          <FormField key={key} label={label} help="Payload value from this ingredient template.">
            <input
              type={property.type === "number" || property.type === "integer" ? "number" : "text"}
              {...form.register(fieldPath, {
                valueAsNumber: property.type === "number" || property.type === "integer",
              })}
            />
          </FormField>
        );
      })}
    </div>
  );
}

function moveCommunicationRoute(
  fieldArray: {
    fields: Array<{ id: string }>;
    move: (from: number, to: number) => void;
  },
  index: number,
  direction: number,
) {
  const nextIndex = index + direction;
  if (nextIndex < 0 || nextIndex >= fieldArray.fields.length) {
    return;
  }
  fieldArray.move(index, nextIndex);
}

function resetWorkflowForm(
  form: ReturnType<typeof useForm<z.infer<typeof workflowSchema>>>,
  steps: ReturnType<typeof useFieldArray<z.infer<typeof workflowSchema>, "recipe_ingredients">>,
  communicationRoutes: ReturnType<typeof useFieldArray<z.infer<typeof workflowSchema>, "communications_routes">>,
  globalCommunicationsConfigured: boolean,
) {
  form.reset({
    name: "",
    description: "",
    enabled: true,
    clear_timeout_sec: "",
    communications_mode: globalCommunicationsConfigured ? "inherit" : "local",
    communications_routes: [],
    recipe_ingredients: [],
  });
  steps.replace([
    {
      ingredient_id: 0,
      step_order: 1,
      on_success: "continue",
      run_phase: "both",
      run_condition: "always",
      parallel_group: 0,
      depth: 0,
      operation: "",
      service_payload_values: {},
      execution_parameters_override_text: "",
    },
  ]);
  communicationRoutes.replace([]);
}



function describeWorkflowStep(
  step: z.infer<typeof workflowStepSchema> | undefined,
  actions: IngredientRecord[],
): string {
  if (!step) {
    return "No step selected.";
  }
  const action = actions.find((item) => item.id === Number(step.ingredient_id));
  if (!action) {
    return "Choose an ingredient template to describe this step.";
  }
  const operation = step.operation ? ` (${operationLabel(action, step.operation)})` : "";
  return `Run ${action.task_key_template}${operation} during ${step.run_phase} when ${step.run_condition}. If it succeeds, ${step.on_success}.`;
}

function workflowStepFormDefaults(
  action?: IngredientRecord,
  parameterOverrides?: Record<string, unknown> | null,
  payloadOverrides?: Record<string, unknown> | null,
): Pick<z.infer<typeof workflowStepSchema>, "operation" | "service_payload_values"> {
  const defaultPayload = { ...((action?.service_payload_template || action?.execution_payload || {}) as Record<string, unknown>) };
  const payload = { ...defaultPayload, ...(payloadOverrides || {}) };
  const allowed = getAllowedOperations(action);
  const params = (action?.service_exec_parameters || action?.execution_parameters || {}) as Record<string, unknown>;
  const operation = String(
    parameterOverrides?.operation || params.operation || allowed[0] || "",
  );
  return {
    operation,
    service_payload_values: payload,
  };
}

function buildExecutionParametersForStep(
  step: z.infer<typeof workflowStepSchema>,
  action: IngredientRecord | undefined,
  advancedOverrides: Record<string, unknown> | null,
): Record<string, unknown> | null {
  const allowed = getAllowedOperations(action);
  const operation = String(step.operation || allowed[0] || "").trim();
  const merged = { ...(advancedOverrides || {}) };
  if (allowed.length > 0 && operation) {
    merged.operation = operation;
  }
  return Object.keys(merged).length > 0 ? merged : null;
}

function buildServicePayloadForStep(
  step: z.infer<typeof workflowStepSchema>,
  action: IngredientRecord | undefined,
): Record<string, unknown> | null {
  const payloadSchema = operationPayloadSchema(action, step.operation || "") || action?.payload_schema;
  const properties = schemaProperties(payloadSchema);
  const required = schemaRequired(payloadSchema);
  const values = step.service_payload_values || {};
  const payload: Record<string, unknown> = {};
  for (const key of Object.keys(properties)) {
    const value = values[key];
    if (value === undefined || value === null) {
      continue;
    }
    if (typeof value === "string" && value.trim() === "" && !required.has(key)) {
      continue;
    }
    payload[key] = value;
  }
  return Object.keys(payload).length > 0 ? payload : null;
}

function getAllowedOperations(action?: IngredientRecord): string[] {
  const params = (action?.service_exec_parameters || action?.execution_parameters || {}) as Record<string, unknown>;
  const raw = params.allowed_operations;
  if (!Array.isArray(raw)) {
    return [];
  }
  return raw.map((item) => String(item).trim()).filter(Boolean);
}

function operationLabel(action: IngredientRecord | undefined, operation: string): string {
  const params = (action?.service_exec_parameters || action?.execution_parameters || {}) as Record<string, unknown>;
  const metadata = params.operation_metadata;
  if (metadata && typeof metadata === "object" && !Array.isArray(metadata)) {
    const entry = (metadata as Record<string, unknown>)[operation];
    if (entry && typeof entry === "object" && !Array.isArray(entry)) {
      const label = (entry as Record<string, unknown>).label;
      if (typeof label === "string" && label.trim()) {
        return label;
      }
    }
  }
  return titleize(operation.replace(/_/g, " "));
}

function operationPayloadSchema(action: IngredientRecord | undefined, operation: string): unknown {
  const params = (action?.service_exec_parameters || action?.execution_parameters || {}) as Record<string, unknown>;
  const metadata = params.operation_metadata;
  if (!metadata || typeof metadata !== "object" || Array.isArray(metadata)) {
    return null;
  }
  const entry = (metadata as Record<string, unknown>)[operation];
  if (!entry || typeof entry !== "object" || Array.isArray(entry)) {
    return null;
  }
  const schema = (entry as Record<string, unknown>).payload_schema;
  return schema && typeof schema === "object" && !Array.isArray(schema) ? schema : null;
}

function schemaProperties(schema: unknown): Record<string, { type?: string }> {
  if (!schema || typeof schema !== "object" || Array.isArray(schema)) {
    return {};
  }
  const properties = (schema as Record<string, unknown>).properties;
  if (!properties || typeof properties !== "object" || Array.isArray(properties)) {
    return {};
  }
  return properties as Record<string, { type?: string }>;
}

function schemaRequired(schema: unknown): Set<string> {
  if (!schema || typeof schema !== "object" || Array.isArray(schema)) {
    return new Set();
  }
  const required = (schema as Record<string, unknown>).required;
  if (!Array.isArray(required)) {
    return new Set();
  }
  return new Set(required.map((item) => String(item)));
}

function buildWorkflowPreview(
  steps: z.infer<typeof workflowStepSchema>[],
  actions: IngredientRecord[],
  workflowName: string,
  communicationsMode: "inherit" | "local",
  routes: Array<Pick<CommunicationRouteRecord, "label" | "execution_target" | "enabled">>,
): string {
  const fragments = steps
    .map((step, index) => {
      const action = actions.find((item) => item.id === Number(step.ingredient_id));
      if (!action) {
        return `step ${index + 1} is waiting for an ingredient template`;
      }
      return `${step.run_phase} -> ${action.task_key_template} (${titleize(action.execution_target)})`;
    })
    .filter(Boolean);
  const enabledRoutes = routes.filter((route) => route.enabled);
  const communicationsSummary = communicationsMode === "inherit"
    ? enabledRoutes.length
      ? `inherit ${enabledRoutes.length} communication policy route(s)`
      : "inherit no configured communication policy routes yet"
    : enabledRoutes.length
      ? `use ${enabledRoutes.length} recipe-specific communication route(s)`
      : "use recipe-specific communication routes with no enabled routes yet";
  if (!fragments.length) {
    return `${workflowName || "This recipe"} will ${communicationsSummary} once you add recipe steps.`;
  }
  return `${workflowName || "This recipe"} will ${communicationsSummary}, then run ${fragments.join(", then ")}.`;
}





function normalizeUiConfig(config: Record<string, unknown>): Record<string, string | boolean> {
  const next: Record<string, string | boolean> = {};
  for (const [key, value] of Object.entries(config)) {
    next[key] = typeof value === "boolean" ? value : String(value ?? "");
  }
  return next;
}

function serializeUiConfig(
  config: Record<string, string | boolean>,
  schema?: Record<string, unknown>,
): Record<string, unknown> {
  const next: Record<string, unknown> = {};
  for (const [key, value] of Object.entries(config)) {
    if (typeof value === "boolean") {
      next[key] = value;
      continue;
    }
    const trimmed = value.trim();
    next[key] = isNumericOperatorConfigField(schema, key) && trimmed ? Number(trimmed) : trimmed;
  }
  return next;
}

function comparableOperatorConfig(
  config: Record<string, unknown>,
  schema?: Record<string, unknown>,
): string {
  const normalized: Record<string, unknown> = {};
  for (const key of Object.keys(config).sort()) {
    const value = config[key];
    if (typeof value === "boolean") {
      normalized[key] = value;
      continue;
    }
    if (typeof value === "number") {
      normalized[key] = Number.isFinite(value) ? value : "";
      continue;
    }
    const trimmed = String(value ?? "").trim();
    const numeric = isNumericOperatorConfigField(schema, key) && trimmed ? Number(trimmed) : NaN;
    normalized[key] = Number.isFinite(numeric) ? numeric : trimmed;
  }
  return JSON.stringify(normalized);
}

function isNumericOperatorConfigField(schema: Record<string, unknown> | undefined, key: string): boolean {
  const properties = schema?.properties;
  if (properties && typeof properties === "object" && !Array.isArray(properties)) {
    const property = (properties as Record<string, Record<string, unknown>>)[key];
    const type = property ? String(property.type || "").toLowerCase() : "";
    if (type === "number" || type === "integer") {
      return true;
    }
  }
  return key.endsWith("_seconds");
}

function hasRequiredCredentialRequirement(requirements: Array<Record<string, unknown>> | undefined): boolean {
  return Boolean(requirements?.some((requirement) => requirement.required === true));
}

function editableCredentialRequirements(
  requirements: Array<Record<string, unknown>> | undefined,
): Array<Record<string, unknown>> {
  return requirements?.filter((requirement) => requirement.managed !== true) || [];
}

function activeCredentialRequirement(
  requirements: Array<Record<string, unknown>> | undefined,
  credentialType: string | null | undefined,
): Record<string, unknown> | undefined {
  if (!requirements?.length) {
    return undefined;
  }
  if (!credentialType) {
    return requirements[0];
  }
  return requirements.find((requirement) => requirement.credential_type === credentialType) || requirements[0];
}

function credentialPayloadFields(
  requirements: Array<Record<string, unknown>> | undefined,
  credentialType: string | null | undefined,
): Array<{ name: string; label: string; help?: string }> {
  const requirement = activeCredentialRequirement(requirements, credentialType);
  const schema = requirement?.credential_schema;
  if (schema && typeof schema === "object" && !Array.isArray(schema)) {
    const properties = (schema as Record<string, unknown>).properties;
    if (properties && typeof properties === "object" && !Array.isArray(properties)) {
      return Object.entries(properties as Record<string, Record<string, unknown>>).map(
        ([name, property]) => ({
          name,
          label: credentialPayloadFieldLabel(
            credentialType,
            name,
            String(property.title || titleize(name.replace(/_/g, " "))),
          ),
          help: credentialPayloadFieldHelp(credentialType, name),
        }),
      );
    }
  }
  const fallback = defaultCredentialField(credentialType);
  return [{
    name: fallback,
    label: credentialPayloadFieldLabel(
      credentialType,
      fallback,
      titleize(fallback.replace(/_/g, " ")),
    ),
    help: credentialPayloadFieldHelp(credentialType, fallback),
  }];
}

function buildCredentialPayload(
  requirements: Array<Record<string, unknown>> | undefined,
  credentialType: string | null | undefined,
  credentialField: string,
  credentialInput: string,
  credentialInputs: Record<string, string>,
): Record<string, string> | undefined {
  const fields = credentialPayloadFields(requirements, credentialType);
  if (fields.length === 1) {
    const value = credentialInput.trim();
    return credentialInputHasNewValue(value) ? { [credentialField]: value } : undefined;
  }
  const payload: Record<string, string> = {};
  for (const field of fields) {
    const value = (credentialInputs[field.name] || "").trim();
    if (credentialInputHasNewValue(value)) {
      payload[field.name] = value;
    }
  }
  return Object.keys(payload).length ? payload : undefined;
}

function credentialInputHasNewValue(value: string): boolean {
  const normalized = value.trim().toLowerCase();
  return Boolean(normalized && normalized !== "leave blank to keep existing");
}

function operatorConfigFields(
  schema: Record<string, unknown> | undefined,
  config: Record<string, string | boolean>,
): Array<{ name: string; label: string; type: string }> {
  const properties = schema?.properties;
  if (properties && typeof properties === "object" && !Array.isArray(properties)) {
    return Object.entries(properties as Record<string, Record<string, unknown>>).map(
      ([name, property]) => ({
        name,
        label: String(property.title || titleize(name.replace(/_/g, " "))),
        type: String(property.type || typeof config[name] || "string"),
      }),
    );
  }
  return Object.keys(config).map((name) => ({
    name,
    label: titleize(name.replace(/_/g, " ")),
    type: typeof config[name],
  }));
}

function defaultCredentialField(credentialType: string | null | undefined): string {
  if (credentialType === "stackstorm_api_key") {
    return "api_key";
  }
  if (credentialType === "kubernetes_kubeconfig") {
    return "kubeconfig";
  }
  if (credentialType === "git_repository_auth") {
    return "token";
  }
  return "token";
}

function credentialSlotLabel(credentialType: string | null | undefined): string {
  if (credentialType === "bakery_bootstrap_hmac") {
    return "PoundCake credential slot";
  }
  return "Credential key ID";
}

function credentialSlotHelp(credentialType: string | null | undefined): string {
  if (credentialType === "bakery_bootstrap_hmac") {
    return "Local PoundCake storage slot. Keep this as default for the normal Bakery connection.";
  }
  return "A named slot for this adapter credential. Use default for the normal connection; use another key only when the same adapter needs separate credentials for different targets.";
}

function credentialValueLabel(credentialType: string | null | undefined): string {
  if (credentialType === "bakery_bootstrap_hmac") {
    return "bootstrap-key";
  }
  return "Credential value";
}

function credentialPayloadFieldLabel(
  credentialType: string | null | undefined,
  fieldName: string,
  fallback: string,
): string {
  if (credentialType === "bakery_bootstrap_hmac") {
    if (fieldName === "hmac_key_id" || fieldName === "key_id") {
      return "bootstrap-key-id";
    }
    if (fieldName === "hmac_secret") {
      return "bootstrap-key";
    }
  }
  return fallback;
}

function credentialPayloadFieldHelp(
  credentialType: string | null | undefined,
  fieldName: string,
): string | undefined {
  if (credentialType !== "bakery_bootstrap_hmac") {
    return undefined;
  }
  if (fieldName === "hmac_key_id" || fieldName === "key_id") {
    return "Paste the bootstrap-key-id minted by the remote Bakery for this PoundCake monitor.";
  }
  if (fieldName === "hmac_secret") {
    return "Paste the bootstrap-key minted by the remote Bakery for this PoundCake monitor.";
  }
  return undefined;
}

function credentialFieldOptions(
  credentialType: string | null | undefined,
): Array<{ value: string; label: string }> {
  if (credentialType === "stackstorm_api_key") {
    return [
      { value: "api_key", label: "API key" },
      { value: "auth_token", label: "Auth token" },
    ];
  }
  if (credentialType === "kubernetes_kubeconfig") {
    return [
      { value: "kubeconfig", label: "Kubeconfig" },
      { value: "token", label: "Token" },
    ];
  }
  if (credentialType === "git_repository_auth") {
    return [
      { value: "token", label: "Token" },
      { value: "ssh_key_path", label: "SSH key path" },
    ];
  }
  return [
    { value: "token", label: "Token" },
    { value: "username", label: "Username" },
    { value: "password", label: "Password" },
  ];
}

function isCommunicationAction(action: IngredientRecord): boolean {
  return action.execution_engine === "bakery" && action.execution_purpose === "comms";
}

export default App;
