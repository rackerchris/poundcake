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
  StackStorm/operator-review fallback paths across the Genestack catalog.

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
  fallback workflows so StackStorm remains the multi-step/workflow adapter, not
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

- Continue promoting alert families from generic operator-review/StackStorm
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
- Split bootstrap privileges so external plugin metadata/bootstrap code does not
  run with both migrator database authority and the plugin credential encryption
  key. Keep schema migration, internal HMAC bootstrap, plugin metadata
  registration, and adapter credential bootstrap under distinct principals.
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

- Promote managed Genestack recipes from generic operator-review actions to
  first-class adapter actions where the adapter can make a safe, scoped decision.
  Keep the current generic recipe as the safety net, but track each alert family
  as `native_action`, `stackstorm_workflow`, `domain_evidence_only`,
  `operator_review`, or `adapter_extension_needed`.
- Add richer native remediation actions for alert families that still use
  conservative operator-review routing after Prometheus, Alertmanager, GitHub,
  and K8s evidence is gathered.
- Promote remaining Kubernetes gaps into the K8s adapter before routing them
  through StackStorm: certificate/Secret inspection, richer node triage,
  PVC/PV storage triage, Service endpoint triage, PDB/HPA triage, and safe
  ConfigMap metadata diagnostics are available as read-only evidence; remaining
  work is additional domain-specific mappings where alert labels identify safe
  exact targets.
- Add domain adapters only when repeated StackStorm/operator-review workflows
  become structured enough to justify first-class capabilities. Likely
  candidates are RabbitMQ, OpenStack, MariaDB, and node/hardware diagnostics.
- Keep StackStorm for pack-owned multi-step workflows and conservative
  operator-review workflows. If a workflow is mostly a direct Kubernetes or
  Prometheus operation, prefer the native adapter.
- Let Bakery own communication lifecycle policy. PoundCake should send stable
  alert/order correlation context and communication intent; Bakery should decide
  whether to create, update/comment, reopen, or close provider records.

## Auth Provider Calls

- Document auth/OIDC/device login routes as an explicit non-Expediter exception
  or introduce a future auth-provider plugin boundary. Current device/OIDC
  flows call Auth0/Azure endpoints directly from `auth_service`, which is
  appropriate for login bootstrap but is still an external HTTP path.
- Keep `/api/v1/auth/device/start` and `/api/v1/auth/device/poll` scoped to
  operator/user authentication only; do not reuse them for service execution or
  provider automation.

## Security Hardening

### Rate Limiting

- Add rate limiting (`slowapi` or middleware) to auth endpoints: `POST /api/v1/auth/login`, `POST /api/v1/auth/device/poll`, and `GET /api/v1/auth/oidc/callback`. These endpoints are susceptible to credential brute force and OIDC state exhaustion without request throttling.

### Credential Encryption Key Rotation

- Implement credential encryption key rotation workflows using Fernet's `MultiFernet` support. Add an API endpoint that accepts a new encryption key, wraps the old key for decryption of existing values, and rotates stored ciphertexts. Plugin credentials (`POUNDCAKE_PLUGIN_CREDENTIAL_ENCRYPTION_KEY`) and service identity credentials (`POUNDCAKE_SERVICE_IDENTITY_CREDENTIAL_ENCRYPTION_KEY`) should remain in separate key domains with independent rotation schedules.

### Bootstrap Privilege Separation

- Split startup bootstrap privileges around the current Dishwasher boundary so external plugin metadata/bootstrap code does not run with both migrator database authority and the plugin credential encryption key. Schema migration, plugin-registry bootstrap, internal HMAC bootstrap, and adapter credential bootstrap should each use distinct database principals, while Dishwasher remains the only authority for manifest-driven ingredient, recipe, scheduled-task, and communication-route sync.
