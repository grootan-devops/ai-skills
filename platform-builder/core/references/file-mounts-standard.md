# File Mount Standard

**Platform-independent.** How an application gets a *configuration file* at runtime — an Nginx
config, `application.properties`, `appsettings.json`, a certificate, a `my.cnf`.

Companion to [`helm-chart-standard.md`](./helm-chart-standard.md), which covers environment
variables. This page covers files.

> `assets/nginx-default.conf` in this skill is a **placeholder showing the shape** of a frontend
> Nginx config. It is not the delivery mechanism. The real config is declared in `values.yaml`
> under `mounts:` and rendered by the chart.

---

## 1. The rule

**Any file the application reads at runtime is declared in `values.yaml` under `mounts:`.**

Not baked into the image, and not written as a hand-rolled ConfigMap template in
`chart/templates/`. Three reasons, in order of how often they bite:

1. **A baked-in file cannot change per environment.** Staging and production need different
   upstreams, origins, pool sizes. Baking means rebuilding the image to change a config value —
   and now the artefact you tested is not the artefact you shipped.
2. **A hand-written template bypasses the library.** `tpl-library` already generates the ConfigMap or
   Secret, the volume, and the volumeMount, wired to the right container. A bespoke template
   duplicates that and drifts from it.
3. **The choice between ConfigMap and Secret becomes invisible.** Declared under `mounts:`, it
   is one key, reviewable in a diff.

## 2. ConfigMap or Secret — decide on the *content*

The top-level key **is** the decision:

| Use | When the file content… |
| --- | --- |
| `mounts.configmap` | is safe in plaintext in git and readable by anyone with cluster read — routing rules, log format, feature flags, tuning |
| `mounts.secret` | contains or derives a credential, private key, certificate, token, or connection string with a password |

Judge the **content**, never the filename. The trap is a file whose name sounds innocuous:

- `application.properties` holding `spring.datasource.password=...` → **Secret**
- `nginx.conf` with only routing and headers → **ConfigMap**
- `nginx.conf` embedding a `proxy_set_header Authorization "Bearer ..."` → **Secret**
- `config.json` with an API endpoint → **ConfigMap**; the same file with the API *key* → **Secret**

A mixed file is a design smell, not a classification problem: split it, so the ConfigMap half
stays reviewable and only the genuinely sensitive half becomes a Secret.

> **Secrets are base64, not encrypted.** Choosing `mounts.secret` limits exposure (RBAC,
> `envFrom` boundaries, etcd encryption if enabled) — it does not make the value safe to commit.
> A real credential still belongs in an external secret store, referenced rather than inlined.

## 3. Mount content is Helm-templated

Every `data` value is passed through `tpl … $` by the library
(`helm-tpl-library/templates/_configmaps.tpl`, `_secret.tpl`), so **`{{ … }}` inside file content is
rendered against the release context**. Derive rather than duplicate:

```yaml
mounts:
  configmap:
    nginx-config:
      enabled: true
      mountTo: "main"
      path: /etc/nginx/conf.d
      data:
        default.conf: |
          server {
            listen {{ .Values.container.port }};
            server_name {{ .Values.routes.host }};

            location /api/ {
              proxy_pass http://{{ include "tpl.resource.siblingName" (merge (dict "name" "backend") $) }}:8080;
            }
          }
```

This is the point of the standard. The port, the hostname, and the sibling service name are
already in `values.yaml`; templating them into the file means one place to change and no chance
of the config disagreeing with the Service.

The same applies to Secrets — `stringData` and `data` are both templated:

```yaml
mounts:
  secret:
    app-properties:
      mountTo: "main"
      path: /config
      stringData:
        application.properties: |
          spring.datasource.url=jdbc:postgresql://{{ .Values.database.host }}:5432/{{ .Values.database.name }}
          spring.datasource.username={{ .Values.database.user }}
```

Use `stringData` for plain text; `data` requires values already base64-encoded.

## 4. Schema

The block shape is the library's, and the library documents it — see **File Mounts
(`mounts:`)** under *Chart standards to follow* in `helm-tpl-library/README.md`, read at the
ref this run resolved. Do not restate it here; a copy drifts silently.

One thing to carry into every mount you author, because getting it wrong is invisible:

> `mountTo` **defaults to `"both"`**, not `"main"`. A mount that omits it is mounted into the
> init containers as well. Set `mountTo: "main"` explicitly whenever a file is for the
> application only — a database credential reaching an init container that runs a migration
> as a different identity is the failure this prevents.

## 5. Assess this during onboarding

**Ask explicitly whether the application needs a file mount.** It is easy to miss, because a
missing config file usually surfaces as a runtime crash loop rather than a failed build.

Signals, by stack:

| Stack | Look for |
| --- | --- |
| Frontend SPA | `nginx.conf`, `default.conf`, `httpd.conf` |
| Java / Spring | `application.properties`, `application.yml`, `logback.xml` |
| .NET | `appsettings.json`, `appsettings.<env>.json` |
| Python | `gunicorn.conf.py`, `uwsgi.ini`, `logging.conf` |
| Node | `ecosystem.config.js`, custom `config/*.json` |
| Any | `*.pem`, `*.crt`, `*.key`, `my.cnf`, `redis.conf`, `otel-collector.yaml` |

Also check the Dockerfile for a `COPY` of a config file into the image — that is the
anti-pattern in §1.1, and migrating it to a mount is usually the right call during `update`.

Confirm each with the user in the Phase 2 table: **filename, mount path, ConfigMap or Secret,
and which values it should derive.** Do not infer sensitivity silently.

## 6. Anti-patterns

| Anti-pattern | Why it fails |
| --- | --- |
| `COPY nginx.conf` in the Dockerfile | Cannot vary per environment; a config change needs an image rebuild |
| A hand-written ConfigMap in `chart/templates/` | Duplicates what the library generates; drifts from it |
| A credential in `mounts.configmap` | Readable by anyone with namespace read |
| Hardcoding a port or sibling hostname in file content | Silently disagrees with `values.yaml` the moment either changes |
| One file mixing routing and credentials | Forces the whole file into a Secret and makes the safe half unreviewable |
| `data:` on a Secret with plain text | `data` expects base64; use `stringData` |
