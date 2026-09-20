# Multi-Cloud Security Capability Matrix & Baseline

This document defines the capability-aware security model enforced across all generated Terraform modules. The skill avoids blunt, universal mandates (e.g. "every resource must use KMS") and instead classifies security controls according to target cloud provider capabilities.

---

## 1. Security Control Taxonomy

For every security domain, each resource category maps to one of six explicit capability statuses:

| Status | Definition | Generator Action |
|---|---|---|
| `required` | Security control is natively supported and non-negotiable for enterprise compliance. | Module strictly enforces control with production defaults. Fails if bypassed without governance override. |
| `recommended` | Control is best practice, but operational or cost trade-offs exist (e.g. NAT gateways, high-retention logs). | Configured with secure default; caller may override via parameter. |
| `optional` | Control is advanced or niche (e.g. customer BYO-IP, replication). | Exposed as an opt-in sub-block or disabled by default. |
| `provider_managed` | Cloud provider handles control automatically by default (e.g. GCP default encryption, Azure storage encryption). | Do not inject redundant or deprecated configuration blocks. |
| `not_supported` | Target cloud resource does not support this control in its provider schema. | Do not invent synthetic parameters or non-existent arguments. |
| `not_applicable` | Control is semantically irrelevant for the resource (e.g. KMS encryption for IAM roles or route tables). | Omit control completely. |

---

## 2. Multi-Cloud Capability Matrices

### 2.1. Amazon Web Services (AWS)

| Resource Category | Encryption at Rest (CMEK) | In-Transit TLS (>=1.2) | Access / Audit Logging | Deletion Protection | Public Access Boundary |
|---|:---:|:---:|:---:|:---:|:---:|
| **Storage (`aws_s3_bucket`)** | `required` (KMS CMK) | `required` (Policy Deny) | `recommended` (Access Logs) | `optional` (Object Lock) | `required` (Public Access Block) |
| **Relational DB (`aws_db_instance`)** | `required` (KMS CMK) | `required` (`rds.force_ssl=1`)| `required` (CloudWatch exports) | `required` (`deletion_protection=true`) | `required` (`publicly_accessible=false`) |
| **NoSQL (`aws_dynamodb_table`)** | `required` (KMS CMK) | `provider_managed` | `recommended` (CloudWatch metrics)| `recommended` (Point-in-Time Recovery) | `not_applicable` (VPC endpoint / IAM) |
| **Compute (`aws_ecs_cluster`)** | `required` (CloudWatch KMS) | `not_applicable` | `required` (Container log groups) | `not_supported` | `required` (Private subnets only) |
| **Compute (`aws_lambda_function`)**| `required` (KMS CMK) | `provider_managed` | `required` (KMS Log Group) | `not_supported` | `required` (VPC private attachment) |
| **Network (`aws_vpc`)** | `not_applicable` | `not_applicable` | `required` (KMS VPC Flow Logs) | `not_applicable` | `required` (Private/Intra subnets) |
| **Load Balancer (`aws_lb`)** | `not_applicable` | `required` (TLS 1.3 / 1.2 Policy)| `recommended` (S3 Access Logs) | `recommended` (`deletion_protection=true`) | `optional` (`internal = true/false`) |
| **Security (`aws_kms_key`)** | `required` (Annual rotation) | `not_applicable` | `recommended` (CloudTrail) | `required` (`deletion_window=30`) | `required` (Explicit Key Policy) |
| **IAM (`aws_iam_role`)** | `not_applicable` | `not_applicable` | `provider_managed` | `not_applicable` | `not_applicable` (Least privilege) |

---

### 2.2. Microsoft Azure (`azurerm`)

| Resource Category | Encryption at Rest (CMEK) | In-Transit TLS (>=1.2) | Access / Audit Logging | Deletion Protection | Public Access Boundary |
|---|:---:|:---:|:---:|:---:|:---:|
| **Storage (`azurerm_storage_account`)** | `required` (Key Vault Key) | `required` (`min_tls_version="TLS1_2"`) | `required` (Diagnostic settings) | `recommended` (Management locks) | `required` (`public_network_access_enabled=false`) |
| **Database (`azurerm_postgresql_flexible_server`)** | `required` (Key Vault Key) | `required` (`ssl_enforcement="Enabled"`) | `required` (Audit logs) | `not_supported` (Use Resource Lock) | `required` (Private endpoint / VNet delegation) |
| **Network (`azurerm_virtual_network`)** | `not_applicable` | `not_applicable` | `required` (Network Watcher flow logs)| `not_applicable` | `required` (Private subnets / NSG deny) |
| **Key Vault (`azurerm_key_vault`)** | `required` (Soft delete + purge protection)| `required` | `required` (Diagnostic logs) | `required` (`purge_protection_enabled=true`) | `required` (`public_network_access_enabled=false`) |

---

### 2.3. Google Cloud Platform (GCP - `google`)

| Resource Category | Encryption at Rest (CMEK) | In-Transit TLS (>=1.2) | Access / Audit Logging | Deletion Protection | Public Access Boundary |
|---|:---:|:---:|:---:|:---:|:---:|
| **Storage (`google_storage_bucket`)** | `required` (Cloud KMS CryptoKey) | `provider_managed` | `recommended` (Access logs) | `recommended` (Retention policy) | `required` (`uniform_bucket_level_access=true`) |
| **Database (`google_sql_database_instance`)** | `required` (Cloud KMS CMEK) | `required` (`require_ssl=true`) | `required` (Audit logs) | `required` (`deletion_protection=true`) | `required` (`ipv4_enabled=false`, private IP only) |
| **Network (`google_compute_network`)** | `not_applicable` | `not_applicable` | `required` (VPC Flow Logs) | `not_applicable` | `required` (Private Google Access enabled) |

---

## 3. Sensitive Data & Credential Governance

### 3.1. Cleartext Password Prohibition
- **Never Generate Default Passwords**: Modules must **never** specify fallback passwords, API tokens, or keys in `variables.tf` defaults.
- **State File Boundary**: `sensitive = true` only masks values in CLI output, logs, and plan diffs; it does **not** prevent cleartext storage in `terraform.tfstate`.
- **Preferred Patterns**:
  1. **Write-Only Arguments**: Where the provider supports it (e.g. `password_wo`, `secret_string_wo`), use write-only fields to avoid storing secrets in state. Requires Terraform `>= 1.11.0`.
  2. **External Secrets Management**: Avoid passing raw credentials into Terraform. Have the database or service generate credentials dynamically or reference an existing Secrets Manager secret.
  3. **IAM Authentication**: Prefer native IAM/managed identity database authentication (`iam_database_authentication_enabled = true`) over static username/password pairs.

### 3.2. IAM Least-Privilege Rules
- **No Wildcard Actions**: Disallow `Action = ["*"]` and broad prefix wildcards (e.g. `s3:*`, `kms:*`) unless strictly mandated by cloud provider documentation (e.g., KMS root account delegation).
- **No Wildcard Resources**: Disallow `Resource = ["*"]` on action statements that can be scoped to specific resource ARNs.
- **Composition**: Prefer `data "aws_iam_policy_document"` for deterministic policy document generation.
