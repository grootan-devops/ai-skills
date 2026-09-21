# Naming, Tagging & Neutrality — Authoring Rules

The naming formula, the AWS length limits, the deterministic truncation algorithm, the
reserved governance tags and their merge precedence are the **library's** contract. Read
them in the reference repository's `README.md`, §5 *Naming & Tagging Standards*, at the ref
this run resolved — and note the "current state" callouts there, which record where the
shipped modules diverge from the standard. Do not restate that section here; a second copy
drifts the moment the library changes.

What follows is what the skill must do that the library cannot state for itself.

---

## 1. Abstracting Brand Names Out of a Prompt

The library forbids project, company, and customer labels in module code. That covers the
artifact. It does not cover the request that produced it.

When a user says *"build the Plainr document bucket"*, the module is never `plainr-docs`.
Lift every identifier in the prompt into `var.application`, `var.environment`, and
`var.name`, and say so in one line rather than silently renaming — the user needs to know
which variable now carries the name they used.

This applies to resource names, locals, defaults, tag values, descriptions, README prose,
and diagram labels alike. `check-module-rules.py` catches the known brand terms; it cannot
catch a customer name it has never seen.

---

## 2. Naming Bounds Outside AWS

The library is AWS-only, so its README covers AWS resources only. When generating for
another cloud, the same rule applies — truncate to `limit - (hash + 1)` characters, append a
hyphen and a stable hash of the *full* name — against these bounds:

| Provider | Resource | Limit | Valid characters | Mitigation |
|---|---|:---:|---|---|
| Azure | `azurerm_storage_account` | 3–24 | lowercase alphanumeric only, no hyphens | Strip hyphens, lowercase, truncate to 18 + 6-char hash |
| Azure | `azurerm_virtual_network` | 2–64 | alphanumeric, `_`, `-`, `.` | Truncate to 58 + 5-char hash |
| GCP | `google_compute_network` | 1–63 | lowercase, digits, hyphen | Truncate to 57 + 5-char hash |

Verify the limit against the provider schema before relying on a row here; these are a
starting point, not an authority.
