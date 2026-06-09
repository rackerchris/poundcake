# Backlog

## Adapter / Plugin Contract Review

This review ranks built-in adapters/plugins by next-work priority against the
current service-plugin contract. Ranking is based on contract breadth,
remaining boundary/maturity gaps, and how much additional adapter work would
unlock safer native workflows.

Recommended next adapter/plugin to work on: `k8s`

Why `k8s` next:

- It has the broadest provider-facing surface after the core contract cleanup.
- More native k8s evidence/remediation work will reduce reliance on
  StackStorm/manual-review paths across the Genestack catalog.

### 1. `k8s` (`community`) - highest next-work priority

Contract fit today:

- Strong manifest-driven ingredient coverage and scoped helper capabilities.
- Good payload validation and read-only diagnostics separation for triage
  operations.
- Narrower RBAC story than a generic cluster adapter and no direct
  control-plane route authority.

Outstanding issues:

- Add more cluster-backed e2e coverage for read-only triage and admin
  observability/detail surfaces so the broad adapter surface is proven outside
  unit tests.
- Define the promotion bar from `community` to `supported`, including required
  RBAC review, e2e coverage, and operator guidance for each enabled mutation.

### 2. `stackstorm` (`community`) - high priority

Contract fit today:

- Clear external credential boundary with `stackstorm_api_key`.
- `content_sync` is correctly modeled as service execution rather than a direct
  plugin API action.
- Adapter dispatch/poll/cancel behavior is well covered at the contract level.

Outstanding issues:

- Add more end-to-end validation that PoundCake-owned content sync, credential
  bootstrap/import, and StackStorm execution all work together against a real
  StackStorm deployment.
- Keep narrowing the line between native PoundCake adapters and StackStorm
  workflow-based integrations so StackStorm remains the multi-step/workflow adapter, not
  the default home for work that should be native in `k8s` or other domain
  adapters.
- Define supported-tier promotion criteria and required operational hardening
  for the remote StackStorm dependency.

### 3. `alertmanager` (`community`) - medium-high priority

Contract fit today:

- Narrow adapter scope with no helper/bootstrap ambiguity.
- Good credential and transport-safety story for optional auth.
- Scheduled silence sync and inspection operations match the contract cleanly.

Outstanding issues:

- Add broader end-to-end coverage for silence synchronization and inspection
  behavior under authenticated and degraded remote conditions.
- Clarify whether any additional helper or cross-adapter composition is needed,
  or whether Alertmanager should stay intentionally narrow and move directly
  toward supported-tier promotion.

### 4. `prometheus` (`community`) - medium-high priority

Contract fit today:

- Strong helper-based design for alert-rule parsing/index/render work.
- Good separation between Prometheus HTTP inspection and Kubernetes CRD
  lifecycle ownership.
- Clean optional credential boundary and transport validation.

Outstanding issues:

- Add more end-to-end coverage around `alert_evidence`, reload behavior, and
  the Prometheus plus `k8s` plus `genestack_monitoring` rule-management path.
- Define supported-tier promotion criteria, especially around helper stability
  and operator-facing monitoring rule ownership.

### 5. `github` (`community`) - medium priority

Contract fit today:

- GitHub credentials flow through the credential manager boundary. Writes (commit, PR) require an adapter token; reads use credential-manager-owned `allow_public_read` to gate unauthenticated public GitHub access.
- The `github` adapter is the preferred choice for GitHub/GitHub Enterprise repos — it uses the GitHub REST API natively and avoids filesystem dependencies.

Outstanding issues:

- Add stronger end-to-end coverage for authenticated write flows.

### 6. `git` (`community`) - medium priority

Contract fit today:

- The `git` adapter is provider-agnostic (GitHub, GitLab, generic) and uses GitPython for clone/pull workflows. PR generation delegates to the `github` adapter when `provider` is `github`; other providers return a clear skip message directing operators to the `github` adapter for PRs.
- Write operations require a `git_repository_auth` adapter credential via the credential manager.

Outstanding issues:

- Add more end-to-end coverage for write flows, especially private repository
  auth and PR creation paths.

### 7. `genestack_monitoring` (`community`) - medium priority

Contract fit today:

- Good composition model: helper dependencies are explicit and there is no
  direct provider credential ownership.
- `content_sync` is correctly modeled as service execution and not bootstrap.

Outstanding issues:

- Continue promoting alert families from generic manual-review/StackStorm
  paths into native adapter actions where there is a safe exact target model.
