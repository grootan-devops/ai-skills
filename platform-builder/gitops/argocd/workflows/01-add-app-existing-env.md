# Workflow 01: Add an app to an existing environment

> Deploys one service (and anything it needs) into a GitOps branch that
> already exists and already has a root Application. Read
> [discovery.md](../references/discovery.md) and
> [kubernetes-rules.md](../../../core/references/kubernetes-rules.md) first.

**Use when:** the target product and environment branch exists, or the app is a POC that belongs in `sandbox`.

**Hand-offs:**

- No branch fits and the user wants a new one: [02-new-env-and-app.md](02-new-env-and-app.md).
- Moving an existing sandbox stack out: [03-promote-from-sandbox.md](03-promote-from-sandbox.md).

**Approval rule:** everything up to step 10 is local and read-only towards the cluster. Every remote action (push, opening or merging the MR/PR, sync, `kubectl` writes) needs an explicit yes from the user, for that specific action. Nothing new is synced before a developer approves it ([review-gate.md](../references/review-gate.md)).

---

## Step 1: Get the GitOps repo

1. Ask for the **GitOps repo URL** if it isn't given. Pause until you have it.
2. **Validate it before anything else** ([discovery.md](../references/discovery.md) §10). Go on only on exit 0, or on exit 1 once the user has seen the problems and said to continue. Stop on exit 2 or 3.
3. Clone it into a scratch directory. The check already listed its branches.
4. For each candidate branch, read `values.yaml` (`git show <branch>:values.yaml`) and `Chart.yaml`. Record:
   - the chart name and library version
   - `environment`, `namespace` and `project`
   - the apps it already runs

## Step 2: Get the application source

Ask for **one** of the following, and accept several if the user has them:

