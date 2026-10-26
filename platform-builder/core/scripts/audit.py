#!/usr/bin/env python3
"""Unified compliance engine: detect platform -> resolve libraries -> run common + CI checks.

    python3 audit.py [repo_path] [--platform gitlab|github] [--latest] [--strict] [--json]

Exit codes:  0 clean (or only P1/P2 without --strict)   1 --strict with findings
             2 any P0, or a library release the checks cannot use (nothing is checked then)

Each library is read at the release the repository pins, or at its latest release with
--latest (an upgrade) or when the repository pins none. The checks take their library rules
from the data file that release ships; a release without it stops the run before any check.

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
    LIBRARY: str                     the library whose release the CI checks read
    ci_checks(repo: Path, shape: str | None, library_root: Path, library_ref: str,
              notes: list[str]) -> (list[Finding], dict)

Keeping that interface at four names is deliberate. If it grows past a handful, the
abstraction has stopped paying for itself and the platforms should diverge again.

Everything emitted here is deterministic and tagged [engine]. Judgement-based findings
come from the agent's review against the selected libraries' security guides, and are
tagged [judged]. The two are never blurred: one is a reproducible fact,
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
import pins                             # noqa: E402
import platform as platform_detect      # noqa: E402  (local module, not stdlib `platform`)
from checks_common import Finding       # noqa: E402
from pins import chart_dir              # noqa: E402

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
    # Terraform builds no package, image or chart: root-level *.tf and nothing else.
    if any(root.glob("*.tf")) and not has_docker and not has_chart:
        return "terraform"
    return None


def run(repo_path: str, platform_name: Optional[str] = None,
        strict: bool = False, as_json: bool = False,
        lib_overrides: Optional[Dict[str, str]] = None, refresh: bool = False,
        latest: bool = False) -> int:
    repo = Path(repo_path).resolve()
    det = platform_detect.detect(str(repo), explicit=platform_name)

    # Which release of each shared library this run reads: the one the repository pins,
    # else its latest. Reported with every result, because half the findings depend on the
    # release the repo is measured against; and a release whose data the checks cannot read
    # stops the run here instead of being measured against rules the skill would guess.
    pinned, _ = libs.project_pins(repo, det.platform)
    # Without a platform no CI check runs, so only the chart library can be needed.
    names = None if det.platform else list(libs.chart_dependencies())
    resolved_libs = libs.resolve_all(repo, det.platform, lib_overrides or {}, names=names,
                                     refresh=refresh, latest=latest, pinned=pinned)
    by_name = {r.name: r for r in resolved_libs}
    notes = [f"{r.name}: {n}" for r in resolved_libs for n in r.notes]
    unusable = [r for r in resolved_libs if not r.ok]
    if unusable:
        if as_json:
            print(json.dumps({
                "repository": str(repo),
                "platform": det.platform,
                "libraries": [libs.asdict(r) for r in resolved_libs],
                "error": "; ".join(f"{r.name}: {r.error}" for r in unusable),
            }, indent=2))
        else:
            print(libs.render(resolved_libs))
            print("\nAudit stopped: no check ran against a library release it cannot read.")
        return 2

    findings: List[Finding] = []
    shape = detect_shape(repo)
    ci_data: Dict[str, Any] = {}

    # Shared checks whose REMEDY differs per platform read this; the principle they
    # enforce is universal, the fix they suggest is not.
    common.set_platform(det.platform or "generic")
    common.set_repo_context(repo)
    helm = by_name.get("helm-tpl-library")
    common.set_chart_library(common.ChartLibrary.load(Path(helm.path)) if helm and helm.path else None)

    if not common.HAVE_YAML:
        findings.append(Finding(
            "P1", "PyYAML Missing", "python3",
            "PyYAML is not installed, so every check that parses CI or chart YAML was skipped. "
            "Install it (`pip install pyyaml`) and re-run before trusting this report."))

    # --- CI checks (platform-specific) -------------------------------------------
    adapter = load_adapter(det.platform) if det.platform else None
    if adapter:
        ci_lib = by_name.get(adapter.LIBRARY)
        ci_findings, ci_data = adapter.ci_checks(
            repo, shape, library_root=Path(ci_lib.path) if ci_lib and ci_lib.path else None,
            library_ref=(ci_lib.ref or "") if ci_lib else "", notes=notes)
        findings += ci_findings
    else:
        findings.append(Finding(
            "P1", "Platform unresolved", str(repo),
            f"Could not determine the CI platform ({det.confidence}). CI checks were "
            f"skipped; Dockerfile/chart checks still ran. Re-run with "
            f"--platform gitlab|github."))

    # --- Migration chain ----------------------------------------------------------
    # Which release the repo pins, and every release between that and the one this run
    # resolved. Reported, never applied: the sections are prose and applying them is
    # judgement. Reporting it is what makes a skipped release visible.
    for name, refs in pinned.items():
        if name in by_name and len(refs) > 1:
            where = "Chart.yaml" if name in libs.chart_dependencies() else ", ".join(adapter.CI_FILES if adapter else [])
            findings.append(Finding(
                "P2", "Mixed Library Pins", where,
                f"{name} is pinned to {', '.join(refs)}. The library's parts are released together; "
                f"pin one release everywhere. This run reads the oldest, {pins.pick(refs)}."))
    migrations: List[Dict[str, Any]] = []
    for r in resolved_libs:
        if not r.ok or not getattr(r, "path", None):
            continue
        current = pins.pick(pinned.get(r.name) or [])
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
    findings += common.check_gitignore(repo, cdir)
    has_chart = (cdir / "Chart.yaml").exists()
    findings += common.check_comment_style(repo, cdir if has_chart else None)
    if has_chart:
        findings += common.check_helm_chart(cdir)
        findings += common.check_helmignore(cdir)
        findings += common.check_container_overrides(cdir)
        findings += common.check_chart_runtime(cdir)
        findings += common.check_secret_placement(cdir)
        findings += common.check_schema_contract(cdir)
        findings += common.check_modes(cdir)
        findings += common.check_optional_blocks(cdir)
        findings += common.check_values_leaf_docs(cdir)
        if det.platform == "gitlab":
            ci_vars = ci_data.get("variables") if isinstance(ci_data, dict) and isinstance(ci_data.get("variables"), dict) else {}
            findings += common.check_image_path_alignment(repo, cdir, ci_vars)
        # Measured against the tpl-library release this run resolved, so the parity report
        # moves with the library rather than against a frozen expectation.
        findings += common.check_values_parity(cdir)
        findings += common.check_app_values_shape(cdir)
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
            "notes": notes,
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
        for n in notes:
            print(f"  note: {n}")
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
        print("NOTE: structural checks only. Review against the selected libraries' security "
              "guides for [judged] findings.")

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
    ap.add_argument("--latest", action="store_true",
                    help="measure against every library's latest release instead of the one the "
                         "repository pins (an upgrade, `platform update`)")
    args = ap.parse_args()
    sys.exit(run(args.repo_path, args.platform, args.strict, args.json,
                 libs.cli_lib_overrides(args), args.refresh, args.latest))


if __name__ == "__main__":
    main()
