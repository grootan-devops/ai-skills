# Argo CD GitOps Repository Conventions and Discovery

Every Argo CD workflow reads this file. It covers how these repositories are organised and how
to find an environment's specifics. It does not restate the library: how
`argocd-gitops-tpl-library` turns `values.yaml` into Applications is documented by the library
at the release the environment pins. That covers child Application names, values-file lookup,
release names, destination namespaces, the root Application manifest and the
`kustomized-helm` plugin contract. Read those docs; don't infer them from examples.

When the repository and this file disagree, the repository wins. Tell the user about the
difference.

## 1. Repository model

- **One Git repository holds many environments**, one branch each, plus a shared `sandbox`
  branch for proofs of concept. The default branch holds only the README.
- **Each environment branch is a root chart plus an `extras/` chart.** Both pin the same
  library release.
- **Branch names:**
  - `<product>/<env>`, for example `oneuptime/dev` or `oneuptime/prod`
  - `sandbox`, a single word
  - `<env>` is one of `dev`, `qa`, `test`, `uat`, `pre-prod` and `prod`
  - everything is lowercase kebab-case, with `/` only between product and env
- **Chart name:** `<label>-<product>`, identical in `Chart.yaml` and `extras/Chart.yaml`.
  Every environment of a product shares it, and `environment` tells them apart.
  - The label and the remaining derived names come from
    [`../../../core/references/naming.md`](../../../core/references/naming.md).
  - Existing branches keep the names they already have.
- **Library release:** read it from an existing branch of the same repository, and keep every
  branch on one release unless the user asks to upgrade.

## 2. Environment settings

Copy the top of `values.yaml` from a sibling branch. That top block holds `enabled`, `project`,
`environment`, `namespace`, `server`, `repoURL`, `branch`, `sync` and `extras`. Change only:

- `environment`
- `namespace`, which defaults to `<product>-<env>`
- `branch`

These conventions hold on every branch:

- `CreateNamespace=false`, because a cluster admin creates namespaces (§7)
- the Argo CD project of the sibling product branches, never the platform project
- `apps: {}` on a new branch
- `extras/values.yaml` repeats the same settings

## 3. App entries

- **Key order:** keep every entry in this order and include every key that applies:
  `enabled`, `namespace`, `releaseName`, `chart`, `plugin`, `ignoreDifferences`, `sync`.
- **Sorting:** apps are sorted alphabetically, case-insensitively, and so are the children
  inside each group.
- **The plugin** (`kustomized-helm`): use it for public or third-party charts, to patch what
  the chart's values can't set. Don't use it for private charts built on `helm-tpl-library`,
  whose values already expose what the rules need. Patches follow the plugin contract in the
  library docs at the pin.
- **`releaseName`:** set it only when two apps share a chart in one namespace and that chart
  builds object names from the release. Render both apps and check that no object names
  collide.

## 4. Extras manifests

- **Layout:**
  - Product branches use one directory per component: `extras/manifests/<component>/`.
  - `sandbox` nests by project: `extras/manifests/<project>/<component>/`. A single-service
    project sits flat at `extras/manifests/<project>/`.
- **One object kind per file,** named after the kind: `deployment.yaml`, `service.yaml`,
  `pdb.yaml`, `httproute.yaml`, `secret.yaml` and so on. Several objects of one kind take a
  suffix: `httproute-api.yaml`.
- **Every manifest sets `metadata.namespace`.**
- **Companion objects for Helm apps go here too.** If a Helm app needs an object its chart
  can't create (a PDB, HTTPRoute or Certificate), put it in `extras/manifests/<app>/`.
- **File extension:** the library decides which files and directories it picks up. Use
  `.yaml` the way sibling directories do.

## 5. Root Application and sync order

- **Create** the root Application once per branch, with `argocd app create -f`. Never use
  `--upsert`.
- **Write the manifest from the library's root Application guide,** with `project`,
  `syncPolicy` and `retry` copied from a live sibling root:
  `argocd app get argo-cd/<sibling-root> -o yaml`.
