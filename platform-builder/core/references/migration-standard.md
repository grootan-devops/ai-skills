# Library Migration Protocol

Use this reference only for platform update or a compatibility comparison.
The selected CI and Helm libraries' MIGRATION.md files are the source of
version-specific steps; do not maintain a second rename or schema table here.

1. Resolve each target library with core/scripts/libraries.py and record its
   checkout/ref. Read its README index and the pages relevant to the change.
2. Run core/scripts/audit.py against the consumer with those same library
   sources. Its migrations[] output lists available release sections between
   the consumer pin and selected target. Inspect every intermediate release
   in order. A branch or SHA pin may not establish a complete release chain;
   report that uncertainty.
3. Compare each applicable step with actual templates, schema, and consumer
   files at the selected ref. Apply only supported ref, input, chart, and
   workflow changes. Do not assume an old example in this Skill is a current
   migration instruction.
4. Preserve deliberate consumer customizations: cache keys, jobs, workflow
   conditions, Helm values, probes, resources, and mounts. If a customization
   conflicts with a required migration and intent is unclear, present the
   exact conflict for a user decision.
5. Re-audit with --strict and the same library sources. Render changed charts
   and run the selected CI platform's validation. Report the applied release
   sections, skipped or missing notes, and any unresolved compatibility risk.

An update is an edit of the existing files, never a fresh scaffold. The engine
finds candidate version steps; it does not apply prose migrations or prove
that a branch pinned consumer is already current.
