# Detecting State Migrations

The reference repository's `MIGRATION.md` records actions for released versions; it does
not currently define a general moved-block or plan-review procedure. Use this guide for
that procedure, then document the exact consumer actions in `MIGRATION.md` if a release
needs them. A moved block can preserve an address, but no static checker can guarantee
that a provider upgrade or configuration change is free of replacement or destruction.

This guide covers the state decisions that static module checks cannot make.

---

## 1. Diff the Working Tree Against Git

```bash
python3 scripts/detect-migrations.py <module_path> [--compare-ref HEAD]
```

The script extracts variables, outputs, and top-level resource declarations from the
selected Git ref and working tree, regardless of the caller's working directory. It reports
removed variables, outputs, and uncovered resource addresses as candidates for a MAJOR
change. Its no-change result is only a PATCH candidate: it does not evaluate changed
default values, Terraform instance keys, provider behavior, or lifecycle settings.

Its resource extraction is textual, so treat the output as a candidate list rather than a
verdict. Two cases it will not resolve on its own:

- **A rename that looks like a delete plus an add.** The script sees `aws_s3_bucket.main`
  disappear and `aws_s3_bucket.this` appear; deciding that they are the same bucket is your
  judgement. Add a moved block only after confirming that mapping against state.
- **A re-key rather than a rename.** Converting `count` to `for_each` keeps the resource
  type and label and changes only the index, so one `moved` block per existing key is
  needed — and the keys come from the consumer's current state, not from the source.

## 2. Scaffold the Block

```bash
python3 scripts/detect-migrations.py <module_path> --scaffold-move <FROM> <TO>
```

Appends to `moved.tf`, creating it if absent, and is a no-op if that exact pair is already
recorded. One invocation per address pair.

## 3. Gate the Release

Before publishing a risky change, use a representative consumer configuration and state
to create a saved Terraform plan. Inspect the human-readable plan and
`terraform show -json <plan-file>`. Review every `resource_changes[].change.actions`
containing `delete`, its `replace_paths`, and any `previous_address` from a move.
Keep the plan file out of Git; it may contain sensitive values. Investigate every
replacement or deletion. Document the verified mapping or required consumer action in
the release's `MIGRATION.md` entry.
A moved block addresses only an address change; changed defaults and provider behavior
may still replace an object. Do not claim zero destruction from this script or a green
module test.

An add or update ends after local edits and review unless the user separately requests
apply. For such a request, establish the exact account, workspace, and environment,
show the saved plan and its replacement/deletion effects, and obtain confirmation of
that exact plan before applying the saved file. Re-plan and review again if configuration
or state changes. Destroy and direct state manipulation need their own request and review.
