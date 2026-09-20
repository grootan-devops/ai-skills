# Security Baselines Reference

See the full authoritative specification at:
[security-capability-matrix.md](./security-capability-matrix.md)

## Summary of Principles

1. **Capability-Aware Security**: Classify controls as `required`, `recommended`, `optional`, `provider_managed`, `not_supported`, or `not_applicable`.
2. **KMS / CMEK**: Mandatory for resources supporting encryption at rest (S3, RDS, DynamoDB, Secrets Manager). Do not mandate for resources lacking encryption support.
3. **TLS & In-Transit Protection**: Minimum TLS 1.2+ enforced on load balancers, object storage, and databases.
4. **Secret Hygiene**: Zero default passwords. Support write-only arguments (`_wo`) on Terraform `>= 1.11.0` or native IAM database authentication.
