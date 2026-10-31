# Workflow 04: Clone an environment (same product, new env)

> Creates a new environment of an existing product from one of its
> branches, e.g. `oneuptime/dev` → `oneuptime/qa`. It rewrites everything
> environment-specific and bootstraps the new environment's databases when permissions
> allow. Read [discovery.md](../references/discovery.md) and
> [kubernetes-rules.md](../../../core/references/kubernetes-rules.md) first.

**Use when:** a product already runs in one environment and needs another (qa, uat, prod…) with the same stack.

**Approval rule:** local preparation needs no approval. Pushes, root app creation, namespace work, database bootstrap, data copy and syncs each need an explicit yes.

---

## Step 1: Source and target

1. Ask for the GitOps repo URL, the **source branch** (e.g. `oneuptime/dev`) and the **target environment** (e.g. `qa`).
2. **Validate the repo** ([discovery.md](../references/discovery.md) §10). Stop on exit 2 or 3. The source branch must be an environment the check lists without problems. If it has problems, show them; it can't be cloned as it is.
3. Derive the target names ([naming.md](../../../core/references/naming.md)): branch `oneuptime/qa`, namespace `oneuptime-qa`, root `edge-east-oneuptime-qa-root`. The chart name stays the same as the source (`edge-east-oneuptime`).
4. Stop if the target branch or root Application already exists.

## Step 2: Inventory the source (read-only)

1. Read the source `values.yaml` apps, `values/**` and `extras/manifests/**`.
2. Read the live state: `argocd app get` on the source root and children, and `kubectl get` in the source namespace.
3. Build a table of **environment-specific values** to rewrite. Search for the env name, namespace, domain and DB names across every file.

   | Category | Typical places | Source → target example |
   | --- | --- | --- |
   | Namespace | `values.yaml` `namespace`, `extras/values.yaml`, manifests `metadata.namespace` | `oneuptime-dev` → `oneuptime-qa` |
   | Branch / env | `branch`, `environment` | `oneuptime/dev`, `dev` → `oneuptime/qa`, `qa` |
   | Hostnames | values `routes`, `global.routes.domain`, `httproute*.yaml`, ingress hosts | `oneuptime-chat-dev.dev.example.com` → agreed qa host |
   | TLS / gateway | `tlsSecretName`, `sectionName`, gateway name | follow the target's domain |
   | Image repos | `image.repository` paths with env suffix | `.../chat/dev` → `.../chat/qa` (confirm it exists) |
   | Chart versions | `chart.version` (often `rc` builds on dev) | ask: pin the same version, or a release version for qa/prod |
   | Databases | `database.postgresql.name`/`user`, redis DB index, mariadb | `oneuptime-chat-dev` → new per-env name |
   | OAuth / Keycloak | client IDs, redirect URIs, realm URL | new clients per env, or the same realm with env URLs |
   | Cross-app URLs | `hub.url`, `internalUrl`, `endpoints.*` | rewrite to target hosts and namespaces |
   | Secrets | values secrets, `secret.yaml` | ask for each; generate new ones for new DB users |
   | Sizing | `replicas`, resources, PVC sizes | ask; prod usually more replicas, same rules |

4. Confirm the rewrite table with the user before writing anything.

## Step 3: Bootstrap the target branch

Run [02-new-env-and-app.md](02-new-env-and-app.md) **steps 1–4**: preflight, scaffold, and namespace via the cluster admin. Use the source branch as the template branch.

## Step 4: Copy and rewrite

