# Reference Repository Mapping & Ground Truth

This document links the `terraform-module-builder` skill to the reference repository that holds
the ground-truth modules.

## Locating the reference repository

The repository is supplied by the operator; the skill hardcodes no path. Resolve it in this
order, and stop at the first that exists:

1. `$TERRAFORM_MODULES_REPO`, if set.
2. A `terraform-modules/` checkout beside the directory this skill was installed into.
3. Ask the user for the path. Never guess, and never carry on without it — the module catalog is
   the ground truth for existing patterns.

Once resolved, two paths matter:

| What | Path within the repository |
|---|---|
| Module catalog | `modules/` |
| Catalog documentation | `docs/AWS.md` (and the sibling per-provider docs) |

## Module Layout

Modules in the reference repository are partitioned by provider and domain under `modules/`:

```text
modules/
└── aws/
    ├── compute/       # batch, ecs, eks, lambda
    ├── database/      # dynamodb, elasticache/valkey, rds/postgres, rds/proxy
    ├── integration/   # api-gateway, eventbridge, sqs, step-functions
    ├── network/       # alb, cloudfront, vpc, waf
    ├── security/      # cognito, kms, secrets-manager
    └── storage/       # amplify, efs, s3
```

## Architectural Guidelines

When inspecting existing modules for code patterns:

1. Observe file decomposition (`<service>.tf`, `security.tf`, `variables.tf`, `outputs.tf`, `locals.tf`, `data.tf`, `versions.tf`).
2. Verify patterns against official Terraform documentation and `terraform providers schema -json` rather than assuming local code is 100% bug-free.
3. Observe known remediation requirements documented in `references/state-migration-guide.md` and `references/security-capability-matrix.md`.
