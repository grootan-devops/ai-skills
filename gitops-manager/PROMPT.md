# GitOps Manager: Copyable Engineer Prompts

Replace placeholders before pasting. These prompts use the workflows in
[SKILL.md](./SKILL.md) and the precise preflight and file contract in
[the environment guide](./references/argocd-environment.md). `add` and `update`
prepare and validate locally. Include a remote action in your request if needed.

## `argocd env add`

### Quick

```text
Run argocd env add for <project>/<environment>. GitOps repository: <gitops-repo-url>; Argo CD URL: <argocd-url>; library source: <source-or-default>. Read the default-branch README for the cluster and Argo CD destination, check branch collision, verify a published stable OCI chart version, scaffold the root and extras charts and root Application from assets/bootstrap, and validate both charts locally. Show the files, exact pin, checks, and any unverified remote prerequisites. Do not commit, push, create an Application, or sync unless I explicitly request those actions.
```

### Full

```text
Act as a GitOps engineer using the gitops-manager skill. Prepare a new
environment for project <project> and environment <environment> (explicitly
empty if this project has no environment suffix).
GitOps repository: <gitops-repo-url>
Argo CD URL: <argocd-url>
argocd-gitops-tpl-library source/ref: <source-or-default>
Target branch override: <branch-or-none>

1. Preflight: check local git and Helm. Read the GitOps repository's actual
   default-branch README and extract the cluster label and Argo CD cluster
   name; never infer either from URLs. Check the target branch for collision.
   Record which Argo CD authentication, repository registration, and cluster
   checks remain for activation. Do not ask me to paste credentials.
2. Resolve the selected library source/ref. Read its README and linked
   bootstrap guides at that same ref. Find the highest published stable OCI
   chart version and verify it with helm show chart --version; do not pin an
   unreleased branch's Chart.yaml value or a README example.
3. Create the local branch and populate assets/bootstrap: Chart.yaml,
   values.yaml, values/, templates/apps.yaml, root-application.yaml, and
   extras/ as required. Use the documented project, namespace, destination,
   naming, and branch rules. Keep credentials out of manifests. Do not
   overwrite an existing branch or Application.
4. Build dependencies, lint, and render both root and extras charts. Inspect
   rendered Application names, destinations, and target revisions. Report
   exact files, version, validation results, and remote prerequisites.
5. Stop at local preparation. Commit, push, Application creation, and sync
   each require my explicit request; preflight and verify any requested action.
```

## `argocd env update`

### Quick

```text
Run argocd env update for <project>/<environment> in <gitops-repo-url>, using Argo CD <argocd-url> and library <source-or-target-version>. Inspect the current chart pin and MIGRATION.md between current and target versions. Preserve existing values, applications, and custom resources; make only necessary local edits. Validate root and extras chart rendering, show the diff and activation prerequisites, and do not commit, push, create an Application, or sync unless explicitly requested.
```

### Full

```text
Act as a GitOps engineer using the gitops-manager skill. Update the existing
<project>/<environment> branch in <gitops-repo-url>.
Argo CD URL: <argocd-url>
Target argocd-gitops-tpl-library source/version: <source-or-version>

1. Inspect the current branch, Chart.yaml dependency pin, root and extras
   chart files, values, templates, Application manifests, and local changes.
   Read the repository default-branch README for cluster metadata. Do not
   overwrite an unrelated branch or existing custom resource.
2. Resolve and verify the published target OCI chart version. Read the
   selected-ref README and every applicable MIGRATION.md section between the
   current and target versions. List mandatory schema, values, and sync-policy
   changes with file evidence; report missing migration information.
3. Plan and apply a surgical local diff. Preserve existing application
   definitions, values, repository URLs, custom parameters, and intentional
   sync behavior. Change only the dependency pin and required migration
   fields. If a mandatory change conflicts with customization, show the
   proposed diff and ask before changing that behavior.
4. Build dependencies, lint, and render root and extras charts. Inspect
   rendered Applications and report the diff, checks, risks, and any remote
   authentication or cluster prerequisites.
5. Leave the result local. Commit, push, Application creation, and sync each
   require an explicit request and the reference's remote preflight.
```

## `argocd env audit`

### Quick

```text
Run argocd env audit for <project>/<environment> in <gitops-repo-url> with Argo CD <argocd-url>. Inspect the selected library pin, chart files, rendered Applications, and available read-only remote health information. Report findings with file evidence and any unavailable authentication or cluster check. Make no changes.
```

### Full

```text
Act as a GitOps auditor using the gitops-manager skill. Audit
<project>/<environment> in <gitops-repo-url>; Argo CD URL <argocd-url>.
Read the repository's default-branch cluster metadata and the environment
branch. Resolve the exact library source and chart dependency version; read
the relevant guides at that ref. Inspect root and extras charts, values,
rendered Applications, branch targets, destinations, and plaintext secret
exposure. Run read-only Helm validation. If authenticated Argo CD access is
available, inspect repository registration, cluster identity, root Application
health and sync status without creating or syncing anything. Separate verified
findings from checks that could not run; include severity, file/command
evidence, and exact library provenance. Do not modify local or remote state.
```
