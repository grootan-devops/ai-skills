# Module README Specification & Template

This document defines the canonical documentation standard for all Terraform modules. The standard establishes **baseline mandatory content** while remaining flexible to accommodate upgrade guides, limitations, and operational runbooks.

---

## 1. Core Structural Philosophy

- **Mandatory Minimum Content**: Every module README must contain the eight core sections detailed below.
- **Extensible Architecture**: Modules requiring deeper documentation (e.g. migration notes, complex lifecycle ownership, or troubleshooting) must append dedicated sections rather than omitting context.
- **Separation of Concerns**: Narrative architectural rationale and security models are authored manually; API specification tables (Requirements, Inputs, Outputs) are kept in dedicated sections that automated tools can refresh without clobbering explanations.

---

## 2. Canonical Markdown Blueprint

```markdown
# <Provider> <Resource Name> Module

<Concise narrative paragraph explaining what the module provisions, its primary operational responsibilities, and its target enterprise deployment role. Avoid marketing fluff or project-specific branding.>

### Architecture & Managed Resources
- `<provider>_<resource>.<identifier>`: <Concise description of the resource role>.
- `<provider>_<resource>.<identifier>`: <Concise description of the resource role>.

### Security & Compliance Guardrails
- **<Control Title>**: <Concise explanation of the enforced baseline (e.g. Mandatory KMS CMK, TLS 1.2+ denial policy, private subnet isolation)>.
- **<Control Title>**: <Concise explanation of the enforced baseline>.

---

## Requirements & Providers

| Requirement | Version |
|---|---|
| `terraform` | `>= <min_version>` |
| `<provider>` | `>= <min_provider_version>` |

---

## Usage Examples

### Minimal Working Example
```hcl
module "<module_name>" {
  source = "<source_path_or_registry_url>"

  application = "core"
  environment = "prod"
  name        = "primary"
  <required_argument> = "<value>"
}
```

### Complete Production Example
```hcl
module "<module_name>" {
  source = "<source_path_or_registry_url>"

  application = "enterprise"
  environment = "prod"
  name        = "production-stack"

  <optional_complex_object> = {
    enabled           = true
    retention_in_days = 90
  }

  tags = {
    CostCenter = "Operations"
  }
}
```

---

## Inputs Specification

| Name | Description | Type | Default | Required |
|---|---|---|---|:---:|
| `<name>` | <Clear description of input purpose> | `<type>` | `**Required**` | Yes |
| `<name>` | <Clear description of input purpose> | `<type>` | `<default_value>` | No |

---

## Outputs Specification

| Name | Description | Sensitive |
|---|---|:---:|
| `<name>` | <Clear description of exported value> | No |
| `<name>` | <Clear description of exported value> | Yes |

---

## Additional Permitted Sections (When Applicable)

### Migration & Upgrade Notes
Document any state refactors, `moved` blocks, or breaking changes between major versions.

### Ownership Boundaries & Lifecycle Assumptions
Document external systems responsible for secret rotation, deployment artifacts, or out-of-band updates.

### Limitations & Known Caveats
Document cloud provider quota limits, regional availability restrictions, or IAM permission prerequisites.
```

---

## 3. Table Formatting Standards

### 3.1. Inputs Table
- **Mandatory Variables**: Set `Default` column to `**Required**` and `Required` column to `Yes`.
- **Optional Variables**: Set `Default` column to the literal default (e.g. `null`, `true`, `"{}"`, `"[]"`) and `Required` column to `No`.
- **Complex Object Types**: Represent structural types compactly (e.g. `object({...})`, `list(object({...}))`).

### 3.2. Outputs Table
- **Sensitive Column**: Must display `Yes` if marked sensitive, `No` if non-sensitive.
