# Security Capability Classification

The six-status capability taxonomy (`required`, `recommended`, `optional`,
`provider_managed`, `not_supported`, `not_applicable`), the credential-handling rules, and
the IAM least-privilege rules are the **library's** contract: see §6 *Security Baselines* in
the reference repository's `README.md`. The resolved AWS matrix — which status each control
takes on each service — is in that repository's `docs/AWS.md`. Read both at the ref this run
resolved; do not restate them here.

What follows is how to *derive* a status for a resource the matrix does not yet cover.

---

## 1. Deriving a Status From the Schema

Classification is a schema question before it is a policy question. For each control —
encryption at rest, in-transit TLS, audit logging, deletion protection, public-access
boundary — look the argument up in `terraform providers schema -json`:

1. **Absent from the schema** → `not_supported`. Never invent an argument. A variable that
   maps to nothing is worse than an omission: it reads as a control that is switched on.
2. **Present, and the cloud applies it with no configuration** → `provider_managed`. Adding
   the block changes nothing and dates the module against provider defaults.
3. **Present, and the resource holds customer data at rest or in transit** → `required`.
4. **Present, with a material cost or operational trade-off** (NAT gateways, long log
   retention, Multi-AZ) → `recommended`: secure default, caller may override.
5. **Present, and niche** (BYO-IP, cross-region replication, object lock) → `optional`,
   off by default.
6. **Present but semantically meaningless** — KMS on an IAM role, a public-access boundary
   on a route table → `not_applicable`.

The distinction that matters most is 1 versus 6. Both end in "omit the control", but
`not_supported` is a provider gap that may close in a later release and is worth a comment
in the module; `not_applicable` never will.

---

## 2. Clouds the Library Does Not Yet Cover

The reference repository ships AWS modules only. When generating for Azure or GCP there is
no resolved matrix to read, so classify from the schema using §1 above. These rows are a
starting point:

### Azure (`azurerm`)

| Resource | CMEK at rest | TLS ≥ 1.2 | Audit logging | Deletion protection | Public boundary |
| --- | :---: | :---: | :---: | :---: | :---: |
| `azurerm_storage_account` | `required` (Key Vault) | `required` (`min_tls_version`) | `required` (diagnostics) | `recommended` (management lock) | `required` (`public_network_access_enabled=false`) |
| `azurerm_postgresql_flexible_server` | `required` (Key Vault) | `required` (SSL enforcement) | `required` (audit logs) | `not_supported` (use a resource lock) | `required` (VNet delegation) |
| `azurerm_key_vault` | `required` (soft delete + purge protection) | `required` | `required` (diagnostics) | `required` (`purge_protection_enabled=true`) | `required` |
| `azurerm_virtual_network` | `not_applicable` | `not_applicable` | `required` (Network Watcher) | `not_applicable` | `required` (NSG deny) |

### GCP (`google`)

| Resource | CMEK at rest | TLS ≥ 1.2 | Audit logging | Deletion protection | Public boundary |
| --- | :---: | :---: | :---: | :---: | :---: |
| `google_storage_bucket` | `required` (Cloud KMS) | `provider_managed` | `recommended` | `recommended` (retention) | `required` (`uniform_bucket_level_access`) |
| `google_sql_database_instance` | `required` (Cloud KMS) | `required` (`require_ssl`) | `required` | `required` (`deletion_protection`) | `required` (`ipv4_enabled=false`) |
| `google_compute_network` | `not_applicable` | `not_applicable` | `required` (flow logs) | `not_applicable` | `required` (Private Google Access) |

Confirm every row against the live schema before generating from it.