- **Sync order:**
  1. the root
  2. the extras parent
  3. extras directories that create CRs or Secrets other apps need
  4. the Helm apps
  5. the remaining extras

## 6. Environment specifics: discover, don't assume

Read these from sibling apps on the same branch first, then from other branches of the same
repository. Show the user what you found, and ask when siblings disagree.

| Concern | Where to look |
| --- | --- |
| Routing | `extras/manifests/*/httproute*.yaml`, `ingress*.yaml`, values `routes:` / `ingress:`: Gateway API or Ingress, the gateway or class, and `sectionName` |
| Hostname | existing routes on the branch: the per-environment domain pattern |
| TLS secret | route or ingress `tls`, and how it reaches the namespace (for example a reflector label) |
| Storage class | PVCs and values `storageClassName`; new PVCs start at **1Gi** |
| Pull secret | `imagePullSecrets`, and how it reaches the namespace |
| Shared databases | private-chart values `database:`: the shared service host, and per-app database and user names |
| Identity | values `oauth:` / `keycloak:`: the realm and per-app clients |

## 7. Namespaces

Argo CD does not create namespaces (`CreateNamespace=false`); a cluster admin does. The
workflow prints the commands and waits for confirmation:

```bash
kubectl create namespace <ns>
kubectl label namespace <ns> pod-security.kubernetes.io/enforce=baseline <labels siblings carry>
```

- Copy the extra labels from a sibling namespace (`kubectl get ns <sibling> --show-labels`),
  for example the ones that sync the pull secret and the TLS secret.
- Use `restricted` instead of `baseline` when every workload in the namespace meets it.

## 8. Discovery commands (read-only)

```bash
git ls-remote --heads <gitops-url>
git -C <clone> show <branch>:values.yaml
git -C <clone> ls-tree -r --name-only <branch> extras/manifests
argocd app list -o name | grep -- '-root$'
argocd app get argo-cd/<root> -o yaml
helm show chart <repo-or-oci> --version <v>     # add --repo for http repos
helm show values <repo-or-oci> --version <v>
```

## 9. Local validation before any commit

```bash
helm dependency build . && helm template x . > <scratch>/apps.yaml
helm dependency build ./extras && helm template x ./extras > <scratch>/extras-apps.yaml
```

Then render every new or changed app exactly as Argo CD will:

- **Plugin apps:** render the chart with the values and kustomization the Application
  carries, as the library's plugin docs describe.
- **Native apps:** `helm template` with the values file.

Audit the output against
[`../../../core/references/kubernetes-rules.md`](../../../core/references/kubernetes-rules.md).

## 10. Validate the repository first

As soon as the user gives the GitOps repository link, and before any other step reads or
writes it, check once that it is the kind of repository these workflows work on:

```bash
python3 gitops/argocd/scripts/repo_check.py <gitops-url>
```

It clones the repository bare into a temporary directory and writes nothing. Its checks:

- **Reachable:** the link answers with the user's own git credentials.
- **Environments:** at least one branch is an environment of the library. Its root and
  `extras/` charts depend on the library at the same release and share a name, and its
  `values.yaml` parses with an `apps` map.
- **Pattern:** each environment branch is compared with its siblings, never with an example:
  - the library releases they pin
  - the top-level values keys and the files most branches have
  - the `branch` value each one records
- **Default branch:** its README, and the cluster label it records
  ([naming](../../../core/references/naming.md)).

What to do with the result:

| Exit | Means | Do |
| --- | --- | --- |
| 0 | A GitOps repository that follows its own pattern | Go on. Drift notes are worth mentioning, but they don't block. |
| 1 | Some environment branches have problems | Show every problem and ask before going on. Never use a branch with problems as a template, and never copy its broken parts into a new branch. |
| 2 | The link can't be read, or no branch is a library environment | Stop. Say which, and ask for the right link, or for access with the user's own credentials. |
| 3 | The repository is empty | Stop. There is no pattern to follow, and bootstrapping a new GitOps repository is not supported yet. Ask whether this is the right link. |

Run it once per repository and session. Reuse its output (branches, pins, environment names,
cluster label) in the steps that follow instead of reading the branches again.
