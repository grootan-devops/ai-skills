# Contribution and Reporting Policy

These repositories are published for use and adaptation under
[AGPL-3.0-only](./LICENSE.md). Grootan Technologies Pvt Ltd maintains them for
its own platform and customer delivery needs.

## External contributions

We do not accept external pull requests, feature requests, GitHub issues,
discussions, or support requests. Public forks are welcome when used and
distributed in accordance with the license.

Ordinary bugs and suspected security vulnerabilities may be reported by email
to `platform-engineering@grootan.com`. A report does not guarantee
investigation, response, remediation, or inclusion in a future release.

Never include credentials, access tokens, private keys, customer information,
personal data, or live exploit payloads. Sanitize all logs and evidence.

## Bug report email

**To:** `platform-engineering@grootan.com`  
**Subject:** `[BUG][repository-name] Short summary`

Copy and complete this body:

```text
Report type: Bug
Repository:
Affected version, tag, or commit:
Affected component and exact location:

Summary:

Environment:
- Operating system:
- Runtime/tool versions:
- Relevant dependency versions:

Steps to reproduce:
1.
2.
3.

Minimal reproducible example:

Observed behavior:

Expected behavior:

Impact and affected users or systems:

Suggested severity: Low / Medium / High / Critical

Sanitized logs, screenshots, or other evidence:

Known workaround:

Reporter name:
Reporter email:
Preferred contact method:

I confirm that this report contains no credentials, customer data, personal
data, private keys, access tokens, or unsafe live exploit payloads.
```

## Security report email

Do not report vulnerabilities through a public channel.

**To:** `platform-engineering@grootan.com`  
**Subject:** `[SECURITY][repository-name] Confidential: short summary`

Use the confidential report template and disclosure guidance in
[SECURITY.md](./SECURITY.md).
