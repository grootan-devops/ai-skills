# CI Deploy Job

A deploy job runs in the **application project's** CI. For every environment it updates the
chart version (and optionally the image) in the GitOps repository, then has the backend roll
it out. The CI libraries ship one deploy template per backend; it is wired in as is, never
replaced by a deploy script of our own.

Everything about the template comes from the CI library **at the release the project already
pins**: its inputs, required CI variables and secrets, the rules that decide when it runs, and
its job names. Read the template and the library's GitOps deploy docs (start at the library
README index) at that release. Never copy input names from an older project or from this
file.

The deploy job is a CI change like any other: it follows the CI rules of `SKILL.md`, and
`core/scripts/audit.py` is the authority on it.

## 1. Find the project's platform and pin

```bash
python3 core/scripts/ci_pin.py <app-repo> [--platform gitlab|github] --backend <backend> --fetch <scratch-dir>
```

The script prints:

- the platform: GitLab or GitHub. If the project has both or neither, it asks.
- the CI library and the pinned release. With several releases it picks the oldest and flags
  them as mixed.
- with `--fetch`, the library cloned at that release and the backend's deploy template inside
  it.

It stops in these cases:

- **No pin:** the project isn't on the CI library yet. Offer `platform onboard` first, and
  stop until it's done.
- **The template isn't in the pinned release:** the library must be upgraded first. Offer
  `platform update`, and stop until it's done.
- **Mixed pins:** wire the deploy job at the oldest release. Offer to align the pins with
  `platform update`; never silently bump the project's other includes.

## 2. Collect the GitOps side

Each backend's deploy workflow lists what the deploy job needs from the GitOps repository: the
repository and branch, where the version lives (the file and path), and which apps to roll out
and in what order. Take every value from what the skill just wrote or read, never from an
example.

## 3. Ask: automatic or manual

Always ask, per environment. There is no default:

- **Automatic:** the deploy runs on its own after a successful build of the branch the user
  names. Typical for `dev`.
- **Manual:** someone triggers it, through a manual job or a dispatch. Typical for `qa` and
  `prod`.

How each choice is wired:

| | Automatic | Manual |
| --- | --- | --- |
| **GitLab** | One include of the template per environment, plus a project-level `rules:` on that environment's deploy job: a first rule that runs it `on_success` in the build pipeline on the named branch, followed by the template's own rules **copied from the pinned release** so the targeted deploy keeps working. Never override `script:`. | One include per environment with the template's rules unchanged: its manual and targeted-deploy paths. |
| **GitHub** | A job in the workflow that already builds and publishes on the named branch. It calls the deploy workflow at the pinned release (`uses: …@<pin>`) and `needs:` the job whose outputs it takes. | A separate `.github/workflows/deploy.yml` with `workflow_dispatch`: an environment choice and an optional target version. It has a `run-name`, a `concurrency` group per environment that never cancels a running deploy, and least-privilege `permissions`. |

GitLab, automatic:

- **Expand the template's input placeholders.** The template's rules refer to its inputs with
  `$[[ inputs.… ]]` placeholders, which GitLab expands only inside the included file. Copy
  the rules with each placeholder replaced by the value that environment's include passes.
- **The version must come from the same pipeline.** The automatic rule only fires in a
  pipeline that also pushes the chart (and the image), so the deploy job picks up that
  pipeline's version. Check that the build workflow on that branch pushes them.

Both platforms:

- **Use the include form or `uses:` form the project's other library modules use,** at the
  same release.
- **GitLab:** add the deploy option and the environment to the project's workflow variables,
  where the library's docs at the pin say so.
- **GitHub:** pass the environment name the template exposes, so environment protection rules
  can gate production.
- **One comment line per non-default choice,** saying why. For example: `# dev deploys
  automatically after main builds`.

## 4. Variables and secrets

List every CI variable and secret the template needs at the pin, and where to set it: GitLab
project or group CI/CD variables (masked and protected for tokens), or GitHub repository or
environment secrets and variables. The user sets them. The skill never writes, prints or asks
for their values.

A token that pushes to the GitOps repository needs write access to the environment branches
and nothing else. A backend token needs only the sync permission on the listed apps.

## 5. Validate and hand over

1. **Run the audit** with the same library source:
   `python3 core/scripts/audit.py <app-repo> --platform <gitlab|github> --strict`.
   Fix every new finding the deploy job caused.

   One finding is not caused by the wiring. If the library's workflow map at the pin lists
   the manual GitHub `deploy.yml` as a scenario but in no shape, the audit reports it as an
   unneeded workflow. Keep the workflow and tell the user the finding comes from the
   library's data.
2. **Also run the native lint:**
   - GitLab CI lint as a dry run: the project's CI Lint API, or `glab ci lint`.
   - GitHub: `actionlint`, when it's installed.
3. **Show the diff and the variables and secrets list.**
4. **Stop.** Committing, pushing, setting variables and running a deploy each need the user's
   explicit request.
