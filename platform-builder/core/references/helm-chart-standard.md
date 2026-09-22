# Enterprise Helm Chart Architecture & `helm-tpl-library` Standard

This document defines the architectural standards for authoring, structuring, and maintaining production Helm charts using the enterprise shared template library (`helm-tpl-library`; its source and pinned ref are declared in [`core/libraries.json`](../libraries.json), never hardcoded here).

---

## 0. What this page owns

Chart **authoring judgement**: how to decide a description, a component name, a container
key, which values to carry, and how to prove the result renders.

It does **not** restate the `tpl-library` contract. The library's `Chart.yaml` stanza, its
`templates/manifest.yaml` entrypoints (`tpl.deployment`, `tpl.job`, `tpl.cronjob`,
`tpl.pvc`, `tpl.servicemonitor`), its values structure and comment law, its sensitive-data
segregation, its sibling-name helper, its `routes:` contract and its `helm-docs` command
are documented in **helm-tpl-library's own `README.md`**, beside the `values.yaml` they
describe. Read that at the resolved version, every run. The section numbers below have gaps
where those topics used to be duplicated here.

---

## 1. Project Purpose & Chart Description

Consumer charts must clearly state the business purpose, runtime architecture, and responsibilities of the application in both `Chart.yaml` and `chart/README.gotmpl`:

1. **Automatic Extraction**:
   - Inspect `package.json` (`description`), `pyproject.toml` (`description`), `pom.xml` (`<description>`), `Cargo.toml`, and the root `README.md`.
2. **Interactive Clarification & Improvement**:
   - If the description cannot be extracted or is generic/trivial (e.g., "TODO", empty, or merely repeats the repo name):
     - **Prompt the user**: Ask for the application's primary role and business domain.
     - **Collaborative Enhancement**: Combine the user's input with detected runtime characteristics (framework, databases, queues, S3 storage) into a concise, professional description.
     - Confirm with the user before generating the chart.
3. **`Chart.yaml`**:
   The confirmed description must be set in the `description:` field:

   ```yaml
   description: Acme website CMS backend service providing headless content management, PostgreSQL persistence, and S3 media storage.
   ```

4. **`chart/README.gotmpl`**:
   Must include the standard headers and an explicit `## Overview & Purpose` section:

   ```gotmpl
   {{ template "chart.header" . }}

   {{ template "chart.badgesSection" . }}

   {{ template "chart.deprecationWarning" . }}

   {{ template "chart.homepageLine" . }}

   {{ template "chart.description" . }}

   ## Overview & Purpose

   <Confirmed project purpose & architecture description>

   ## Architecture & Template Library
   ...
   ```

5. **Documentation Synchronization**:
   Re-render `chart/README.md` with the exact `helm-docs` invocation the library's own
   README specifies — the flags are part of that contract, not a detail to improvise.

---

### 1.1. `.helmignore` Must Not Hide `charts/`

A bare `*.tgz` or a `charts` entry in `.helmignore` hides the resolved dependency archives
from the Helm chart **loader**, so a correct, fully downloaded dependency reports as
missing and every library-chart include fails. Neither error message names `.helmignore`.

Baseline file, the anchored `/*.tgz` form, and the one-step diagnostic:
**`ignore-files-standard.md` §3** — the single source for all three ignore files.
Enforced by `check_helmignore` in `core/scripts/checks_common.py`.

---

## 2. Product, Component & Sub-Component Architecture Standard

To ensure deterministic naming across Kubernetes namespaces, ArgoCD applications, container repositories, and DNS routes:

- **Product Name**: The top-level product prefix in lowercase (e.g. `myapp`). Inferred from git remote, package scopes, or session memory; confirmed with the user in Phase 2; generic placeholders (`myorg`, `myproduct`) are strictly prohibited.
- **Component**: Primary business domain or service name (e.g. `order`, `chat`, `admin`, `cart`, `pii`).
- **Sub-Component**: Functional role within the component. **Strict standard: must be one of `backend`, `frontend`, `worker`, `gateway`**. Generic name `service` is strictly forbidden and flagged as a P1 violation.
  - **Backend**: APIs, REST/gRPC microservices, authentication servers, core engines (`order-service` -> `order-backend`, `pii-service` -> `pii-backend`, `pii-vault` -> `pii-backend`, `auth-service` -> `auth-backend`).
  - **Worker**: Asynchronous event consumers, cron jobs, scrubbers, batch processors, data pipelines (`pii-masker` -> `pii-worker`, `pii-scrubber` -> `pii-worker`, `pii-anonymizer` -> `pii-worker`, `notification-worker` -> `notification-worker`, `analytics-aggregator` -> `analytics-worker`).
  - **Gateway**: Ingress proxies, reverse-proxies, API gateways, BFFs (`auth-gateway` -> `auth-gateway`, `edge-proxy` -> `edge-gateway`, `api-gateway` -> `api-gateway`).
  - **Frontend**: Single-page applications, web dashboards, portals, static Nginx sites (`admin-portal` -> `admin-frontend`, `chat-ui` -> `chat-frontend`, `storefront` -> `store-frontend`).
