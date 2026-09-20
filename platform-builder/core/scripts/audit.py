#!/usr/bin/env python3
"""Unified compliance engine: detect platform -> load adapter -> run common + CI checks.

    python3 audit.py [repo_path] [--platform gitlab|github] [--strict] [--json]

Exit codes:  0 clean (or only P1/P2 without --strict)   1 --strict with findings   2 any P0

Design
------
Two kinds of check, run in one pass:

  * COMMON  (core/scripts/checks_common.py) -- Dockerfile, Helm chart, .dockerignore,
    .gitignore, project hygiene. Identical on every platform, so they run unconditionally.
  * CI      (platforms/<name>/ci_checks.py) -- the config format, job wiring and token
    model, which differ completely between platforms.

Adapter contract -- a platform module exposes:
    NAME: str
    CI_FILES: list[str]
    ci_checks(repo: Path, shape: str | None = None) -> (list[Finding], dict)

Keeping that interface at three names is deliberate. If it grows past a handful, the
abstraction has stopped paying for itself and the platforms should diverge again.

Everything emitted here is deterministic and tagged [engine]. Judgement-based findings
come from the agent's pass over core/references/security-core.md plus the platform
addendum, and are tagged [judged]. The two are never blurred: one is a reproducible fact,
the other a considered opinion the user may overrule.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

_HERE = Path(__file__).resolve().parent
_ROOT = _HERE.parent.parent
sys.path.insert(0, str(_HERE))

import checks_common as common          # noqa: E402
import libraries as libs                # noqa: E402
import platform as platform_detect      # noqa: E402  (local module, not stdlib `platform`)
from checks_common import Finding       # noqa: E402

APP_MANIFESTS = ("package.json", "pyproject.toml", "requirements.txt", "go.mod",
                 "pom.xml", "build.gradle", "build.gradle.kts", "Cargo.toml", "Gemfile")


def load_adapter(name: str):
    """Import platforms/<name>/ci_checks.py with its own directory on the path."""
    adir = _ROOT / "platforms" / name
    mod_path = adir / "ci_checks.py"
    if not mod_path.exists():
        raise SystemExit(f"ERROR: no adapter for platform '{name}' at {mod_path}")
    sys.path.insert(0, str(adir))
    spec = importlib.util.spec_from_file_location(f"adapter_{name}", mod_path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def chart_dir(root: Path, declared: str = "") -> Path:
    """Resolve the chart location, honouring a declared CHART_DIR.

    A root-level Chart.yaml is normal for library and umbrella charts; assuming ./chart
    produces a spurious blocking finding on exactly those repositories.
    """
    if declared:
        return (root / declared).resolve()
    if (root / "Chart.yaml").exists():
        return root
    for candidate in ("chart", "helm", "charts"):
        if (root / candidate / "Chart.yaml").exists():
            return root / candidate
    return root / "chart"


def detect_shape(root: Path) -> Optional[str]:
    has_chart = (chart_dir(root) / "Chart.yaml").exists()
    has_docker = (root / "Dockerfile").exists()
    has_app = any((root / m).exists() for m in APP_MANIFESTS)
    if has_chart and not has_docker and not has_app:
        return "chart-only"
    if has_docker and not has_app and not has_chart:
        return "image-only"
    if has_app and has_docker and has_chart:
        return "service-with-chart"
    if has_app and has_docker:
        return "service"
    if has_app:
        return "library" if (root / "LICENSE").exists() else "app-no-artifact"
    return None


def run(repo_path: str, platform_name: Optional[str] = None,
        strict: bool = False, as_json: bool = False,
        lib_overrides: Optional[Dict[str, str]] = None, refresh: bool = False) -> int:
    repo = Path(repo_path).resolve()
    det = platform_detect.detect(str(repo), explicit=platform_name)

    # Which copy of each shared library this run reads. Resolved and REPORTED rather
    # than checked: an audit whose provenance is unstated cannot be reproduced, because
    # half its findings depend on the library version the repo is measured against.
    resolved_libs = libs.resolve_all(repo, det.platform, lib_overrides or {},
                                     refresh=refresh)

    findings: List[Finding] = []
    shape = detect_shape(repo)
    ci_data: Dict[str, Any] = {}

    # Shared checks whose REMEDY differs per platform read this; the principle they
    # enforce is universal, the fix they suggest is not.
    common.set_platform(det.platform or "generic")

    # --- CI checks (platform-specific) -------------------------------------------
    if det.platform:
        adapter = load_adapter(det.platform)
        ci_findings, ci_data = adapter.ci_checks(repo, shape)
        findings += ci_findings
    else:
        findings.append(Finding(
            "P1", "Platform unresolved", str(repo),
            f"Could not determine the CI platform ({det.confidence}). CI checks were "
            f"skipped; Dockerfile/chart checks still ran. Re-run with "
            f"--platform gitlab|github."))

    # --- Migration chain ----------------------------------------------------------
    # Which library version the repo is pinned to, and every release between that and
    # the one this run resolved. Reported, never applied: the sections are prose and
    # applying them is judgement. Reporting it is what makes a skipped release visible.
    pinned: Dict[str, str] = {}
    if det.platform and ci_data:
        detector = getattr(load_adapter(det.platform), "library_refs", None)
        if detector:
            try:
                pinned = detector(ci_data) or {}
            except Exception:
                pinned = {}
    migrations: List[Dict[str, Any]] = []
    for r in resolved_libs:
        if not r.ok or not getattr(r, "path", None):
            continue
        current = pinned.get(r.name)
        steps = libs.migration_chain(r, current)
        if steps or current:
            migrations.append({
                "library": r.name,
                "current": current,
                "target": r.ref,
                "current_known": bool(current and libs._semver_key(current)),
                "steps": [{"version": v, "body": b} for v, b in steps],
            })

    # --- Common checks (every platform) -------------------------------------------
    declared_chart_dir = ""
    if isinstance(ci_data, dict):
        variables = ci_data.get("variables") if isinstance(ci_data.get("variables"), dict) else {}
        declared_chart_dir = str((variables or {}).get("CHART_DIR", "") or "")
    cdir = chart_dir(repo, declared_chart_dir)

    df = repo / "Dockerfile"
    if df.exists():
        findings += common.check_dockerfile(df, shape)
        findings += common.check_dockerignore(repo)
    findings += common.check_gitignore(repo)
    if (cdir / "Chart.yaml").exists():
        findings += common.check_helm_chart(cdir)
        findings += common.check_helmignore(cdir)
        # Measured against the tpllib copy this run actually resolved, so the parity
        # report moves with the library rather than against a frozen expectation.
        for r in resolved_libs:
            if r.name == "helm-tpl-library" and getattr(r, "path", None):
                findings += common.check_values_parity(cdir, Path(r.path) / "values.yaml")
    findings += common.check_project_hygiene(repo)

    p0 = [f for f in findings if f.severity == "P0"]
    p1 = [f for f in findings if f.severity == "P1"]
    p2 = [f for f in findings if f.severity == "P2"]

    if as_json:
        print(json.dumps({
            "repository": str(repo),
            "platform": det.platform,
            "platform_confidence": det.confidence,
            "platform_signals": det.signals,
            "needs_confirmation": det.needs_confirmation,
            "shape": shape,
            "libraries": [libs.asdict(r) for r in resolved_libs],
            "migrations": migrations,
            "findings": [f.to_dict() for f in findings],
            "summary": {"P0": len(p0), "P1": len(p1), "P2": len(p2)},
        }, indent=2))
    else:
        print("=" * 80)
        print(f"Platform Compliance Audit: {repo}")
        print("=" * 80)
        print(det.summary())
        print(f"  shape: {shape or 'unrecognised'}")
        for r in resolved_libs:
            if r.ok:
                at = f" @ {r.ref}" if r.ref else ""
                dirty = "  [UNCOMMITTED CHANGES]" if r.dirty else ""
                print(f"  library: {r.name}{at} ({r.kind}, via {r.origin}){dirty}")
            else:
                print(f"  library: {r.name} UNRESOLVED -- {r.error}")
        for m in migrations:
            if not m["steps"]:
                continue
            known = m["current"] if m["current_known"] else f"{m['current'] or 'unknown'} (not a release tag)"
            print(f"  migration: {m['library']} {known} -> {m['target']} — apply in order:")
            for st in m["steps"]:
                note = "" if st["body"] else "   (no MIGRATION.md section)"
                print(f"             {st['version']}{note}")
        print()
        if not findings:
            print("[PASS] No structural findings.")
        for f in p0 + p1 + p2:
            print(f)
        print()
        print("-" * 80)
        print(f"Audit Summary: P0 (Critical): {len(p0)} | P1 (High): {len(p1)} | P2 (Medium): {len(p2)}")
        print("-" * 80)
        print("NOTE: structural checks only. Run the agent's pass over "
              "core/references/security-core.md + the platform addendum for [judged] findings.")

    if p0:
        return 2
    if strict and (p1 or p2):
        return 1
    return 0


def main() -> None:
    ap = argparse.ArgumentParser(description="Unified GitLab/GitHub platform compliance audit.")
    ap.add_argument("repo_path", nargs="?", default=".")
    ap.add_argument("--platform", choices=["gitlab", "github"],
                    help="skip detection and force a platform")
    ap.add_argument("--strict", action="store_true", help="fail on P1/P2 as well as P0")
    ap.add_argument("--json", action="store_true", help="machine-readable output")
    libs.add_library_args(ap)
    ap.add_argument("--refresh", action="store_true",
                    help="re-fetch git library sources, ignoring the cache")
    args = ap.parse_args()
    sys.exit(run(args.repo_path, args.platform, args.strict, args.json,
                 libs.cli_lib_overrides(args), args.refresh))


if __name__ == "__main__":
    main()
