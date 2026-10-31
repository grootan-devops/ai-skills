# Workflow 03: Promote an app stack from sandbox

> Moves a product or app stack that has outgrown its POC in `sandbox` into
> its own `<product>/<env>` branch(es) and namespace(s), migrates its data, then
> removes it from `sandbox`. Read
> [discovery.md](../references/discovery.md) and
> [kubernetes-rules.md](../../../core/references/kubernetes-rules.md) first.

**Use when:** a sandbox project needs dedicated environments (dev, qa, prod) or has passed the POC stage.

**Approval rule:** this workflow changes live data. Every remote or cluster action needs an explicit yes for that action:

- pushes
- root app creation
- syncs
- starting migration pods
- each copy step
- commenting out and undeploying the sandbox stack

Never delete the source data. Leave it in place until the user confirms the new environment works.

---

## Step 1: Identify the stack in sandbox

1. Ask for the **GitOps repo URL** and the **project or app name** as it appears in sandbox (e.g. `status-poc`). Pause until you have both.
2. **Validate the repo** ([discovery.md](../references/discovery.md) §10). Stop on exit 2 or 3. On exit 1, show the problems and go on only if the user says so. The `sandbox` branch must be one of the environments it lists.
3. Collect every piece that belongs to it on the `sandbox` branch:
   - **Helm apps:** `apps.<project>…` entries in `values.yaml` and their `values/` files.
   - **Manifests:** `extras/manifests/<project>/**` (sandbox nests by project).
   - **Shared pieces it uses:**
     - sandbox-level `minio`, `redis`, `rustfs` and their buckets or DB indexes
     - ConfigMaps or Secrets in other directories
     - routes and hostnames (`*.apps.example.com`)
4. Map the dependency graph from env vars, configs and secrets: which DB, cache, object store, OAuth client, external API each component uses, and **where each one lives**.
5. Confirm the inventory with the user: components, dependencies, hostnames and data stores.

Live state (read-only) shows what is actually deployed:

```bash
argocd app list -o name | grep -- '-sandbox-.*<project>'
kubectl -n sandbox get deploy,sts,cronjob,svc,pvc,secret,cm -l app.kubernetes.io/instance=<…>
```

## Step 2: Plan the target environments

1. Ask which environments are needed now: dev only, or dev, qa and prod.
2. For each one, the target is `<product>/<env>` with namespace `<product>-<env>`. The product name may differ from the sandbox project name; confirm it.
3. **Decide what is centralized and what is namespaced:**

   | Kind | Default | Examples |
   | --- | --- | --- |
   | Shared platform service | **Centralize**: point at the existing shared instance, new DB or user per env | Postgres (CNPG `postgres-cluster-rw.postgres`), MariaDB, Mongo, Airflow, Airbyte, Keycloak realm |
   | Per-env stateful component | **Namespace level**: deploy into `<product>-<env>` | minio, rustfs, redis or valkey, app-specific caches |
   | Stateless services | Namespace level | backend, frontend, workers, MCP servers |

4. Prefer **converting manifests to the product's Helm chart** if one exists by now (workflow 01 steps 3–4). Otherwise keep manifests, moved to `extras/manifests/<component>/` (no project level).

## Step 3: Create the new branch(es)

For each target environment, run [02-new-env-and-app.md](02-new-env-and-app.md) **steps 1–4**: inputs, preflight, bootstrap and namespace. Don't sync any app yet.

## Step 4: Port the apps

Follow [01-add-app-existing-env.md](01-add-app-existing-env.md) **steps 6–10** for each component, using the sandbox definitions as input:

- **Re-point hostnames** to the env pattern, e.g. `<app>-<env>.dev.example.com` or the product domain. Agree the new hostnames with the user.
- **Re-point dependencies** to the centralized service (new DB name or user per env) or to the new namespaced instance.
- **Fix every rule violation** the sandbox version had ([kubernetes-rules.md](../../../core/references/kubernetes-rules.md)). Promotion is the moment to make it compliant.
- **Leave data stores empty** in the first sync.
- **The team's own apps** come from their application projects. Run the readiness gate for each one ([app-readiness.md](../../../core/references/app-readiness.md)), at the channel of the target environment, and add them with `app_add.py --app-facts`. A sandbox POC with no project behind it stays as it is, or is onboarded first with `platform onboard`.

Render and audit locally ([kubernetes-rules.md](../../../core/references/kubernetes-rules.md) §4).

## Step 5: Data migration inventory

Fill this table with the user:

| Store | Source (sandbox) | Target | Action |
| --- | --- | --- | --- |
| Centralized DB, already shared | e.g. `postgres-cluster-rw.postgres` DB `<x>` | same server | **None.** Optionally create a new DB or user per env and copy (step 7). |
| Self-hosted DB in sandbox ns | e.g. `sandbox/<project>-postgres` | centralized DB **or** `<product>-<env>` | dump → restore |
| redis / valkey | sandbox redis, DB index `<n>` | new redis in `<product>-<env>` | `redis-cli --rdb` or key copy, only if the data matters |
| minio / rustfs buckets | sandbox minio `<bucket>` | new tenant in `<product>-<env>` | `mc mirror` |
| PVC files | `sandbox/<pvc>` | new PVC | copy pod (tar stream) |
| Secrets / OAuth clients | sandbox values / `secret.yaml` | new branch | re-create; rotate if the env is prod |

