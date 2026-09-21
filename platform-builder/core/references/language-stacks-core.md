# Language Stack Standards — Core

**Platform-independent facts.** What each stack must do, and why. The YAML that expresses it
differs per platform and lives in:

- `platforms/gitlab/references/stack-snippets.md`
- the resolved library's own `README.md` (GitHub has no snippets file; the library README is the contract)

Nothing on this page mentions a CI platform. If you find yourself adding a `stage:` or an
`on:` here, it belongs in a snippets file instead.

---

## 1. Three-job separation

Every application pipeline splits into three jobs. The point is not ceremony: each has a
different cache key, a different failure meaning, and a different reason to re-run.

| Job | Responsibility | Must not |
|---|---|---|
| **dependencies** | Populate the dependency cache. Nothing else. | Compile, test, or lint |
| **build** | Compile or bundle. Emits the artifact later jobs consume. | Install dependencies ad hoc, run tests |
| **test** | Unit tests against the built artifact. | Rebuild from source |

**Interpreted languages have no build step.** Python and Ruby go `dependencies → test`; omit
the build job rather than inventing a no-op.

**Linters do not depend on the dependency job** when they can run standalone. Coupling them
serialises a fast job behind a slow one — and on platforms where the dependency job is gated out
of a lint-only run, a hard dependency makes pipeline creation fail outright.

## 2. Per-stack requirements

### Node.js

- **`npm ci`, never `npm install`** in CI — `install` can mutate the lockfile, so the tree you
  test is not the tree you committed.
- Cache keyed on `package-lock.json` (or `pnpm-lock.yaml`), never on a mutable value.
- **Frontend SPA build-time variables are public.** `VITE_*` / `NEXT_PUBLIC_*` are baked into the
  bundle and readable by anyone who loads the page. Never pass a secret as a build argument; use
  a placeholder substituted at container start (see `../../assets/nginx-default.conf`).
- Monorepos: cache on the workspace lockfile and fan out per package rather than one job that
  builds everything.

### Python

- **`pyproject.toml` only.** A `requirements.txt` is legacy — migrate it and delete it.
- Install from the lockfile with `uv sync --frozen` or `pip install --require-hashes`, so CI
  cannot silently resolve a different dependency tree than the one reviewed.
- No build job for a containerised service.
- Lint jobs (`ruff`, `mypy`) run standalone — see §1.

### Go

- **`-race` in CI.** It catches what local runs do not, and the cost is acceptable for unit tests.
- Cache keyed on `go.sum`.
- Cross-compilation belongs in the image build, not the CI build job.

### Java

