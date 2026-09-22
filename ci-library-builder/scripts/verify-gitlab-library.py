#!/usr/bin/env python3
"""Structural gates for a GitLab CI template library.

Checks the mechanical parts of references/gitlab-job-anatomy.md. It does not replace
yamllint or a pipeline dry-run -- run those too.

    python3 verify-gitlab-library.py <library_dir> [--strict] [--json]

Exit codes:
  0  no P0 findings (or no findings at all with --strict)
  1  P0 findings, or any finding with --strict
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

try:
    import yaml
except ImportError:
    sys.exit("PyYAML is required: pip install pyyaml")

# Concrete jobs the library deliberately names with a language segment.
NAMING_EXCEPTIONS = {"Go:Dependency:Download", "Go:Fmt", "Go:Vet", "Go:Lint", "Go:Security:Scan"}
LANG_PREFIXES = ("Node:", "Python:", "Java:", "Golang:", "Go:")


class Finding:
    def __init__(self, sev, rule, where, msg, fix):
        self.sev, self.rule, self.where, self.msg, self.fix = sev, rule, where, msg, fix

    def as_dict(self):
        return dict(severity=self.sev, rule=self.rule, where=self.where, message=self.msg, fix=self.fix)


def load(path: Path):
    """Parse a GitLab YAML file, tolerating !reference and other GitLab tags."""
    class Loader(yaml.SafeLoader):
        pass

    Loader.add_multi_constructor("!", lambda loader, suffix, node: None)
    try:
        # A file with an `inputs:` spec header is two documents; the jobs are in the last.
        docs = [d for d in yaml.load_all(path.read_text(), Loader=Loader) if isinstance(d, dict)]
        return docs[-1] if docs else {}
    except yaml.YAMLError as exc:
        return {"__parse_error__": str(exc).splitlines()[0]}


def collect(root: Path):
    """Return (jobs, anchors, stages, files) across the whole library."""
    jobs, anchors, stages, docs = {}, {}, [], {}
    for f in sorted(root.rglob("*.yml")):
        # `.github/` holds this library's own GitHub Actions CI, not GitLab CI.
        # Those files have an `on:` key, which YAML 1.1 parses as the boolean
        # True -- and a bool has no .startswith, so scanning them crashed the
        # whole verifier and every check below it silently never ran.
        if ".git/" in str(f) or ".github/" in str(f) or f.name == ".yamllint.yml":
            continue
        doc = load(f)
        if not isinstance(doc, dict):
            continue
        docs[f] = doc
        if isinstance(doc.get("stages"), list):
            stages = doc["stages"]
        for name, body in doc.items():
            if not isinstance(name, str):
                continue
            if not isinstance(body, dict) or name in ("variables", "workflow", "default", "include", "stages"):
                continue
            (anchors if name.startswith(".") else jobs)[name] = (f, body)
    return jobs, anchors, stages, docs


def extends_of(body):
    e = body.get("extends")
    return [e] if isinstance(e, str) else list(e or [])


def _reaches_rules(name, jobs, anchors, seen):
    """True if the job declares rules, or reaches a *-rules anchor through any extends chain."""
    if name in seen:
        return False
    seen.add(name)
    entry = jobs.get(name) or anchors.get(name)
    if not entry:
        return name.endswith("rules")
    body = entry[1]
    if "rules" in body:
        return True
    return any(_reaches_rules(p, jobs, anchors, seen) for p in extends_of(body) if isinstance(p, str))


def check(root: Path):
    out = []
    jobs, anchors, stages, docs = collect(root)
    known = set(jobs) | set(anchors)

    for f, doc in docs.items():
        rel = str(f.relative_to(root))
        if "__parse_error__" in doc:
            out.append(Finding("P0", "yaml", rel, f"does not parse: {doc['__parse_error__']}", "fix the YAML"))
            continue

        # --- shell gate: $? captured on its own script line (always 0) ---------
        for i, line in enumerate(f.read_text().splitlines(), 1):
            if re.match(r"^\s*-\s*[A-Za-z_][A-Za-z0-9_]*=\$\?\s*$", line):
                out.append(Finding(
                    "P0", "exit-code-capture", f"{rel}:{i}",
                    "`$?` captured on its own script line — GitLab echoes each item first, "
                    "which resets `$?`, so this is always 0 and the gate can never fail",
                    "put the command, the capture and the exit in one `- |` block"))

    for name, (f, body) in sorted({**jobs, **anchors}.items()):
        rel = f"{f.relative_to(root)} :: {name}"
        hidden = name.startswith(".")

        # --- image belongs to an anchor or default, never a concrete job ------
        if "image" in body and not hidden:
            out.append(Finding(
                "P1", "image-on-concrete-job", rel,
                "concrete job declares `image:` directly — normally the runtime anchor owns it",
                "legitimate only when the toolkit lacks the tool and no anchor provides it "
                "(sonar-scanner, go); otherwise move it to the anchor"))

        # --- stage must exist -------------------------------------------------
        st = body.get("stage")
        if st and stages and st not in stages:
            out.append(Finding("P0", "unknown-stage", rel, f"stage `{st}` is not in `stages:`",
                               f"use one of: {', '.join(stages)}"))

        # --- extends must resolve --------------------------------------------
        for parent in extends_of(body):
            if isinstance(parent, str) and parent not in known:
                out.append(Finding("P0", "unresolved-extends", rel,
                                   f"extends `{parent}`, which no file in this library defines",
                                   "fix the name, or add the missing anchor"))

        # --- needs: optional is mandatory and targets must exist --------------
        for n in body.get("needs") or []:
            if isinstance(n, dict) and "job" in n:
                if "optional" not in n:
                    out.append(Finding(
                        "P0", "needs-missing-optional", rel,
                        f"`needs: {n['job']}` has no `optional:` — it defaults to false and "
                        "fails pipeline creation the moment that job is gated out",
                        "set optional: true (may be absent) or false (genuinely required)"))
                tgt = n["job"]
                if isinstance(tgt, str) and "$" not in tgt and tgt not in known and not n.get("optional"):
                    sev = "P1" if hidden else "P0"
                    out.append(Finding(sev, "needs-unknown-job", rel,
                                       f"`needs: {tgt}` names a job this library does not define"
                                       + (" — a consumer-declared name, so it must be optional"
                                          if hidden else ""),
                                       "fix the name, or mark it optional: true"))
                if n.get("artifacts") is None:
                    out.append(Finding("P1", "needs-artifacts-unset", rel,
                                       f"`needs: {n['job']}` does not say `artifacts:`",
                                       "artifacts: false unless the job opens the payload"))

        if hidden:
            continue

        # --- concrete jobs only ----------------------------------------------
        if re.match(r"^(Node|Python|Java|Golang):Dependency:Download$", name):
            out.append(Finding("P1", "language-prefixed-job", rel,
                               "project-level job carries a language segment",
                               "name it `Dependency:Download`; the language lives in the hidden "
                               "template it extends (Go is the deliberate exception)"))

        has_rules = _reaches_rules(name, jobs, anchors, set())
        if not has_rules:
            out.append(Finding("P1", "unconditional-job", rel,
                               "no `rules:` and extends no `*-rules` anchor — runs in every pipeline",
                               "extend an existing rules anchor from common/.gitlab-ci.yml"))

        cache = body.get("cache")
        if isinstance(cache, dict) and "policy" not in cache:
            out.append(Finding("P1", "cache-without-policy", rel, "`cache:` without `policy:`",
                               "pull-push in the job that warms it, pull everywhere else"))

        arts = body.get("artifacts")
        if isinstance(arts, dict) and "expire_in" not in arts:
            out.append(Finding("P1", "artifacts-without-expire", rel, "`artifacts:` without `expire_in:`",
                               "set it explicitly; default retention is not a decision"))
    return out


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("library_dir")
    ap.add_argument("--strict", action="store_true", help="exit 1 on any finding, not just P0")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args()

    root = Path(a.library_dir).resolve()
    if not root.is_dir():
        sys.exit(f"not a directory: {root}")

    findings = check(root)
    if a.json:
        print(json.dumps([f.as_dict() for f in findings], indent=2))
    else:
        for sev in ("P0", "P1"):
            group = [f for f in findings if f.sev == sev]
            if not group:
                continue
            print(f"\n{'❌' if sev == 'P0' else 'ℹ️ '} {sev} ({len(group)})")
            for f in group:
                print(f"  {f.where}\n      {f.msg}\n      → {f.fix}")
        p0 = sum(1 for f in findings if f.sev == "P0")
        print(f"\nTotals: P0={p0} P1={len(findings) - p0}")
        if not findings:
            print("Clean.")
        print("\nAlso run: yamllint -c .yamllint.yml <library_dir>")

    p0 = any(f.sev == "P0" for f in findings)
    sys.exit(1 if (p0 or (a.strict and findings)) else 0)


if __name__ == "__main__":
    main()