| Source | What to collect |
| --- | --- |
| A project onboarded with `platform onboard` (preferred for the team's own apps) | the project; go to step 3 |
| Helm chart URL (repo or OCI) | chart name, available versions (`helm search repo` or `helm show chart`), default values |
| GitHub or GitLab project, or a CI URL | the project and branch; go to step 3 |
| Docs or a manifest URL ("how to install on Kubernetes") | the manifests or install steps, required images and versions |
| Just an app name ("deploy uptime-kuma") | search for an official chart first, then official manifests |

## Step 3: Trace a repo or CI URL

1. Resolve the URL to its project and default or release branch. A pipeline or job URL gives you the project and the ref.
2. **Run the readiness gate first** ([app-readiness.md](../../../core/references/app-readiness.md)):
   - **Exit 0:** the chart, its published version and the image come from the facts file. Skip to step 4. In step 6b, add the app with `app_add.py --app-facts`.
   - **Exit 3:** the project isn't published yet. Follow the gate: offer to run its pipeline, and continue only once it's published.
   - **Exit 2:** the project isn't onboarded. Offer `platform onboard`, or trace it by hand from item 3 on.
3. **Look for a Helm chart in the repo:** `chart/`, `charts/<name>/`, `helm/`, `deploy/helm/`, or any `Chart.yaml`.
4. **If a chart exists, find the chart-publish job** in `.gitlab-ci.yml`, `.github/workflows/*` or the included CI-library job names. From it, get:
   - the chart registry (e.g. a GitLab package registry `https://<host>/api/v4/projects/<id>/packages/helm/<channel>`, or an OCI repo)
   - the published chart name
   - the latest published version for the right channel (`dev` → `rc` versions, `stable` → releases)
5. **Check whether the chart depends on the `helm-tpl-library`** (in `Chart.yaml` dependencies). If it does, it is a **private library chart**.
6. **If there is no chart,** collect what's needed to write manifests:
   - Dockerfile: `EXPOSE`, `USER`, `HEALTHCHECK`, `ENTRYPOINT`
   - docker-compose: services, ports, volumes, env, depends_on
   - the image registry path and tag scheme from the CI build job
   - health endpoints from the code (routes like `/health`, `/ready`)
   - required env vars and secrets, from `.env.example`, config loaders or the README

## Step 4: Classify the app

| Class | Signal | Consequence |
| --- | --- | --- |
| **Public or third-party** | Upstream chart (postgres, kyverno, vendor or paid product) | Helm with plugin `kustomized-helm` |
| **Private, library chart** | Chart depends on `helm-tpl-library` | Helm, no plugin, minimal values |
| **Private, no chart** | Only code, Dockerfile or compose | Raw manifests in `extras/manifests/<app>/` |

**Helm always wins over manifests** when both exist.

## Step 5: Choose the branch

1. Prefer the branch that already hosts this **product and environment** (`<product>/<env>`).
2. Otherwise propose `sandbox` for POCs or shared tools.
3. **Show the choice and ask the user to confirm.** Offer the alternatives:
   - a different existing branch
   - a **new branch**: go to [02-new-env-and-app.md](02-new-env-and-app.md)
   - if `sandbox` doesn't exist either, ask whether to create one, or a new branch

## Step 6: Design the deployment

Prepare everything locally on a working branch of the clone.

Write every new values file and manifest in the layout the branch's existing files already use:
indent, lists indented under their key or not, blank lines between top-level keys and between
nested blocks, and the quote character. `app_add.py` does this for the values file it scaffolds,
and prints which files it followed. For a file you write yourself, print the layout with
`python3 gitops/argocd/scripts/yaml_style.py <two or three sibling files>` and follow it. When
you edit an existing file, keep its own layout.

### 6a. Public chart

1. Choose the chart version. Use the latest stable release unless the user names one. Confirm it.
2. Write `values/<key>.yaml` (or `values/<group>/<child>.yaml`) from **vanilla** `helm show values`, then apply only the minimum edits from [kubernetes-rules.md](../../../core/references/kubernetes-rules.md) §2.
3. Add the `apps` entry to `values.yaml`:
   - keys in schema order: `enabled, namespace, releaseName, chart, plugin, ignoreDifferences, sync`
   - `plugin: { name: kustomized-helm, kustomize: { patches: [...] } }`, with patches only for what the chart can't set
   - keep apps in alphabetical order
4. Render the chart **with** the values and patches, and audit it ([kubernetes-rules.md](../../../core/references/kubernetes-rules.md) §4).
5. **Missing objects:** add a PDB, HTTPRoute or Certificate the chart can't create to `extras/manifests/<app>/`.

### 6b. Private library chart

1. Read two or three sibling apps on the target branch, and the same app on other branches if it exists there. Copy only the **shape** of their values.
2. Write a **minimal** values file:
   - `component` / `subComponent`
   - image repository (and tag if not chart-driven)
   - `replicas`, resources
   - `database.*`, `oauth` / `keycloak`, `routes`, persistence
   - only the env vars the app needs
3. Add the `apps` entry: no plugin, keys in schema order, alphabetical position. Use [`app_add.py`](../scripts/app_add.py) ([service-bootstrap.md](../references/service-bootstrap.md)). With a facts file from step 3, pass `--app-facts`: it fills the chart source and the main image, so neither is asked.
4. Render and audit against every rule except resource sizes. If anything fails, **stop and raise it**:
   - list the failing rules
   - ask whether to fix it in the chart repo, or in this GitOps branch (the same branch, or a new branch plus a PR)
   - do not proceed until the user decides

### 6c. Manifests

1. Create `extras/manifests/<app>/` (sandbox: `extras/manifests/<project>/<component>/`).
2. Write one file per kind: `deployment.yaml` or `statefulset.yaml`, `service.yaml`, `pdb.yaml`, `httproute.yaml` or `ingress.yaml`, plus `configmap.yaml`, `secret.yaml`, `pvc.yaml` and `serviceaccount.yaml` as needed.
3. Make each one compliant as you write it ([kubernetes-rules.md](../../../core/references/kubernetes-rules.md) §2, "Raw manifests"), including all three probes.

### 6d. Dependencies

For each dependency the app needs (DB, cache, object store, queue):

- **Prefer a centralized service** that already exists: Postgres `postgres-cluster-rw.postgres`, MariaDB, Mongo, Airbyte.
  - Plan a new database and user for this app.
  - Database and user creation runs in step 11 with approval, or the user does it.
- **Per-namespace services** (redis, minio, rustfs) are added as their own apps on the branch. Use the same chart and pattern as siblings, e.g. the `redis` chart from `ot-container-kit` or the `tenant` chart from MinIO.

## Step 7: Networking and storage

Ask the user, offering defaults discovered from siblings ([discovery.md](../references/discovery.md) §6):

1. **Exposure:** none, internal (private gateway or ingress), or public.
2. **Route kind:** HTTPRoute (Gateway API) or Ingress. Use whatever the branch already uses.
3. **Hostname:** follow the branch pattern, e.g.
   - `<app>-<env>.dev.example.com` or `<app>.dev.example.com` for dev
   - `<project>-<component>.apps.example.com` for a public POC
4. **TLS secret / listener:** the wildcard secret or `sectionName` matching the hostname's domain.
5. **Persistence:** whether it's needed, the storage class (the siblings') and the access mode. The size starts at **1Gi** unless the user says otherwise.