**Rule:** anything per-environment (minio, rustfs, redis) moves to the new namespace. Anything already centralized needs no data move.

## Step 6: Deploy the empty stack (approval)

1. Commit and push the new branch, then hand it over for review ([review-gate.md](../references/review-gate.md), part B): print the branch and file links with `review_links.py`, ask the dev to review, push fixes if needed, and approve here. **Stop until they approve**, then fetch the branch and re-render and re-audit if it changed.
2. Create the root Application.
3. Sync the root and extras first, then the data stores (minio, redis), then the stateless apps. Use the order in [02 step 6](02-new-env-and-app.md).
4. **Scale app writers to 0** in the new env (or keep them unsynced) until the data is copied, so nothing writes to the empty stores.
5. Wait until the data stores are Healthy.

## Step 7: Migrate the data (approval per store)

Use the credentials and endpoints from the sandbox and new-env values or secrets.

1. **Check access first:**
   - `kubectl auth can-i create pods -n <target-ns>`
   - `kubectl auth can-i get secrets -n sandbox`

   If either is missing, print the exact steps for the user to run instead.
2. **Freeze writes on the source** where the user agrees (scale sandbox writers to 0, or announce a window).
3. **Run a temporary migration pod** in the target namespace:
   - public client image pinned to a tag (`postgres:<v>`, `mongo:<v>`, `minio/mc:<tag>`, `redis:<v>`)
   - compliant security context (non-root, drop ALL, `RuntimeDefault`)
   - memory requests equal to limits
   - an `emptyDir` with `sizeLimit` for staging dumps
   - `restartPolicy: Never`

   Apply it with `kubectl apply -f` from the scratch directory, never committed. Examples:

   ```bash
   # Postgres
   pg_dump -h <src-host> -U <user> -d <db> -Fc -f /work/db.dump
   pg_restore -h <dst-host> -U <user> -d <db> --no-owner --no-privileges /work/db.dump
   # MinIO / S3-compatible
   mc alias set src <src-endpoint> <ak> <sk>; mc alias set dst <dst-endpoint> <ak> <sk>
   mc mirror --preserve src/<bucket> dst/<bucket>
   # Mongo
   mongodump --uri "<src-uri>" --archive=/work/m.gz --gzip
   mongorestore --uri "<dst-uri>" --archive=/work/m.gz --gzip
   ```

4. **Verify each store** before moving on: row counts per table, object counts and sizes per bucket, key counts, and an app-level smoke check. Show the numbers to the user.
5. **Delete the migration pod** (and any temporary secret) once that store is verified.

## Step 8: Cut over (approval)

1. Scale the new env's writers back up, or sync them. Wait until they are Healthy.
2. Switch DNS or hostnames if the old sandbox hostnames must keep working: either add them to the new route temporarily, or tell users the new URLs.
3. The user verifies the app end to end in the new env.

## Step 9: Remove the stack from sandbox (approval)

Only after the user confirms the new env works:

1. On the `sandbox` branch, **comment out** every piece identified in step 1. Keep the files for history and rollback:
   - Helm apps: comment out the `apps.<…>` entries in `values.yaml`, and the matching values files.
   - Manifests: comment out every line of each file in `extras/manifests/<project>/**`. A fully commented file renders nothing.
   - Add a header comment to each: `# Promoted to <product>/<env> on <date>`.
2. Render `sandbox` locally and confirm only the stack's Applications disappeared.
3. **Commit and push** `sandbox`, then sync the sandbox root, its extras parent and the affected apps. With pruning, Argo CD undeploys them.
4. **Leftover data:** PVCs from StatefulSet `volumeClaimTemplates`, and buckets on shared sandbox minio, are **not** deleted automatically. List them for the user. They decide when to delete; this workflow never deletes data.

## Step 10: Wire the CI deploy job

Follow [05-deploy-job.md](05-deploy-job.md) steps 1–6 for every app from an application project, in each new environment. Everything it needs is
already known from this workflow and the readiness gate. Ask the user only one thing per
environment: **automatic** (after a build of which branch) or **manual**.

## Step 11: Report

Once every app is Healthy and Synced, open the report with what the developer keeps
([review-gate.md](../references/review-gate.md), "After it is live"):

```bash
python3 gitops/argocd/scripts/app_report.py <env checkout> --gitops-url <url> --branch <product>/<env> \
  --argocd-url <argocd-url> --app <key> --extras-folder <dir>
```

Give its output as it is: each app's URL, its Argo CD Applications with their links, its values
file (or manifests folder) and the line that declares it, the root Application, and the
`argocd app sync` commands to run later. Run it once per new branch.

Then tell the user:

- the new branches and root apps
- what was migrated, with verification numbers
- what was centralized
- the sandbox entries commented out
- leftover data awaiting their decision
- any exceptions recorded
- each deploy job: automatic or manual, the CI file changed, and the variables and secrets still to set
