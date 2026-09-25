# Detecting State Migrations

The `moved` block shapes, retention rule, and consumer plan procedure are in the reference
repository's `MIGRATION.md` at the resolved ref. A moved block can preserve an address, but
no static checker can guarantee that a provider upgrade or configuration change is free of
replacement or destruction. Verify the resulting plan against representative consumer state.

This file covers the half the library cannot automate: finding the address change before it
ships.

---

## 1. Diff the Working Tree Against Git

```bash
python3 scripts/detect-migrations.py <module_path> [--compare-ref HEAD]
```

The script extracts variables, outputs, and top-level resource declarations from the
selected Git ref and working tree, regardless of the caller's working directory. It reports
removed variables, outputs, and uncovered resource addresses as candidates for a MAJOR
change. It does not evaluate changed default values or Terraform instance keys.

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

Before publishing, run the consumer procedure from the library's `MIGRATION.md` against a
realistic fixture. Investigate every planned replacement or deletion; a moved block addresses
only an address change. Do not claim zero destruction from this script or a green module test.