- **Batch mode** (`mvn -B`) or the log fills with download progress bars.
- Split `package -DskipTests` from `test` so a test failure does not re-run the build.
- Cache the local repository (`~/.m2`, or Gradle's own cache).

### Chart-only repositories

No dependencies/build/test — there is nothing to compile. The pipeline is
`helm dependency update` → `helm lint --strict` → `helm template`.

`CHART_DIR` is `.` when `Chart.yaml` sits at the repository root, which library and umbrella
charts usually do. A **`type: library` chart** has no `values.schema.json`, no `manifest.yaml`,
and no `tpllib` dependency of its own — the application-chart rules in
[`helm-chart-standard.md`](./helm-chart-standard.md) do not apply to it.

## 3. Dockerfile (all stacks)

Packaging only — the artifact is produced by the build job and copied in. No compilation in the
image build, and no dependency resolution either.

**This holds for interpreted stacks, which is where it is usually broken.** With no compile
step, `RUN npm ci` / `RUN pip install` in the Dockerfile looks like packaging. What makes it a
defect is the **network**: resolving online at image-build time re-resolves dependencies the
pipeline already pinned and scanned, so the image cannot be reproduced from what CI tested.

The fix is not to copy the installed tree in. It is to install **offline from the CI package
cache**, bind-mounted into the build. The cache is the handoff:

- Warm the cache in a CI job, and verify the production-only offline install there.
- Restore that cache onto the image-build job.
- `RUN --mount=type=bind,source=<cache>,... <install> --offline` in the Dockerfile.

**A dependency directory is never an artifact.** `node_modules/`, `.venv/` and `vendor/` are
cache contents, not build output: artifacting them uploads tens of thousands of files per run
to deliver what the cache already holds. Compiled output — `dist/`, `target/*.jar`, a binary —
*is* a real artifact. Cache dependencies; artifact build output. The platform's snippets file
shows the wiring.

- Non-root `USER 10001:10001`; `COPY --chown=10001:10001`.
- Base image pinned by an explicit tag, never `:latest` and never untagged, with a
  `# renovate:` annotation on the line above so the bot can bump it. A `FROM ${VAR}`
  reference needs no tag of its own — CI resolves it from an organisation variable.
- Inverted default-deny `.dockerignore`: deny `**`, then allow only what the image needs.
- **Never `ARG` a secret.** Build args persist in image history even if a later layer deletes
  the file. Use a BuildKit secret mount when a build genuinely needs one.
- No compilers or `curl` in the final layer — they widen what an attacker can do after an RCE.

### 3.1 Layout: the `USER` bracket and instruction grouping

Every image is written as a **root setup phase closed by a drop to the runtime user**.

1. **`USER 0` immediately after the runtime stage's `FROM`.** The setup phase installs,
   creates directories and fixes ownership, and it needs root — so say so. Relying on
   whatever user the base image happens to leave behind makes the build depend on a
   base-image detail that can change under you. A **builder stage takes no `USER 0`**: it is
   discarded, so its user never ships, and hadolint `DL3002` ("last USER should not be
   root") is evaluated per stage and would fail the lint job that gates every consumer.
2. **`USER 10001:10001` closes the layer**, before the runtime instructions (`EXPOSE`,
   `CMD`/`ENTRYPOINT`). Everything after it runs unprivileged.

Between those two, group by instruction kind and separate groups with a blank line.
Consecutive instructions that form one unit — a run of `COPY`s bringing in source, a
single `RUN` doing install-and-chown — stay together with no blank line inside the group,
under one comment that says what the group is for.

```dockerfile
ARG NODE_JS_24_MICRO_BASE_IMAGE
FROM ${NODE_JS_24_MICRO_BASE_IMAGE}

USER 0

WORKDIR /app

ENV NODE_ENV=production \
    PORT=8080 \
    UPLOAD_DIR=/app/data/uploads

# Copy locked dependency manifests
COPY package.json package-lock.json ./

# Mount pre-warmed CI cache via Buildx, install production dependencies offline,
# create the uploads target and set ownership
RUN --mount=type=bind,source=.npm,target=/tmp/.npm,rw \
    npm ci --omit=dev --offline --no-audit --no-fund --cache /tmp/.npm && \
    mkdir -p /app/data/uploads && \
    chown -R 10001:10001 /app

# Copy application source code with non-root ownership
COPY --chown=10001:10001 *.js /app/
COPY --chown=10001:10001 db/ /app/db/
COPY --chown=10001:10001 middleware/ /app/middleware/
COPY --chown=10001:10001 routes/ /app/routes/
COPY --chown=10001:10001 public/ /app/public/

USER 10001:10001

EXPOSE 8080

CMD ["node", "server.js"]
```

The grouping is not cosmetic: a blank line inside a run of `COPY`s reads as "these are
unrelated", and the next person moves or drops one. Keeping the manifests separate from
the source copies is what makes the layer order — manifests, install, then source —
legible, and that order is what keeps the install layer cached when only source changes.

Copy source **after** the install, never before, or every source edit invalidates the
dependency layer.

**Merge consecutive `RUN`s.** Each one is a layer, and a layer carries whatever the previous
one left behind — an install layer followed by a separate `chown` layer ships both copies.
Chain them with `&& \` instead. hadolint `DL3059` flags this in CI.

**Group related `ARG`s** into one continued statement rather than a run of single lines. The
exception is a version pin: an `ARG` carrying a `# renovate:` annotation stays on its own
line, because the annotation binds to the line directly below it and grouping breaks the
bot.

### 3.2 Choosing the base image

Use the runtime image matching the project's language, and fall back to
`MICRO_ROOT_BASE_IMAGE` when no language image fits (a static binary, or a stack with no
dedicated micro image):

| Stack | Build arg |
|---|---|
| Java | `JAVA_25_MICRO_BASE_IMAGE` |
| Python | `PYTHON_312_MICRO_BASE_IMAGE` |
| Node.js service | `NODE_JS_24_MICRO_BASE_IMAGE` |
| Node.js SPA behind nginx | `NGINX_MICRO_BASE_IMAGE` |
| Go, or anything else | `MICRO_ROOT_BASE_IMAGE` |

CI injects each of these, so the Dockerfile declares the `ARG` and uses it — it pins nothing
itself, and bumping a base image is a change to one organisation variable.

**A runtime stage is never built `FROM` a build image.** `TOOLKIT_BUILD_IMAGE` is the one
build container — Go, JDK + Maven, Python, Node and buildah are all baked into it, so there
is no per-language build image to choose between. It carries every compiler, package manager
and credential helper that implies. Basing a runtime stage on it ships all of that to
production and undoes the point of a micro base. It belongs in a builder stage only — and
most images need no builder stage at all, because the pipeline already produced the
artifact.

```dockerfile
ARG TOOLKIT_BUILD_IMAGE \
    MICRO_ROOT_BASE_IMAGE

FROM ${TOOLKIT_BUILD_IMAGE} AS builder

WORKDIR /src

COPY . .

RUN make build

FROM ${MICRO_ROOT_BASE_IMAGE}

USER 0

WORKDIR /app

# Copy only the built artifact out of the builder stage
COPY --from=builder --chown=10001:10001 /src/bin/app /app/app

USER 10001:10001

EXPOSE 8080

CMD ["/app/app"]
```

### 3.3 Runtime instructions

**`EXPOSE` is required on a service image.** It is the image's only self-describing
contract, and the chart's `containerPort` cannot be checked against anything without it. A
CI runner image or a base image has no port and needs none.

**Prefer `CMD`.** It states the default command while leaving an operator free to override
it with `docker run <image> <cmd>`. An `ENTRYPOINT` can only be replaced with
`--entrypoint`, which is a worse interface for the same result.

Use `ENTRYPOINT` only to invoke a **pre-start shim** — a script that must substitute
configuration into the built artifact before the service starts, such as writing runtime
values into a compiled SPA bundle. Even then, prefer solving it at the `CMD` level if you
can.

A shim's last action decides whether it needs an init:

- If it **`exec`s** the service, the shim's process is replaced and the service is PID 1.
  Signals and child reaping work. Nothing further is needed, and this is the preferred form.
- If it **forks**, backgrounds work, or leaves children behind, the shim stays PID 1 and
  will not reap zombies or forward signals. Hand PID 1 to an init:
  `exec /usr/bin/dumb-init -- nginx -g "daemon off;"`.

## 4. Timeouts and caching

- Every job sets an explicit timeout. Platform defaults are generous enough that a hung job
  burns hours before anything intervenes.
- A cache keyed on a branch name or a mutable ref can be poisoned by whoever can push that
  ref. Key on a lockfile. Which lockfile each module uses is in the library's module
  catalog — do not restate it here.
