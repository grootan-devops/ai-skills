# Naming Conventions Reference

See the full authoritative specification at:
[naming-standards.md](./naming-standards.md)

### Summary of Rules
1. **Formula**: `${var.application}-${var.environment}-${var.name}` (fallback to `${var.application}-${var.environment}`).
2. **Project Neutrality**: Zero occurrences of company or project branding (`Plainr`, `takween`, `xyz`).
3. **Length Limits**: Truncate ALB resources to 32 characters using stable 4-character hashes. S3 buckets adhere to 63-character lowercase global namespace rules.
4. **Tag Governance**: Merge consumer tags first, reserved governance tags last (`merge(var.tags, local.governance_tags)`).