- **Standard Chart Name**: `{component}-{sub_component}` (e.g. `order-backend`, `chat-frontend`, `admin-backend`, `pii-worker`).
- **Kubernetes Release Name**: `{product}-{component}-{sub_component}` (e.g. `myapp-order-backend`, `myapp-chat-backend`, `myapp-pii-worker`).
- **Release Name Length**: `.Values.global.releaseNameLength` is set to the character length of the `{product}` prefix (e.g., `5` for `myapp`), enabling `tpl.resource.siblingName` to cleanly isolate the product prefix and derive sibling DNS.
- **Container Image Repository**: `{product}/{component}-{sub_component}` (e.g. `myapp/order-backend`), dynamically templated as `"{{ .Values.global.partOf }}/{{ .Values.component }}-{{ .Values.subComponent }}"`.
- **Labeling (`app.kubernetes.io/part-of`)**: `.Values.global.partOf: "{product}"`.

---

### 2.1. Container Naming Standard — the `main` key is reserved

`tpl-library` derives every container name from the **map key**, not from a `name:` field, and
reserves `main`. The rendered-name table and the exact scope of that reservation are library
behaviour and live in helm-tpl-library's `README.md`.

What matters when you are *choosing* a key is why the prefix exists: the `container` label
has to be self-describing in Loki and in cAdvisor metrics without a collector relabel rule.
Query `{container="order-backend-main"}` and you have the service; a fleet-wide
`container="main"` means nothing on its own.

> [!IMPORTANT]
> **`main` names one thing: *the* application process** — the single long-running container
> the chart exists to run. Use it once, as `containers.main`. Anything else is doing a
> different job and must say what that job is.
>
> `tpl-library` enforces this for init containers and for `jobs:` containers, and fails the
> render. It does **not** apply to `cronjobs:`, which reuse the root `containers:` and so
> legitimately run `main` in their pod.

```yaml
containers:
  main:                     # the application itself — the only `main` in this file
    image:
      repository: "{{ .Values.global.partOf }}/{{ .Values.component }}-{{ .Values.subComponent }}"
  log-collector:            # -> order-backend-log-collector
    image:
      repository: "grafana/alloy"
  metrics-exporter:         # -> order-backend-metrics-exporter
    image:
      repository: "prometheuscommunity/postgres-exporter"

initContainers:
  db-migration:             # -> init-order-backend-db-migration   (NOT `main`)
    image:
      repository: "{{ .Values.global.partOf }}/{{ .Values.component }}-migrator"
  wait-for-db:              # -> init-order-backend-wait-for-db
    image:
      repository: "busybox"

jobs:
  migrate:
    containers:
      migrate:              # -> order-backend-migrate              (NOT `main`)
        image:
          repository: "{{ .Values.global.partOf }}/{{ .Values.component }}-migrator"
    initContainers:
      wait-for-db:          # -> init-order-backend-wait-for-db     (NOT `main`)
        image:
          repository: "busybox"
```

**Choosing a container key — derive it, do not look it up.** There is no approved list of
keys. Work out the right one for *this* container from what it actually does in *this*
chart, then check the result against the tests below. The tables further down are worked
examples of the method, not a vocabulary to match against.

Four questions, in order:

1. **What is this container's job in one phrase?** Not what it is, what it *does*. "Ships
   this pod's stdout to Loki." "Pools Postgres connections." "Reloads the app when a
   mounted ConfigMap changes." That phrase is the raw material for the key.
2. **Would the key survive replacing the image?** If the team swaps Alloy for Fluent Bit,
   or Envoy for Linkerd, or Vault Agent for External Secrets, the container's job is
   unchanged — so the key must be too. If your candidate key would have to change, it names
   the vendor, not the role. Rewrite it.
