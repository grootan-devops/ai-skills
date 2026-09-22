# Ignore Files Standard — `.gitignore`, `.dockerignore`, `.helmignore`

Three files that look alike and behave differently. The recurring defect is assuming one
set of semantics covers all three — so start from what reads each file, because that is
what decides whether a mistake is cosmetic or load-bearing.

| File | Read by | Wrong entry costs you |
| --- | --- | --- |
| `.gitignore` | git | a secret or a cache in history — permanent |
| `.dockerignore` | the image build context | a bloated image, or a missing dependency tree |
| `.helmignore` | the Helm **chart loader**, not just `helm package` | a dependency that resolves as missing while the file is present |

**The rule that is not shared:** `.gitignore` and `.dockerignore` govern what is *sent*.
`.helmignore` also governs what is *loaded*. That difference is the whole of §3.

---

## 1. `.gitignore` — deny the generated and the secret

Nothing generated, nothing secret. The Helm entries are the ones most often missed:

```gitignore
# Helm -- resolved dependencies and the lock are build output, never sources
charts
Chart.lock

# Secrets
.env
.env.*
!.env.example
*.pem
*.key

# Stack (pick the ones that apply)
node_modules/        # Node
dist/                # Node build output
__pycache__/ .venv/ .uv/ .pytest_cache/   # Python
target/ .gradle/     # Java
bin/                 # Go
```

`charts` and `Chart.lock` are committed by accident more than anything else here. A
committed `charts/` shadows what CI resolves, so a stale dependency ships and the pipeline
that would have caught it never runs.

---

## 2. `.dockerignore` — inverted allowlist

Default-deny, then admit only what the runtime needs. An allowlist stays correct as the
repo grows; a denylist silently starts shipping every new directory.

```dockerfile
**
*

# Dependency manifests
!package.json
!package-lock.json

# CI cache for the BuildKit bind mount
!.npm
!.npm/**

# Application source
!*.js
!routes
!routes/**
```

Three rules:

1. **`**` then `*`, in that order, as the first two lines.** This is the form every
   example in the CI library's README uses, and it is what the library's own Dockerfiles
   are written against. Match it rather than reasoning about which one subsumes the other.
2. **Anchor root-only patterns without a leading slash.** `!*.js` admits root modules and
   does not cross `/`. A leading `/` is not a reliable anchor here; that is `.helmignore`
   and `.gitignore` semantics, not Docker's.
3. **Do not append a re-deny section.** `**` already denied everything; a trailing block of
   `test/`, `**/*.md`, `**/.env*` re-denies what was never admitted. It reads as defence in
   depth and is dead weight that the next person has to reason about. If something unwanted
   is reaching the image, the allowlist above it is too broad — narrow the `!` line instead.

**Admit the package-manager cache directory (`.npm`, `.uv`), not the installed tree.** The
image installs offline from that cache through a BuildKit bind mount; `node_modules/` and
`.venv/` are neither copied in nor uploaded as artifacts. Admitting `node_modules` instead
of `.npm` is the same defect as an online `npm ci` in the Dockerfile — see
the resolved library's own `README.md`.

## 3. `.helmignore` — and the trap that costs hours

### The baseline

```text
.DS_Store
.git/
.gitignore
.svn/
*.swp
*.bak
*.tmp
*.orig
*~
.project
.idea/
*.tmproj
.vscode/

Chart.lock
.helmignore
.gitlab-ci.yml
.yamllint
README.gotmpl

test/
```

What is **absent** matters as much as what is present: no bare `*.tgz`, and no `charts`.

### Why a bare `*.tgz` breaks the build

`.helmignore` is applied by the chart **loader**, and an unanchored pattern matches a
basename at *any* depth. So `*.tgz` reaches into `charts/` and hides the dependency
archives `helm dependency update` has just downloaded. Helm then reports:

```text
[WARNING] chart directory is missing these dependencies: tpl-library
[ERROR] templates/: ... at <include "tpl.deployment" .>:
        template: no template "tpl.deployment" associated with template "gotpl"
```

Both messages are false. The archive is present, intact, correctly named and correctly
versioned. Neither line mentions `.helmignore`, which is why this gets misdiagnosed as a
bad dependency pin, a registry auth failure, or a Helm version defect — and why it is
worth knowing by sight.

If a packaged chart at the chart root must be excluded, anchor it:

```text
/*.tgz      # chart root only -- charts/*.tgz still loads
```

Never ignore `charts` or `charts/` at all.

### The one-step diagnostic

Untar `charts/<dep>.tgz` **in place** and re-run the lint. If it passes with the archive's
contents but fails with the archive, the loader is ignoring the file — and `.helmignore`
is the only thing that does that.

```bash
grep -nE '^\s*\*\.tgz\s*$|^\s*charts/?\s*$' chart/.helmignore
```

---

## 4. Audit checklist

**Every entry below is conditional on the repository actually producing the thing it
ignores.** An ignore file is a statement about what this project generates; padding it with
entries for a stack the project does not use is noise, and noise in a findings list is how a
reader learns to skim it. The stack entries (`.venv`, `node_modules`, `target`) were already
gated on the manifest that implies them; these two are gated the same way.

| Check | File | Severity | Applies when |
| --- | --- | --- | --- |
| exists at all | all three | P1 / P1 / P2 | always |
| `.env` ignored, `.env.example` re-admitted | `.gitignore` | P2 | always — any project can grow a local `.env` |
| `charts` + `Chart.lock` ignored | `.gitignore` | P2 | **only with a chart** — `helm dependency update` produces both; without a chart neither can appear |
| stack caches (`.venv`, `node_modules`, `target`, …) | `.gitignore` | P2 | only with that stack's manifest |
| starts `**` then `*` | `.dockerignore` | P2 | always, where a Dockerfile exists |
| has at least one `!` | `.dockerignore` | P2 | **only when the Dockerfile copies from the build context** — an image assembled from earlier stages or a base image alone correctly admits nothing |
| cache dir (`.npm`/`.uv`) admitted | `.dockerignore` | P1 | only where the image installs offline from it |
| no bare `*.tgz`, no `charts` | `.helmignore` | **P1** | chart repositories |
| baseline entries present | `.helmignore` | P2 | chart repositories |

`core/scripts/checks_common.py` enforces these as `check_gitignore`, `check_dockerignore`
and `check_helmignore`. Add a rule there, not to a platform adapter — all three files are
identical on GitLab and GitHub.
