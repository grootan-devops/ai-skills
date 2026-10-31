# Workflow 02: New environment branch, then add the app

> `platform env add`, then the app. Creates a new `<product>/<env>` branch and its root
> Application, then deploys the app into it with
> [01-add-app-existing-env.md](01-add-app-existing-env.md). The preflight and activation
> checks are in [environment.md](../references/environment.md); the conventions are in
> [discovery.md](../references/discovery.md).

**Use when:**

- workflow 01 step 5 found no fitting branch and the user chose a new one, or
- the user asks for a new product or environment directly.

**Approval rule:** local preparation needs no approval. Pushing the branch, creating the root Application, namespace work and syncing each need an explicit yes. The root Application is created only after a developer approves the branch ([review-gate.md](../references/review-gate.md)).

---

## Step 1: Inputs

Ask for anything missing, and pause until it's given:

| Input | Example | Notes |
| --- | --- | --- |
| GitOps repo URL | `https://<git-host>/<group>/<repo>.git` | Reuse the URL from workflow 01 if coming from there |
| Argo CD URL | `https://argocd.<domain>` | Needed for `argocd` CLI steps |
| Product name | `oneuptime`, `billing` | Lowercase, DNS-safe |
| Environment | `dev`, `qa`, `test`, `uat`, `pre-prod`, `prod` | |
| Application project(s) | `https://<git-host>/<group>/oneuptime-api` | For the team's own apps: the projects onboarded with `platform onboard`. Checked in step 5. |

From these, derive the names with
[naming.md](../../../core/references/naming.md) and **show them to the user before writing
anything**:

```bash
python3 core/scripts/naming.py --repo-url <gitops-url> --readme <default-branch README.md> \
  --product <product> --env <env>
```

| Name | Example |
| --- | --- |
| Cluster label, and where it came from | `edge-east` (repository name) |
| Chart name (root and extras) | `edge-east-oneuptime` |
| Branch | `oneuptime/dev` |
| Default namespace | `oneuptime-dev` |
| Root Application | `edge-east-oneuptime-dev-root` |

Before going further:

- **If the branch already exists:** stop and go to [01-add-app-existing-env.md](01-add-app-existing-env.md) with that branch.
- **If the root Application already exists** (`argocd app get argo-cd/<root>`): stop and report it. Never overwrite it, and never use `--upsert`.

## Step 2: Preflight (read-only)

1. Check that `git`, `helm`, `argocd` and `kubectl` are installed.
2. **Validate the repo** ([discovery.md](../references/discovery.md) §10), unless workflow 01 already did. Stop on exit 2 or 3. On exit 1, show the problems and go on only if the user says so.
3. Check the Argo CD login and the cluster:
   - `argocd version --server <url>`
   - `argocd account get-user-info --server <url>`
   - `argocd cluster list` (expect `in-cluster`)
4. Check that Argo CD has the repo registered: `argocd repo list`.
5. Pick a **template branch:** the closest existing `<other-product>/<env>` branch in the same repo that the check found free of problems. Use it as the source of truth for:
   - the library version pin
   - `project`, `server`, the `sync` and `extras.sync` blocks
   - the `.gitignore` content
   - the root Application's spec (`argocd app get argo-cd/<template-root> -o yaml`)

## Step 3: Bootstrap the branch

Follow [environment.md](../references/environment.md) with the names from step 1 and these
choices from the template branch:

- **Library release:** the template branch's pin, so every branch in the repository stays on
  one release unless the user asks to upgrade.
- **Environment settings:** the template's `values.yaml` top block, as described in
  [discovery.md](../references/discovery.md) §2.
- **Starter files:** the ones the library's getting-started guide lists at that release. The
  README also gets a Kyverno policy exceptions table, empty to start with.

Create the branch from the repository's default branch, never from the template branch.

- **Don't copy** any of the template's apps, `values/` or `extras/manifests/`. For a copy of an environment, use [04-clone-env.md](04-clone-env.md).
- **Check before moving on:**
  - `helm dependency build` plus `helm lint --strict` plus `helm template` succeed for `.` and `./extras`
  - the only Application rendered is the extras parent

## Step 4: Namespace (cluster admin)

1. Print the commands from [discovery.md](../references/discovery.md) §7 for `<product>-<env>`, with the labels the branch's routes and images need.
2. **Wait** for the user to confirm they've been applied.
3. Verify read-only: `kubectl get ns <ns> --show-labels`.

## Step 5: Add the app(s)

For each application project, run the readiness gate first
([app-readiness.md](../../../core/references/app-readiness.md), with the channel this
environment follows). Each project must be published, or published through the pipeline the
gate offers, before its app is added. Then continue with
[01-add-app-existing-env.md](01-add-app-existing-env.md) **steps 6–10**, with the new branch as the target. The new branch is reviewed as a whole in step 6, so no review branch is needed per app. Add every app the product needs before shipping, so the first sync deploys a complete stack.

## Step 6: Review and ship (each item needs approval)

The root Application is created only after a developer has reviewed the branch
([review-gate.md](../references/review-gate.md), part B).

1. **Commit and push** the new branch: scaffold plus apps in one commit, or scaffold first then apps. No root Application watches it yet, so nothing deploys.
2. **Hand over:** print the branch link and the file links with `review_links.py`, and ask the dev to review, push fixes if needed, and approve here. **Stop until they approve.**
3. **On approval:** fetch the branch, then re-render and re-audit if it changed.
4. **Write the root Application** manifest ([discovery.md](../references/discovery.md) §5):
   - spec copied from the template root
   - `metadata.name`: the root Application name from step 1
   - `targetRevision: <product>/<env>`
5. **Create it:** `argocd app create -f root.yaml`. No `--upsert`.
6. **Sync** in order:

   ```bash
   argocd app sync argo-cd/<root>
   argocd app sync argo-cd/<extras parent>
   argocd app sync argo-cd/<extras directory app>   # each, names from helm template
   argocd app sync argo-cd/<helm app>               # each
   ```

7. **Check** with `argocd app get` on each app until it is Healthy and Synced. Then check the policy reports for the namespace ([kubernetes-rules.md](../../../core/references/kubernetes-rules.md) §4, item 8).

## Step 7: Wire the CI deploy job

Follow [05-deploy-job.md](05-deploy-job.md) steps 1–6 for every app of the new environment. Everything it needs is
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

- the branch
- the root Application
- the namespace labels applied
- any exceptions recorded in the README
- each deploy job: automatic or manual, the CI file changed, and the variables and secrets still to set