3. **Does it read correctly after the component prefix?** The rendered name is what an
   on-call engineer sees in a Loki stream with no other context.
   `order-backend-connection-pool` is self-explanatory; `order-backend-pgb` and
   `order-backend-sidecar` are not. Say the full rendered name aloud before accepting the key.
4. **Is it distinguishable from its siblings?** Two containers in one pod that both plausibly
   answer to the same key mean the key is too coarse. `metrics-exporter` and
   `business-metrics-exporter`, not `metrics` and `metrics2`.

**Form**: lowercase, hyphenated, a `noun` or `verb-noun` phrase. Prefer the shortest phrase
that still passes question 3 — `proxy` is better than `sidecar-proxy` when there is only one
proxy; `auth-proxy` is right when there are two.

**Always rejected**, whatever the container does: `sidecar`, `helper`, `aux`, `agent` alone,
`container2`, any bare vendor or product name, and any key whose meaning depends on which
container it happens to sit beside. These fail question 3 by construction.

Worked examples — the method applied, not a closed list:

| What it does | Key | Not, and why |
| --- | --- | --- |
| Ships pod logs to a log backend | `log-collector` | `alloy`, `fluentbit` (vendor), `logs` (too coarse), `sidecar` |
| Exposes app metrics for scraping | `metrics-exporter` | `prom` (vendor), `exporter` (of what?), `metrics2` |
| Terminates/routes mesh traffic | `proxy` | `envoy`, `istio-proxy` (vendor) |
| Fetches secrets into a shared volume | `secret-agent` | `vault` (vendor), `agent` (of what?) |
| Pools DB connections | `connection-pool` | `pgbouncer` (vendor), `db` (too coarse) |
| Reloads config on ConfigMap change | `config-reloader` | `reloader` (vendor-ish and coarse) |
| Authenticates requests ahead of the app | `auth-proxy` | `oauth2-proxy` (vendor), `proxy` if a mesh proxy is also present |
| Streams OTLP traces to a collector | `trace-forwarder` | `otel`, `otel-collector` (vendor/product) |
| Periodically snapshots a volume | `backup-agent` | `restic`, `velero` (vendor) |
| Serves a read-through local cache | `cache` | `redis`, `memcached` (vendor) |

If the container you are adding is not in that table — the common case — apply the four
questions. A key you derived and can justify beats one borrowed from a row that only
approximately fits.

**Init container keys** describe a *completed precondition*, not a running process, so they
read as a past-tense outcome rather than a role: `db-migration`, `wait-for-db`,
`fetch-config`, `chown-data`, `seed-fixtures`, `warm-cache`. The same four questions apply —
`wait-for-db` survives swapping the wait image, `busybox` does not.

Prefix init keys numerically (`01-`, `02-`) only when ordering actually matters. `tpl-library`
renders init containers in `sortAlpha` order over the **keys**, not in declaration order,
and Kubernetes runs them in that rendered order — so two init containers with a real
dependency between them need the prefix, and independent ones should not carry a false
implication of sequence.

An explicit `name:` on a container overrides the whole scheme, including the `main`
reservation. Use it only when an external contract fixes the container name (a mesh
injector, a log pipeline you do not control); otherwise leave it empty and let the key drive.

---

## 3. `values.yaml` Authoring

### 3.0. The Replica Law — start from the library's `values.yaml`

> [!IMPORTANT]
> **A consumer `values.yaml` is a replica of `tpl-library`'s `values.yaml`, not a subset of it.**
> Copy the library file at the resolved ref and override the values this application needs.
> Never assemble one from memory of which keys matter.

Every key the library declares stays in the consumer file — **including the ones that are
optional, empty, and unused here** — with its `# --` comment, its `# Ref:`, its
`# @section --`, and its `### Example` block. `additionalConfigmapEnvs`, `extraSecretMounts`,
`resizePolicy`, `hostAliases`, `scheduling.*`, `networkPolicy.*`: all of them, at `{}` or `[]`.

Three reasons, in the order they bite:

1. **An absent key is unreadable.** A reader cannot distinguish "this chart deliberately
   does not use `extraSecretMounts`" from "whoever wrote this did not know it existed".
   Present-and-empty answers the question; absent does not.
2. **`helm-docs` documents what is in the file.** An omitted key produces no table row, so
   the generated `README.md` silently under-describes the chart's own contract.
3. **It is the only thing that survives a library upgrade.** When `tpl-library` adds a key, a
   replica diffs cleanly against the new library file and the addition is obvious. A
   hand-picked subset diffs against nothing.