## Step 8: Secrets and configuration

- Collect only the secrets the app actually needs (DB password, OAuth client secret, API keys). Ask the user for values, or generate random ones for brand-new databases.
- Put them where the branch already puts them:
  - private library chart: the values file
  - manifests: `secret.yaml`

  Dev repos may carry plaintext secrets.

## Step 9: Namespace

Skip this if the app goes into an existing namespace that already has the labels.

1. Check read-only: `kubectl get ns <ns> --show-labels`.
2. If the namespace is missing or a label is missing, print the admin commands ([discovery.md](../references/discovery.md) §7) and **wait** for the user to confirm they've been applied.

## Step 10: Validate locally

1. Run `helm dependency build` and `helm template` for `.` and `./extras` (both must succeed).
2. Confirm the new Application names, namespaces and values paths in the rendered output.
3. Run the full audit ([kubernetes-rules.md](../../../core/references/kubernetes-rules.md) §4), including a non-root smoke test for any image whose UID changed.
4. **Show the user:**
   - the diff
   - the new Application names
   - any open exceptions, with proposed README rows

## Step 11: Review and ship (each item needs approval)

New apps and extras folders go live only after a developer has reviewed them on an MR/PR
([review-gate.md](../references/review-gate.md), part A). Changes to apps that already run,
in the same request, are listed separately and take the usual path in item 5.

1. **Review branch and MR/PR:** on a yes, commit the new apps on `review-<product>-<env>-<app>`, push it, and open the MR/PR into the environment branch. Use a short message that names the app and branch.
2. **Hand over:** print the MR/PR link and the file links with `review_links.py`, and ask the dev to review, push changes if needed, and approve here. **Stop until they approve.**
3. **On approval:** fetch the review branch, re-render and re-audit if it changed, then merge the MR/PR.
4. **Database or user bootstrap** on a shared database, if planned:
   - check access first: `kubectl auth can-i create pods -n <db-ns>`, `argocd account can-i sync applications '*'`
   - run it from a temporary pod using a public client image
   - if access is missing, print the steps for the user to run instead
5. **Changes to running apps**, if any: show their diff, then commit and push them to the environment branch on a yes.
6. **Sync** in the order of [discovery.md](../references/discovery.md) §5: the root, the extras parent, the new extras directories, then the new Helm app. List the exact Application names `helm template` rendered on the merged branch:

   ```bash
   argocd app sync argo-cd/<root>
   argocd app sync argo-cd/<extras parent>
   argocd app sync argo-cd/<new extras directory app>
   argocd app sync argo-cd/<new helm app>
   ```

7. **Watch** until the apps are Healthy and Synced, then check the policy reports for the new objects ([kubernetes-rules.md](../../../core/references/kubernetes-rules.md) §4, item 8).

## Step 12: Wire the CI deploy job

Follow [05-deploy-job.md](05-deploy-job.md) steps 1–6 for this app. Everything it needs is
already known from this workflow and the readiness gate. Ask the user only one thing per
environment: **automatic** (after a build of which branch) or **manual**.

## Step 13: Report

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

- the MR/PR and the merge commit
- what was bootstrapped (namespace, DB)
- any exceptions recorded
- what is left for them to do
- the deploy job: automatic or manual, the CI file changed, and the variables and secrets still to set
