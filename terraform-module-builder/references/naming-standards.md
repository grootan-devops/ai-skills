# Naming & Tagging Standards Specification

This document defines the naming conventions, length constraints, deterministic truncation algorithms, and tagging governance applied across all modules.

---

## 1. Deterministic Naming Formula

All modules implement standard resource naming via `locals.tf`:

```hcl
locals {
  rendered_name = var.name != null && var.name != "" ? "${var.application}-${var.environment}-${var.name}" : "${var.application}-${var.environment}"
}
```

### 1.1. Absolute Project & Brand Neutrality
- **No Hardcoded Project Names**: No project names, company names, or internal brand labels (e.g., `Plainr`, `takween`, `xyz`, customer identifiers) may ever appear in resource names, locals, defaults, tags, or documentation.
- **Dynamic Parameterization**: All naming must flow through `var.application`, `var.environment`, and `var.name`. If a developer mentions an internal brand in a prompt, the skill must abstract it into generic variables.

---

## 2. Cloud-Specific Naming Bounds & Truncation

Different cloud resources enforce strict length and character set limits. Modules must never apply blind string concatenation or naive truncation that causes naming collisions.

### 2.1. Provider Resource Constraints Matrix

| Provider | Resource Type | Length Limit | Valid Characters | Renaming Behavior | Collision Mitigation Strategy |
|---|---|:---:|---|:---:|---|
| **AWS** | `aws_lb` (ALB/NLB) | 1-32 chars | `^[a-zA-Z0-9-]+$` (no leading/trailing hyphens) | Destructive Recreate | Truncate prefix to 27 chars + `-` + 4-char MD5 hash of full name. |
| **AWS** | `aws_lb_target_group` | 1-32 chars | `^[a-zA-Z0-9-]+$` | Destructive Recreate | Truncate prefix to 27 chars + `-` + 4-char MD5 hash. |
| **AWS** | `aws_s3_bucket` | 3-63 chars | Lowercase letters, numbers, hyphens, periods | Destructive Recreate | Global namespace: allow `bucket_name_override` or append deterministic account/region hash. |
| **AWS** | `aws_iam_role` | 1-64 chars | `^[a-zA-Z0-9+=,.@_-]+$` | Destructive Recreate | Truncate prefix to 58 chars + 5-char hash. |
| **AWS** | `aws_db_instance` | 1-63 chars | Lowercase letters, numbers, hyphens | Destructive Recreate | Truncate prefix to 57 chars + 5-char hash. |
| **AWS** | `aws_kms_key` (Alias) | 1-256 chars | `^[a-zA-Z0-9:/_-]+$` (prefixed with `alias/`) | Non-destructive | Native alias naming without truncation. |
| **Azure**| `azurerm_storage_account` | 3-24 chars | Lowercase alphanumeric only (no hyphens) | Destructive Recreate | Strip hyphens, lowercase, truncate to 18 chars + 6-char hash. |
| **Azure**| `azurerm_virtual_network` | 2-64 chars | Alphanumeric, underscores, hyphens, periods | Destructive Recreate | Truncate prefix to 58 chars + 5-char hash. |
| **GCP**  | `google_compute_network` | 1-63 chars | Lowercase letters, numbers, hyphens | Destructive Recreate | Truncate prefix to 57 chars + 5-char hash. |

### 2.2. Deterministic Truncation Algorithm

When a resource name exceeds its cloud length limit, the module must apply deterministic truncation preserving a human-readable prefix while guaranteeing uniqueness via a stable hash:

```hcl
locals {
  # Example for 32-character ALB limit:
  alb_raw_name       = local.rendered_name
  alb_name_hash      = substr(md5(local.alb_raw_name), 0, 4)
  alb_truncated_name = length(local.alb_raw_name) > 32 ? "${substr(local.alb_raw_name, 0, 27)}-${local.alb_name_hash}" : local.alb_raw_name
}
```

---

## 3. Tagging Governance & Precedence Contract

### 3.1. Reserved Governance Tags
The following standard tags are reserved for organizational compliance, cost allocation (FinOps), and auditing:
- `Application`: Product or system name (`var.application`).
- `Environment`: Target deployment tier (`var.environment`, validated via regex `^[a-z0-9-]+$`).
- `Name`: Resolved resource identifier (`local.rendered_name` or sub-resource name).
- `ManagedBy`: Always hardcoded to `"Terraform"`.

### 3.2. Merge Precedence
To prevent callers from accidentally or maliciously overriding reserved governance tags, **consumer tags are merged first and reserved tags are merged last**:

```hcl
locals {
  governance_tags = {
    Application = var.application
    Environment = var.environment
    Name        = local.rendered_name
    ManagedBy   = "Terraform"
  }

  # Consumer tags can add metadata, but CANNOT clobber governance tags:
  tags = merge(var.tags, local.governance_tags)
}
```

If a specific sub-resource has a distinct role (e.g. public vs private subnet), append the sub-resource name to the base tags:
```hcl
tags = merge(local.tags, { Name = "${local.rendered_name}-public-${each.key}" })
```
Do **not** force tags onto cloud resources that do not support tagging in the provider schema.
