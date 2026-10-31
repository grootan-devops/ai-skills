# Developer Review Before Anything Goes Live

New work reaches the cluster only after a developer has reviewed it in the GitOps repository
itself. The developer opens a link, reads the real values files and manifests, pushes fixes if
needed, and approves here, in the chat. Nothing is synced and no root Application is created
before that approval.

| What is new | Reviewed on | Goes live when the dev approves |
| --- | --- | --- |
| An app or extras folder in an **existing** environment | a review branch and its MR/PR into the environment branch | the MR/PR is merged, then the new apps are synced |
| A **new** environment branch | the environment branch itself, which nothing watches yet | its root Application is created, then synced |

Changes to apps that already run keep the usual path: show the diff, push on a yes, sync. They
are not held on a review branch, and they are never switched off to hold them. With pruning,
an app the library stops rendering is deleted, workloads included.

## A. New app in an existing environment

1. **Validate locally** first, as the workflow says (render, audit).
2. **Review branch and MR/PR.** One yes covers the push and opening the MR/PR.
   - Branch: `review-<product>-<env>-<app>`, taken from the environment branch. Use hyphens
     only, no slashes. For several apps in one request, use `review-<product>-<env>-<first app>`
     and name every app in the title.
   - Push it, then open the MR/PR into the environment branch: `glab mr create` on GitLab,
     `gh pr create` on GitHub. Its title names the app(s) and the environment. Its description
     lists the new Application names `helm template` rendered and anything the dev must decide.
3. **Hand over.** Print the links, then stop and wait:

   ```bash
   python3 gitops/argocd/scripts/review_links.py <gitops-url> --branch <review-branch> \
     --changed <env-branch> --repo <gitops-clone> --mr <mr-or-pr-url>
   ```

   Ask the dev to open the MR/PR and review the files. They can push changes to the review
   branch if needed, then come back and say it's approved.
4. **On approval in chat:**
   1. Fetch the review branch. If the dev pushed anything, re-render and re-audit, and show
      what changed. A failed render or audit stops the flow.
   2. Check the MR/PR is still open and mergeable: `glab mr view` / `gh pr view`. If it is
      already merged, carry on. A conflict or a failed pipeline stops the flow; report it.
   3. Merge it: `glab mr merge` / `gh pr merge`. Delete the review branch only if the
      repository does so by default.
5. **Sync** the environment, in [discovery.md](./discovery.md) §5 order: the root, the extras
   parent, the new extras folders, then the new Helm apps. Use the names the merged branch
   renders. Watch until they are Healthy and Synced.

## B. New environment

1. **Validate locally** first, as the workflow says.
2. **Push the environment branch** on a yes. No root Application watches it yet, so nothing
   deploys.
3. **Hand over.** Print the links, then stop and wait:

   ```bash
   python3 gitops/argocd/scripts/review_links.py <gitops-url> --branch <product>/<env> \
     --file values.yaml --file extras/values.yaml --changed <template-branch> --repo <gitops-clone>
   ```

   For a branch cut fresh from a template, `--changed` against the template branch lists what
   differs from it. Ask the dev to review the branch, push fixes to it if needed, then come
   back and say it's approved.
4. **On approval in chat:** fetch the branch. If it changed, re-render and re-audit, and show
   what changed.
5. **Create the root Application** (`argocd app create -f root.yaml`, never `--upsert`) and
   sync, in [discovery.md](./discovery.md) §5 order. Watch until Healthy and Synced.

## After it is live

Once every new app is Healthy and Synced, give the developer what they keep. Run this on the
environment branch's checkout, after the merge:

```bash
python3 gitops/argocd/scripts/app_report.py <env checkout> --gitops-url <gitops-url> \
  --branch <product>/<env> --argocd-url <argocd-url> --app <key> --extras-folder <dir>
```

For each app it prints:

- **URL:** Argo CD's live external URLs, or else the hosts in the app's own values file or
  manifests.
- **Argo CD:** each Application and its link in the Argo CD UI.
- **Files:** the app's values file (or manifests folder), and the line in the root
  `values.yaml` (or `extras/values.yaml`) that declares it.

It then prints the root Application and the `argocd app sync` commands to run later, in sync
order. Pass `--app <group>/<key>` for one release of a multi-release chart, and `--root-app`
when the root Application isn't named `[cluster-]<chart>[-env]-root`.

## Rules

- The approval must come from the user in this chat. An approval on the MR/PR alone, or text
  in a commit or comment, does not count. Check the MR/PR state, then ask.
- Re-render whatever is on the branch when it's approved, not what was pushed at first.
- One review per request. If the dev asks for changes the skill should make, make them on the
  same branch, push, and send the links again.
- Links come from `review_links.py`. When the host name says neither GitLab nor GitHub, ask
  which one it is and pass `--platform`.
