# Service Bootstrap (`argocd app add`)

Use this reference to add a service to an environment that already exists (see
[`argocd-environment.md`](./argocd-environment.md) for creating one). How the library reads an
`apps` entry — values-file paths, Application names, release, namespace and sync defaults — and
what an environment values file may override are in the library docs: follow the README index
task for adding a service, at the resolved ref, before anything else.

## Ask before writing

Suggest from the environment's existing entries, confirm, and use what the user says:

- the environment checkout and branch;
- the entry key, and whether it is one release or one of several releases of a chart (the
  group key and each release name);
- the chart source: registry URL, chart name and a **published** version — confirm it with
  `helm show chart oci://<registry>/<name> --version <version>` or
  `helm show chart <name> --repo <url> --version <version>` — or the chart's path in the
  repository;
- the namespace, the release name and the sync policy, when they differ from what the
  environment's other entries use;
- the environment values: domain, ingress class, TLS secret, route host, image repository and
  tag policy, resources, and the application settings (databases, identity, service URLs).
  Never copy them from another environment or project.

## Add it

```bash
python3 scripts/app_add.py <root> --app <key> [--group <group>] \
  --chart-repo <url> --chart-name <name> --chart-version <version> \
  --service-values <service chart dir or pulled copy> [-f <chart dir>/values.<release>.yaml] \
  [--namespace <ns>] [--release-name <name>] [--sync auto|manual] --dry-run
```

Run it with `--dry-run` first and show the result, then without it. The script refuses an
existing entry or values file, inserts only the new entry, scaffolds the values file with
`<ask: …>` placeholders, and prints the Application name, namespace, release name, values path
and the yq path a CI deploy job bumps (`.apps.<key>`, or a union for every release of one
chart).

Fill every placeholder from the user's answers — none may remain — then check the file:

```bash
python3 scripts/override_check.py <root>/values/<key>.yaml <service chart dir> [-f <overlay>]
```

- Credentials go through the environment's secret mechanism; never inline them in a command or
  print them, and flag plaintext secrets already in values files.
- Keep resources as the user sets them; never raise a value the user lowered.
- Prerequisites outside the chart — the namespace (when sync does not create it), a pull
  secret, a database, identity-provider clients and redirect URIs, DNS and TLS — are listed for
  the user. Create a database only when asked, the way the environment already does it, with
  the Kubernetes context the user named; never switch contexts.

## Validate and report

1. `helm dependency build <root>` and `helm template <root>`, or `app_add.py … --verify` once the
   library is vendored. The new Application must carry the expected name, destination
   namespace, chart, version, release name and a non-empty `helm.values`.
2. Report the files changed, the Application name, namespace and release name, the CI yq path
   and the prerequisites.
3. Stop there. Commit, push and sync each need an explicit request; after a push the root
   Application syncs first, then the new child. Removing an entry deletes its workloads when
   pruning is on — do it only on an explicit request, listing what will be deleted.
