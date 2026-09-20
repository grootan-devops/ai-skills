# State Migration & Refactoring Safety Guide

This document defines the protocols for refactoring existing Terraform modules, renaming resources, decomposing monolithic configurations, and migrating state addresses without causing destructive resource recreations.

---

## 1. The Zero-Destruction Refactor Rule

**Rule**: No internal refactoring of a module may ever force a destroy-and-recreate cycle on existing stateful resources (databases, storage, networks, keys, compute clusters) unless explicitly declared as a breaking major release with an approved migration plan.

---

## 2. Using `moved` Blocks in `moved.tf`

Terraform 1.1+ provides first-class `moved` blocks to record resource address changes directly in module source code. This eliminates the need for consumers to manually run `terraform state mv`.

### 2.1. Resource Renaming Migration

When standardizing resource labels (e.g. changing `aws_s3_bucket.main` to `aws_s3_bucket.this`):

```hcl
# moved.tf

moved {
  from = aws_s3_bucket.main
  to   = aws_s3_bucket.this
}
```

### 2.2. Migrating from `count` or List Indexing to `for_each`

When fixing unstable integer-indexed subnets (e.g. in VPC):

```hcl
# moved.tf

moved {
  from = aws_subnet.private[0]
  to   = aws_subnet.private["us-west-2a/private"]
}

moved {
  from = aws_subnet.private[1]
  to   = aws_subnet.private["us-west-2b/private"]
}

moved {
  from = aws_subnet.private[2]
  to   = aws_subnet.private["us-west-2c/private"]
}
```

### 2.3. Decomposing into Child Modules

When extracting inline resources into a nested submodule:

```hcl
# moved.tf

moved {
  from = aws_security_group.alb
  to   = module.security_group.aws_security_group.this
}
```

---

## 3. Historical `moved` Block Retention

- **Never Delete Historical `moved` Blocks**: Once a `moved` block is released, it must remain in `moved.tf` permanently across future minor and patch releases.
- **Consumer Staggering**: Module consumers upgrade at different cadences. Retaining historical `moved` blocks ensures that a consumer skipping from `v1.0.0` directly to `v1.4.0` migrates state seamlessly without corruption.

---

## 4. Pre-Refactor Plan Verification Protocol

Before merging or publishing any module refactoring, the engineer/skill must execute:

```bash
# 1. Generate plan using realistic fixture
terraform -chdir=examples/complete plan -out=refactor.tfplan

# 2. Inspect plan JSON for destructive actions
terraform -chdir=examples/complete show -json refactor.tfplan > /tmp/plan.json

# 3. Assert zero delete actions
jq '.resource_changes[] | select(.change.actions[] == "delete")' /tmp/plan.json
```

If any unexpected `delete` action is detected, the refactor is immediately halted and the missing `moved` block is identified.
