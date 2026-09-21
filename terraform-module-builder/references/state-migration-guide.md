# Detecting State Migrations

The zero-destruction guarantee, the `moved` block shapes, the permanent-retention rule, and
the plan assertion a consumer runs are the **library's** contract: read `MIGRATION.md` in
the reference repository at the ref this run resolved, and add to it rather than restating
it here.

This file covers the half the library cannot automate: finding the address change before it
ships.

---

## 1. Diff the Working Tree Against Git

```bash
python3 scripts/detect-migrations.py <module_path> [--compare-ref HEAD]
```

The script extracts variables, outputs, and resource addresses from the committed ref and
from the working tree, and reports what changed as a SemVer classification — MAJOR on a
removed variable, output, or resource address; MINOR on an addition; PATCH when the public
surface is unchanged.

Its resource extraction is textual, so treat the output as a candidate list rather than a
verdict. Two cases it will not resolve on its own:

- **A rename that looks like a delete plus an add.** The script sees `aws_s3_bucket.main`
  disappear and `aws_s3_bucket.this` appear; deciding that they are the same bucket is your
  judgement, not its output.
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

Before publishing, run the consumer procedure from the library's `MIGRATION.md` against a
realistic fixture and confirm it reports no deletions. A refactor that cannot pass that
check is not ready, and `terraform state mv` is not the remedy — the missing `moved` block
is.
