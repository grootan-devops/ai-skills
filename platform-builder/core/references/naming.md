# Environment Naming

A new environment's names come from the GitOps repository, not from the library's examples.
Existing environments keep the names their `Chart.yaml` and `values.yaml` already have; the
skill reads them and never renames anything.

## Cluster label

Use the first of these that exists, and show the user both the result and where it came from:

1. **The cluster label the GitOps repository's default-branch README records,** in the form
   the library's root Application guide documents.
2. **The repository's own name**, the last segment of its URL without `.git`. This applies
   unless the name is generic (`gitops`, `argocd`, `deploy`, …) or not DNS-safe.
3. **Otherwise ask the user.**

The label prefixes the chart name. Leave the library's optional `cluster` value unset, so
generated names carry the label once.

## Derived names

| Name | Rule | Example (label `edge-east`) |
| --- | --- | --- |
| Chart (root and `extras/`) | `<label>-<product>` | `edge-east-oneuptime` |
| Branch | `<product>/<env>`, or `<product>` without an environment | `oneuptime/dev` |
| Default namespace | `<product>-<env>` | `oneuptime-dev` |
| Root Application | `<chart>-<env>-root`, or `<chart>-root` without an environment | `edge-east-oneuptime-dev-root` |

Child Application names are built by the library from the chart name, the app keys and the
environment. Read them from `helm template`, never by hand.

## Argo CD destination

- Copy the destination from a sibling branch.
- If the README records an Argo CD cluster name, the destination must match it.
- Check the destination exists with `argocd cluster list` before activation.

## Script

```bash
python3 core/scripts/naming.py --repo-url <gitops-url> --readme <default-branch README.md> \
  --product <product> --env <env>
```

It prints every name above and the label's source. It exits 2 when the label has to be asked.
