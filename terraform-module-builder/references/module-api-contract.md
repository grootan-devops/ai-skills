# Module API Contract & Specification

This document defines the authoritative API contract for all Terraform modules engineered under the `terraform-module-builder` skill. Every module is treated as a versioned, public software library.

---

## 1. Variable API Standards

Every public variable must provide a predictable, strongly-typed, and self-documenting interface.

### 1.1. Mandatory vs. Conditional Attributes

| Attribute | Status | Rules & Constraints |
|---|:---:|---|
| `description` | **Mandatory** | Must explain the purpose, operational impact, and expected behavior. Never leave empty or generic (e.g., "The name"). |
| `type` | **Mandatory** | Must use explicit type constraints (`string`, `number`, `bool`, `list(...)`, `map(...)`, `set(...)`, `object(...)`). Raw `any` is strictly prohibited except as a documented, isolated escape hatch. |
| `default` | **Conditional** | Required on optional variables. **Omitted on mandatory variables**. Never provide default passwords, tokens, API keys, or fake credentials (e.g., no `TemporaryDefaultPassword123!`). |
| `sensitive` | **Conditional** | Set to `true` **only** if the variable conveys secrets, credentials, or private keys. Do not set `sensitive = false` on non-sensitive variables. |
| `nullable` | **Conditional** | Explicitly set `nullable = false` when passing `null` would crash downstream expressions or cause unintended cloud defaults. Omit if `null` is a valid opt-out sentinel. |
| `ephemeral` | **Conditional** | Set to `true` **only** when data is temporary and consumed exclusively by write-only or ephemeral downstream resources (Terraform `>= 1.10.0`). Do not set `ephemeral = false` everywhere. |
| `validation` | **Conditional** | Add validation blocks **only** when enforcing genuine domain boundaries, format constraints, enums, or cross-argument logic that Terraform or the provider does not catch cleanly. |

### 1.2. Strongly-Typed Structural Objects

Group related configuration into strongly-typed objects using `optional(type, default)`. This eliminates loose, fragmented variables and avoids weak `map(string)` anti-patterns:

```hcl
variable "cloudwatch_logs" {
  description = "CloudWatch logging configuration for the resource."
  type = object({
    retention_in_days           = optional(number, 90)
    kms_key_arn                 = optional(string, null)
    deletion_protection_enabled = optional(bool, true)
    log_group_class             = optional(string, "STANDARD")
  })
  default = {}
}
```

### 1.3. Legitimate vs. Banned `lookup()` Usage

- **Banned**: Using `lookup()` to query static object attributes or bypass the type system.
  ```hcl
  # ANTI-PATTERN (BANNED):
  retention = lookup(var.logs, "retention_in_days", 90)
  ```
- **Allowed**: Accessing dynamic keys in open-ended runtime maps where keys are not known at authoring time:
  ```hcl
  # PERMITTED:
  custom_header_value = lookup(var.custom_headers, "X-Custom-Auth", null)
  ```
- **Fallback Chaining**: Use native HCL functions such as `coalesce()` for hierarchical defaults:
  ```hcl
  kms_key_id = coalesce(var.cloudwatch_logs.kms_key_arn, var.kms_key_arn)
  ```

---

## 2. Output API Standards

Outputs form the public consumption layer of the module. They must remain stable, minimal, and secure.

### 2.1. Output Design Rules
1. **Curate Stable Primitives & Structured Maps**: Export explicit identifiers, ARNs, endpoints, and well-typed summary maps:
   ```hcl
   output "id" {
     description = "The unique identifier of the provisioned resource."
     value       = aws_resource.this.id
   }

   output "arn" {
     description = "The Amazon Resource Name (ARN) of the provisioned resource."
     value       = aws_resource.this.arn
   }
   ```
2. **Avoid Full Resource Object Dumps**: Do **not** output `value = aws_resource.this` by default. Exposing raw provider schemas:
   - Couples module consumers to provider schema changes.
   - Increases accidental exposure of sensitive or computed internal attributes.
   - Restricts internal module refactoring without breaking callers.
3. **Sensitive Flags**: Set `sensitive = true` **only** when the exported value contains passwords, private keys, or generated secrets:
   ```hcl
   output "master_password" {
     description = "The generated database master password."
     value       = aws_db_instance.this.password
     sensitive   = true
   }
   ```
   Do not add `sensitive = false` to standard outputs.

---

## 3. Semantic Versioning & Public API Contract

Modules adhere strictly to [Semantic Versioning 2.0.0](https://semver.org/). Any change to the public API must be categorized as follows:

| Release Level | Trigger Conditions | Examples |
|:---:|---|---|
| **PATCH** (`x.y.Z`) | Bug fixes, documentation updates, internal implementation refactors that cause **zero** behavioral or state changes for callers. | - Correcting markdown typos in `README.md`<br>- Reformatting HCL with `terraform fmt`<br>- Internal local expression optimizations |
| **MINOR** (`x.Y.0`) | Backward-compatible feature additions, new optional variables with safe defaults, new outputs, or adding non-breaking resources. | - Adding an optional `tags` input<br>- Adding a new output `endpoint`<br>- Adding support for a new optional sub-feature with safe defaults |
| **MAJOR** (`X.0.0`) | Any breaking change to variables, outputs, defaults, provider version bounds, or resource addresses without state migrations. | - Renaming or removing a variable<br>- Changing a variable default that alters existing infrastructure<br>- Removing an output<br>- Altering resource addresses without a `moved` block<br>- Bumping minimum provider version to an incompatible major release |

---

## 4. Provider Versioning & Configuration Rules

### 4.1. Reusable Child Modules
- **Never Include Provider Configurations**: Reusable child modules must **never** declare `provider "aws" { ... }`, hardcode regions, or specify credentials. The caller/root module owns provider configuration.
- **Minimum Bounds Only**: In `versions.tf`, specify the minimum provider version required by features used:
  ```hcl
  terraform {
    required_version = ">= 1.5.0"

    required_providers {
      aws = {
        source  = "hashicorp/aws"
        version = ">= 6.0.0"
      }
    }
  }
  ```
  Do **not** enforce an upper bound (e.g. `< 7.0.0`) in reusable child modules, as this creates artificial dependency conflicts in consumer stacks.
- **Provider Aliases**: Declare `configuration_aliases` **only** when the module strictly requires secondary provider instances (e.g., multi-region replication):
  ```hcl
  required_providers {
    aws = {
      source                = "hashicorp/aws"
      version               = ">= 6.0.0"
      configuration_aliases = [aws.replica]
    }
  }
  ```
