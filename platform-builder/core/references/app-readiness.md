# App Readiness

An application project reaches GitOps in a fixed order:

1. `platform onboard` writes its chart, Dockerfile and CI.
2. The project's first full CI run publishes the image and the chart.
3. `platform env add` or `platform app add` adds the app to an environment, and wires its CI
   deploy job (automatic or manual).

This gate runs at the start of step 3. It checks that steps 1 and 2 happened, and takes the app's facts
from the project itself so that nothing about the chart or image is asked or guessed.

## 1. Read the project

```bash
python3 core/scripts/app_facts.py <app-repo>
```

It reads the project's own files, in the same places the audit reads them:

- the chart directory, its name and version, and whether it is built on the service library
- `partOf`, `component` and `subComponent`
- the image registry and repository
- the CI platform, the CI-library pin, and the registry settings the CI passes to the library

**Exit 2, not onboarded:** the project has no CI-library pin, or neither a chart nor a
Dockerfile.

- Stop the GitOps work, and offer `platform onboard <app-repo>`.
- After the onboarding change is merged and the pipeline has run, start again from this gate.

A third-party repository, or one with its own CI that the team doesn't want onboarded, also
exits 2. For those, workflow 01 traces the repository by hand instead.

## 2. Work out where CI publishes

The registry, the path and the version scheme for each branch or channel are the CI library's
rules at the project's pin. Read them from that release's configuration and publishing docs
(start at its README index), together with the registry settings `app_facts.py` printed.

From them, write down:

- the chart's OCI repository, without the chart name
- the image repository, without a tag
- the channel the environment follows: `rc` for an environment fed by pre-releases (typically
  `dev`), `release` for one fed only by releases

Never copy a path from another project or an example.

## 3. Check the registry

```bash
python3 core/scripts/app_facts.py <app-repo> --check --chart-repo oci://<host>/<path> \
  --image <host>/<path> --channel rc|release [--version <v>] [--image-tag <t>] --json > <scratch>/app-facts.json
```

- **What it does:** it lists the chart's tags and picks the newest one of the channel. Then it
  checks the image tag, which by default is the same as the chart version.
- **Tags that differ:** pass `--image-tag` when the library tags the image differently.
- **Read-only:** it uses the user's existing registry login (oras or crane, falling back to
  `helm show chart`, then crane, skopeo or docker for the image). It never logs in and never
  pushes.

**Exit 0, ready:** keep the JSON file. `app_add.py --app-facts <file>` takes the chart source,
the service values and the main container's image from it.

**Exit 1, could not check:** no listing tool is installed, or the registry refused the login.

- Say which.
- Ask the user to log in with their own credentials, or to name the published version with
  `--version`.
- Never ask for or handle the credentials yourself.
- Some registries, ghcr.io among them, answer "denied" for a repository that doesn't exist
  yet. If the user is logged in and still gets "denied", treat it as not published, and say
  so.

**Exit 3, not published:** go to step 4.

## 4. Not published yet: offer to run the pipeline

1. **Show what is missing:** the chart version, the image tag, or both.
2. **Find the pipeline that publishes them** on the branch the environment follows, from the CI
   library's docs at the pin: its name, and the variables or inputs it needs.
3. **Offer to start it, and start it only on an explicit yes:**

   | Platform | Start | Watch |
   | --- | --- | --- |
   | GitLab | `glab ci run --branch <branch> [--variables KEY:value]` | `glab ci status --branch <branch> --live` |
   | GitHub | `gh workflow run <workflow> --ref <branch> [-f input=value]` | `gh run watch <run-id> --exit-status` |

   Watch it in the background, and report the pipeline link to the user.
4. **If it fails:** report the failed job and its log tail, and stop. Fixing the project's CI
   is a separate task: offer `platform audit <app-repo>`.
5. **If it succeeds:** run step 3 again and continue only when it exits 0.
6. **If the user declines:** stop, and give the exact command to run themselves. Re-invoke the
   skill after the run.

## 5. Report

- The facts used: chart, version, image and tag.
- The CI-library pin and the channel.
- Which tools checked the registry.
- Whether a pipeline was run, with its link.