- Keep the helper dependency contract aligned with the evolving `k8s`,
  `prometheus`, and `github`/`git` boundaries so the composition plugin does
  not become a hidden workflow engine.

### 8. `bakery` (`supported`) - lower adapter priority

Contract fit today:

- Strongest current fit for a supported external plugin.
- Clear bootstrap credential boundary, communication route ownership, and
  remote-only provider behavior.

Outstanding issues:

- Most remaining work is cross-cutting rather than Bakery-adapter-specific:
  communication activity surface redaction/role review, bootstrap privilege
  separation, and ongoing supported-tier operational hardening.

### 9. `dummy` (`supported`) - lowest priority

Contract fit today:

- Reference implementation fully compliant with the service-plugin contract.
- Opaque receipt contract (receipt IDs owned by Expediter, extra metadata
  encoded in `result`/`raw` envelopes).
- `poll` is a read-only observation boundary.
- Demonstrates manifest discovery, helper registration, bootstrap hooks,
  ingredient templates, communication routes, scheduled health checks, expected
  outcome matching, cancellation, and Timer reconciliation.

Outstanding issues:

- Keep it as the regression/reference plugin as the contract evolves.
- Add coverage only when new cross-cutting contract features need a minimal
  reference implementation.

## Credential Surface

- Move internal control-plane HMAC toward asymmetric per-service signing. Workers
  should eventually hold private keys while the API stores only public keys, so
  API-side credential reads cannot mint service calls.
- Add deployment/runtime drift checks for database grants. Startup checks should
  fail if worker DB users can read the base service identity credential table or
  if readonly users regain credential-table access.
- Move worker auth material out of the app database over time. Prefer
  Kubernetes Secrets or an external secret manager once the bootstrap contract
  can write per-service material there.
- Add explicit credential encryption key rotation workflows. Adapter credentials
  and service identity credentials now use separate key domains; the remaining
  work is controlled rotation without widening either blast radius.
- Introduce operator-managed adapter connection records for non-secret runtime
  configuration. Production Helm values should not carry provider URLs,
  usernames, bearer tokens, repository URLs, or kubeconfigs for external
  plugin adapters; devstack may seed local fixtures only.

## Communications Activity Detail Surface

- Review `/api/v1/communications/activity` with the Bakery team. The full route
  currently exposes ticket IDs, provider references, operation IDs,
  writable/reopenable state, and last delivery errors. Decide whether the full
  route should remain reader-visible, move to operator/admin, or split into
  reader-safe status and privileged detail surfaces.

## Genestack Alert Recipe Maturity

- Promote managed Genestack recipes from generic manual-review actions to
  first-class adapter actions where the adapter can make a safe, scoped decision.
  Keep the current generic recipe as the safety net, but track each alert family
  as `native_remediation`, `workflow_remediation`, `evidence_only`,
  `manual_review`, or `adapter_gap`.
- Add richer native remediation actions for alert families that still use
  conservative manual-review routing after Prometheus, Alertmanager, GitHub,
  and K8s evidence is gathered.
- Promote remaining Kubernetes gaps into the K8s adapter before routing them
  through StackStorm: certificate/Secret inspection, richer node triage,
  PVC/PV storage triage, Service endpoint triage, PDB/HPA triage, and safe
  ConfigMap metadata diagnostics are available as read-only evidence; remaining
  work is additional domain-specific mappings where alert labels identify safe
  exact targets.
- Add domain adapters only when repeated StackStorm/manual-review workflows
  become structured enough to justify first-class capabilities. Likely
  candidates are RabbitMQ, OpenStack, MariaDB, and node/hardware diagnostics.
- Keep StackStorm for pack-owned multi-step workflows and conservative
  manual-review workflows. If a workflow is mostly a direct Kubernetes or
  Prometheus operation, prefer the native adapter.
- Let Bakery own communication lifecycle policy. PoundCake should send stable
  alert/order correlation context and communication intent; Bakery should decide
  whether to create, update/comment, reopen, or close provider records.

- Keep `/api/v1/auth/device/start` and `/api/v1/auth/device/poll` scoped to
  operator/user authentication only; do not reuse them for service execution or
  provider automation.

## Security Hardening

### Rate Limiting

- Add rate limiting (`slowapi` or middleware) to auth endpoints: `POST /api/v1/auth/login`, `POST /api/v1/auth/device/poll`, and `GET /api/v1/auth/oidc/callback`. These endpoints are susceptible to credential brute force and OIDC state exhaustion without request throttling.

