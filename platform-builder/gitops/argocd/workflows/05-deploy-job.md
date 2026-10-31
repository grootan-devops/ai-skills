# Workflow 05: Wire the CI deploy job

> A step of `platform env add`, `platform app add`, `platform env clone` and
> `platform app promote`, not a command of its own. Wires the Argo CD deploy template of the project's CI library
> into an application project's GitLab or GitHub CI. Then a build updates the app's chart
> version in the GitOps repository and syncs its Applications. The platform-independent
> rules are in
> [deploy-job.md](../../../core/references/deploy-job.md); read it first.

**Use when:** workflows 01–04 reach their deploy-job step, or the user asks for a deploy job
for an app that is already deployed. Ask only whether each environment's job runs
automatically or manually; take everything else from the GitOps branch and the application
project.

**Approval rule:** everything up to step 5 is local. Committing, pushing, setting CI variables or secrets, and running a deploy each need an explicit yes.

---

## Step 1: The app in the GitOps repository

Collect these values from the environment branch. Each one must exist; render the branch if
you're unsure.

| Value | Where it comes from |
| --- | --- |
| GitOps repository URL and branch | the branch, e.g. `oneuptime/dev` |
| Values file and yq path of the app's entry | `app_add.py` printed both (`values.yaml`, `.apps.<key>` or `.apps.<group>.<release>`). Several entries on one chart use a yq union path. |
| Image values file and paths | only when the image tag lives apart from the chart version |
| Manifest file | only for an app deployed from `extras/manifests/`, in manifest mode |
| Applications to sync | the root first, then the app's Applications, with names from `helm template` |
| Argo CD server | the host the user gave for this environment |

## Step 2: The application project

1. Reuse the application repository traced in workflow 01 step 3. Ask for it only when the
   deploy job is added on its own. When
   the readiness gate ran ([app-readiness.md](../../../core/references/app-readiness.md)), its
   facts file already gives the chart name, the chart and image repositories and the channel.
   Use them for the deploy job's inputs.
2. Run `core/scripts/ci_pin.py <app-repo> --backend argocd --fetch <scratch>`
   ([deploy-job.md](../../../core/references/deploy-job.md) §1). Stop where that section says
   to.
3. Read the deploy template and the CI library's GitOps deploy docs at the pinned release.
   Note its inputs, CI variables, secrets and rules.

## Step 3: Automatic or manual

This is the one question: for **each environment**, automatic (after a build of which
branch; suggest the branch the environment's channel follows) or manual.

Write the wiring the way [deploy-job.md](../../../core/references/deploy-job.md) §3 describes.
Use one include (GitLab) or one job (GitHub) per environment. Fill in each input from step 1,
with input names taken from the template.

## Step 4: Variables and secrets

List what the template needs: the Argo CD token, the GitOps repository token, and registry
credentials when it validates the chart or image. Say where each one is set
([deploy-job.md](../../../core/references/deploy-job.md) §4).

The Argo CD token needs sync permission on the listed Applications only. Never ask for the
token's value.

## Step 5: Validate

1. Run `core/scripts/audit.py <app-repo> --strict`, or the native lint
   ([deploy-job.md](../../../core/references/deploy-job.md) §5).
2. Check that the deploy job's GitOps inputs match step 1 exactly: branch, file, yq path and
   Application names. A wrong yq path updates nothing, and a wrong Application name fails the
   sync.

## Step 6: Ship (each item needs approval)

1. Commit and push the CI change in the application project. Use a feature branch and a
   merge or pull request if the project requires it.
2. The user sets the variables and secrets from step 4.
3. **First deploy:**
   - *Automatic:* the next build of the named branch deploys.
   - *Manual:* run the manual job or dispatch the workflow for the environment.

   Watch it until it commits to the GitOps branch and the Applications are Healthy and
   Synced.

## Step 7: Report

Tell the user:

- the application repository and the CI file changed
- the CI library release used
- for each environment: automatic (and on which branch) or manual
- the GitOps branch, file, yq path and Applications each job updates
- the variables and secrets they still have to set
- the audit or lint result