1. Copy the source's `apps` block, `values/**` and `extras/manifests/**` into the target branch.
2. Apply the rewrite table from step 2. Re-search afterwards for any leftover source-env strings (env name, namespace, domain, DB names) and show the user every hit.
3. **Versions follow the target's channel.** A source that runs pre-releases (e.g. `dev`) may feed a target that takes only releases (e.g. `prod`). For each app from an application project, run the readiness gate ([app-readiness.md](../../../core/references/app-readiness.md)) at the target's channel, and set the chart version and image tag it finds. Never carry a pre-release into a release-only environment.
4. Re-check every app against [kubernetes-rules.md](../../../core/references/kubernetes-rules.md). The target may be stricter (prod), so fix anything the source let through.
5. Render and audit ([kubernetes-rules.md](../../../core/references/kubernetes-rules.md) §4). Every Application name must end with the new env, e.g. `edge-east-oneuptime-chat-qa`.

## Step 5: Database bootstrap (approval, permission-aware)

1. **Check what you're allowed to do:**

   ```bash
   kubectl auth can-i create pods -n <db-namespace>
   kubectl auth can-i get secrets -n <db-namespace>
   argocd account can-i sync applications '*'
   ```

   If any check fails, write out the exact SQL or commands for the user (or a DBA) to run, and pause until they confirm.
2. **For each centralized DB the stack uses** (CNPG Postgres, MariaDB, Mongo):
   - Read the admin credentials from the existing secret. Never print them.
   - Run a temporary client pod: public image, pinned tag, compliant security context.
   - Create the target databases and users from the rewrite table. Postgres example:

     ```sql
     CREATE ROLE "<user>" LOGIN PASSWORD '<generated>';
     CREATE DATABASE "<db>" OWNER "<user>";
     ```

   - Put the generated credentials where the app expects them: the values file or `secret.yaml` on the target branch.
3. **Namespaced stores** (redis, minio, rustfs) are created empty by their own apps on first sync. Buckets and users come from chart values or an init Job.
4. **Optional seed data:** ask whether qa or prod should start empty (the default) or with a copy of the source data. If they want a copy, use the migration pod procedure from [03-promote-from-sandbox.md](03-promote-from-sandbox.md) step 7, with source env → target env, verification and pod clean-up included.
5. Delete the temporary pods.

## Step 6: Review and ship (each item needs approval)

1. Commit and push the target branch. No root Application watches it yet, so nothing deploys.
2. **Hand over** ([review-gate.md](../references/review-gate.md), part B): print the branch and file links with `review_links.py` (`--changed <source branch>`), ask the dev to review the rewrites, push fixes if needed, and approve here. **Stop until they approve**, then fetch the branch and re-render and re-audit if it changed.
3. Create the root Application: `argocd app create -f root.yaml`, no `--upsert`.
4. Sync in order:
   1. the root
   2. extras, then the data stores
   3. the apps

   ```bash
   argocd app sync argo-cd/edge-east-oneuptime-qa-root
   argocd app sync argo-cd/<extras parent>
   argocd app sync argo-cd/<app> ...       # names from helm template
   ```

5. Wait until every app is Healthy and Synced. Check the policy reports for `<product>-<env>`.
6. The user verifies the environment end to end.

## Step 7: Wire the CI deploy job

Follow [05-deploy-job.md](05-deploy-job.md) steps 1–6 for every app from an application project in the new environment. Everything it needs is
already known from this workflow and the readiness gate. Ask the user only one thing per
environment: **automatic** (after a build of which branch) or **manual**.

## Step 8: Report

Once every app is Healthy and Synced, open the report with what the developer keeps
([review-gate.md](../references/review-gate.md), "After it is live"):

```bash
python3 gitops/argocd/scripts/app_report.py <env checkout> --gitops-url <url> --branch <product>/<env> \
  --argocd-url <argocd-url> --app <key> --extras-folder <dir>
```

Give its output as it is: each app's URL, its Argo CD Applications with their links, its values
file (or manifests folder) and the line that declares it, the root Application, and the
`argocd app sync` commands to run later.

Then tell the user:

- the new branch and root app
- the databases and users created (names only, no secrets)
- whether data was seeded, with verification numbers
- the rewrite table as applied
- leftover source-env strings, if any
- any exceptions recorded
- each deploy job: automatic or manual, the CI file changed, and the variables and secrets still to set