**Where the copy stops:** a key the library marks `# @default -- Check values.yaml` is one
opaque value rendered through `toYaml`. Its sub-keys are yours to shape — `strategy:` with
`type: Recreate` and no `rollingUpdate:` is complete, not incomplete. Likewise
`containers.main` is an example name: the contract applies to whatever containers you declare.

Enforced by `check_values_parity` in `core/scripts/checks_common.py`, measured against the
`helm-tpl-library` copy the run resolved (§0.0) rather than a frozen expectation.

### 3.0c. Credentials sit under an `auth:` sub-map

Every domain group that carries a credential nests it:

```yaml
database:
  postgresql:
    host: ""
    port: 5432
    auth:
      username: ""
      password: ""

smtp:
  host: ""
  port: 587
  from: ""
  auth:
    username: ""      # the authenticating account
    password: ""
```

`auth:` is the one place a reviewer looks to answer "what does this chart hold that is
secret?", and it keeps the split between `configmapEnvs` and `secretEnvs` mechanical:
everything under `auth:` is a `secretEnvs` candidate, everything beside it is not.

**The authenticating account is not the display value.** `smtp.auth.username` is the mailbox
that authenticates; `smtp.from` is the envelope sender. They are frequently the same address
and are not the same field — deriving `SMTP_USER` from `smtp.from` breaks the moment a relay
account differs from the sender, which is the normal case for a shared or no-reply sender.
The same distinction applies to a database owner versus the connecting role.

### 3.0d. Application values: top level, before the workload plumbing, documented per leaf

The library's `values.yaml` is workload plumbing only — it declares no key for application
configuration. Whatever this application needs is yours to add, under three rules.

**Top level, not wrapped.** A domain group sits at the root of `values.yaml`, exactly as
`database:` and `smtp:` do in §3.0c. Do **not** invent a `app:` / `config:` / `settings:`
envelope:

```yaml
# right                          # wrong
nextauth:                        app:
  url: ""                          nextauth:
keycloak:                            url: ""
  issuer: ""                       keycloak:
                                     issuer: ""
```

The wrapper buys nothing and costs on every reference: `.Values.app.nextauth.url` instead of
`.Values.nextauth.url`, in every `configmapEnvs` line and every `helm --set` a deployer
types. The one thing to check is collision: the name must not be one the library already
declares. **Read the root keys off the resolved library's `values.yaml`** rather than a list
kept here — a copy is correct until the library adds a key, and then it is silently wrong.

A domain noun usually clears it, but do not assume: `service`, `metrics`, `routes`,
`persistence` and `pod` are all ordinary application nouns *and* library root keys. Check.

**Placed after `strategy:` and before `restartPolicy:`.** Application configuration is what a
deployer edits; the workload plumbing is what they inherit and rarely touch. Appending the
application block to the end of the file buries the only section anyone opens the file for
behind 700 lines they do not. The library's own key order is otherwise preserved, so the
replica still diffs cleanly against the library (§3.0, reason 3).

**Every leaf carries its own `# --` and `# @section --`.** A consumer key whose sub-keys are
each set individually is not one opaque value, so it does not get the parent-level
`# @default -- Check values.yaml` treatment:

```yaml
# right
apps:
  operation:
    # -- Host of the Operation console, substituted into the bundle at container start.
    # @section -- Application Settings
    host: ""
    # -- Path the launcher appends to the Operation host.
    # @section -- Application Settings
    path: ""

# wrong -- one comment on the parent, leaves undocumented
# -- Sibling application hosts and redirect paths.
# @section -- Application Settings
# @default -- Check values.yaml
apps:
  operation:
    host: ""
    path: ""
```

`# @default -- Check values.yaml` belongs to a library key rendered opaquely through
`toYaml`, where the sub-keys are the caller's shape (§3.0, "Where the copy stops"). Using it
on a consumer map collapses every leaf into a single `helm-docs` row, so the generated
`README.md` never names `apps.operation.host` — the same under-description §3.0 reason 2
rejects for omitted keys. If the map is genuinely free-form (arbitrary keys a deployer
invents), `@default` is correct; if the keys are fixed and you wrote them, document each one.

---

## 6. Mandatory Pre-Flight Verification via `helm template`

To guarantee zero template compilation errors, missing variable references, or nil pointer exceptions:

```bash
helm template <chart_name> chart/
```

Every scaffolded or updated Helm chart must successfully render all Kubernetes resources (Deployment, Service, ConfigMap, Secret, Routes [Ingress / HTTPRoute], PDB, and HPA) before it can be committed or released.