### Credential Encryption Key Rotation

- Implement credential encryption key rotation workflows using Fernet's `MultiFernet` support. Add an API endpoint that accepts a new encryption key, wraps the old key for decryption of existing values, and rotates stored ciphertexts. Plugin credentials (`POUNDCAKE_PLUGIN_CREDENTIAL_ENCRYPTION_KEY`) and service identity credentials (`POUNDCAKE_SERVICE_IDENTITY_CREDENTIAL_ENCRYPTION_KEY`) should remain in separate key domains with independent rotation schedules.

## E2E Cakectl Coverage

- Keep `tests/lib.sh` as the single control-plane entrypoint for shell e2e API access. The shared helper now resolves `cakectl` and `kubectl`, respects caller-provided `API_ROOT_URL`, and surfaces port-forward logs on readiness failures so we can tell the difference between API regressions and local forwarding failures.
- Keep `tests/run_e2e.sh`, `tests/run_k8s_pod_action_e2e.sh`, `tests/run_stackstorm_action_e2e.sh`, `tests/run_stackstorm_workflow_remediation_e2e.sh`, `tests/run_genestack_content_sync_k8s_e2e.sh`, and `tests/run_genestack_managed_recipe_e2e.sh` on `cakectl` for PoundCake API requests wherever the operation targets PoundCake routes.
- Leave direct non-`cakectl` calls only where the test is intentionally validating a boundary outside the PoundCake control plane:
  - `tests/run_security_abuse_e2e.sh` still needs raw signed HTTP requests for invalid internal-HMAC, replay, tamper, and wrong-token cases because `cakectl` cannot safely craft deliberately malformed service-auth traffic.
  - `tests/run_stackstorm_workflow_remediation_e2e.sh` and `tests/run_genestack_managed_recipe_e2e.sh` still need direct Alertmanager API seeding/query calls because the test has to create live upstream alert state before PoundCake receives the webhook.
  - StackStorm-specific e2e scripts still need direct StackStorm API reads for execution confirmation until `cakectl` grows a first-class StackStorm inspection surface.

## Structured Operator Diffs

- Follow `Operator Change Safety` with a UI and CLI diff-quality pass focused on structured operator-facing objects rather than only aggregate field counts or raw JSON blobs.
- Keep the current auth, RBAC, API route, and credential-manager boundaries unchanged. This follow-on item is presentation and review-UX work only unless a separate contract review explicitly approves broader changes.
- Add field-aware before/after rendering for communication routes, recipe steps, plugin config objects, and other large mutation payloads so operators can distinguish added, removed, and edited entries quickly.
- Preserve secret redaction and reader/operator/admin boundaries while improving diff readability. Structured diffs must not expose credential material, hidden provider metadata, or service-only payloads.
- Add parity checks so the same mutation classes expose the same safety concepts in both the UI review dialogs and `cakectl --dry-run`: summary, before/after diff, consequence, immediate effect, and verification guidance.
- Prefer reusable shared diff helpers over page-local custom renderers so future operator mutation surfaces inherit the same review model by default.

## Correction Items

- Keep the Helm devstack bootstrap ordering under regression. Fresh installs exposed multiple startup bugs that only show up from an empty kind cluster:
  - `helm/devstack/create.sh` called `prepare_devstack_secret_values` before the function was defined.
  - `helm/templates/poundcake-startup-jobs.yaml` rendered `poundcake-mariadb-users` and `poundcake-mariadb-grants` without a YAML document separator, so the migrator/grant sequence was not actually split.
  - The chart needed an explicit schema bootstrap hook before grant reconciliation on a fresh database.
- Keep MariaDB bootstrap compatibility under regression:
  - `adapter_credentials.allow_public_read` must use a MariaDB-safe boolean server default.
  - Worker-scoped `service_identity_credentials_*` views must exist before worker-reader grants are applied.
- Keep startup bootstrap scripts import-complete:
  - `api/services/plugin_bootstrap.py` must keep `Path` imported because the adapter-credential bootstrap marks the plugin-bootstrap ready file during Helm startup.
- Treat local live e2e port-forwarding as an environment-sensitive path. The shell helper now records port-forward logs, but managed/sandboxed runners may still block `kubectl port-forward` with `operation not permitted`; when that happens, rerun the live shell suites outside the sandbox rather than treating it as a product regression.
