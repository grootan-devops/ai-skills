# The Reference Repository Is the Source of Truth

`terraform-modules` holds the ground-truth modules and the standards they are built to. The
skill does not carry its own copy of those standards: the contract, the naming and tagging
rules, the security baselines, the documentation standard, the release levels, and the test
gate all live in that repository's `README.md`, and the upgrade contract lives in its
`MIGRATION.md`. Resolve the repository first, then read from it — a rule quoted from memory
is a rule that has already drifted.

## Locating it

The operator supplies the repository; the skill hardcodes no path. Resolve in this order and
stop at the first that exists:

1. `$TERRAFORM_MODULES_REPO`, if set.
2. A `terraform-modules/` checkout beside the directory this skill was installed into.
3. Ask the user for the path.

Never guess, and never carry on without it.

## What to read, and when

| Before you… | Read |
| --- | --- |
| design any public API | `README.md` §4 *Module Contract* |
| name a resource or emit a tag | `README.md` §5 *Naming & Tagging Standards* |
| choose a security control | `README.md` §6 *Security Baselines*, then `docs/AWS.md` |
| write or refresh a module README | `README.md` §7 *Module Documentation Standard* |
| classify a change or cut a release | `README.md` §8 *Versioning & Release Contract* |
| change a resource address | `MIGRATION.md` |
| claim a module is verified | `README.md` §9, and run `make verify` |

The module catalog is `modules/`, partitioned by provider and domain; `docs/AWS.md` carries
the per-module catalog with its compliance baseline. Both are listed in the repository's own
`README.md` §1 — read the layout there rather than from a copy here, which is exactly the
kind of list that goes stale when a module is added.

## Reading existing modules for patterns

`README.md` §4–§9 contain "current state" callouts recording where the shipped modules
diverge from the standard they document — tag merge order, provider upper bounds, name
truncation. Those are live: a module is evidence of what the library does, not proof of what
it should do. When the two disagree, the standard wins and the divergence is a finding.

Otherwise, observe file decomposition (`<service>.tf`, `security.tf`, `variables.tf`,
`outputs.tf`, `locals.tf`, `data.tf`, `versions.tf`), and verify every pattern against
`terraform providers schema -json` before reusing it.
