#!/usr/bin/env python3
"""Workspace and stage control for the k8s-upgrade skill.

    python3 workspace.py providers
    python3 workspace.py init    <root> <cluster> --provider NAME [--context NAME]
    python3 workspace.py lint    <cluster-dir>
    python3 workspace.py require <cluster-dir> <stage>
    python3 workspace.py record  <cluster-dir> <stage> pass|fail [--run DIR] [--note TEXT]
    python3 workspace.py status  <cluster-dir>
    python3 workspace.py kubeconfig <cluster-dir>

The skill runs as six stages, one per invocation: init, lint, check, prepare, upgrade,
validate (core/references/stages.md). Each stage records its result in
<cluster-dir>/state.json; `require` exits non-zero, saying why, unless the stages before the
given one passed, in order, and nothing earlier was re-run since. check creates the run folder
and records it; later stages use that run.

A cluster belongs to one provider (kubeadm today; managed providers later), named in
cluster.md. The provider's providers/<name>/scripts/provider.py supplies the files init
creates, which of them hold credentials, and its offline lint checks
(core/references/providers.md). `providers` lists the providers this copy of the skill has.

init creates <root>/.gitignore and <root>/<cluster>/ with cluster.md (provider and kubectl
context), critical-extra.txt and the provider's input files, without overwriting anything.
lint checks the workspace offline: the provider's checks, that git ignores the credential
files, the kubectl context exists, kubectl and python3 are installed, critical-extra.txt syntax.
kubeconfig writes <cluster-dir>/kubeconfig (mode 600) holding only the context named in
cluster.md; each stage exports KUBECONFIG to it, so the run never depends on, or changes, the
user's current kubectl context.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import os
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

STAGES = ["init", "lint", "check", "prepare", "upgrade", "validate"]
HERE = Path(__file__).resolve().parent
SKILL = HERE.parent.parent
ASSETS = HERE.parent / "assets"
PROVIDERS = SKILL / "providers"


def now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def load_state(c: Path) -> dict:
    p = c / "state.json"
    return json.loads(p.read_text()) if p.exists() else {"stages": {}, "run": None}


def save_state(c: Path, state: dict) -> None:
    (c / "state.json").write_text(json.dumps(state, indent=1) + "\n")


def cluster_field(c: Path, key: str) -> str:
    p = c / "cluster.md"
    if not p.exists():
        return ""
    for line in p.read_text().splitlines():
        if line.lower().startswith(f"{key}:"):
            return line.split(":", 1)[1].strip()
    return ""


def context_of(c: Path) -> str:
    return cluster_field(c, "context")


def available_providers() -> list[str]:
    return sorted(d.name for d in PROVIDERS.iterdir() if (d / "scripts" / "provider.py").is_file())


def load_provider(name: str):
    path = PROVIDERS / name / "scripts" / "provider.py"
    if not path.is_file():
        raise SystemExit(f"provider {name!r} is not supported by this skill yet; available: "
                         f"{', '.join(available_providers())} (see core/references/providers.md)")
    spec = importlib.util.spec_from_file_location(f"k8s_upgrade_provider_{name}", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def cmd_providers(args) -> int:
    for name in available_providers():
        print(name)
    return 0


def cmd_init(args) -> int:
    provider = load_provider(args.provider)
    root, c = Path(args.root), Path(args.root) / args.cluster
    c.mkdir(parents=True, exist_ok=True)
    made = []
    if not (root / ".gitignore").exists():
        shutil.copy(ASSETS / "gitignore.example", root / ".gitignore")
        made.append(".gitignore")
    for name, src in provider.INIT_FILES.items():
        if not (c / name).exists():
            shutil.copy(PROVIDERS / args.provider / "assets" / src, c / name)
            made.append(f"{args.cluster}/{name}")
        if name in provider.SECRET_FILES:
            os.chmod(c / name, 0o600)
    if not (c / "cluster.md").exists():
        ctx = args.context or subprocess.run(["kubectl", "config", "current-context"], capture_output=True, text=True).stdout.strip()
        (c / "cluster.md").write_text(
            f"# Cluster {args.cluster}\n\nprovider: {args.provider}\ncontext: {ctx}\n\n"
            "Every kubectl call of this skill uses this context, through the kubeconfig each stage writes.\n")
        made.append(f"{args.cluster}/cluster.md")
    elif cluster_field(c, "provider") != args.provider:
        raise SystemExit(f"{c}/cluster.md names provider {cluster_field(c, 'provider')!r}, not {args.provider!r}")
    if not (c / "critical-extra.txt").exists():
        (c / "critical-extra.txt").write_text(
            "# namespace/kind/name per line (kind: deployment, statefulset or daemonset).\n"
            "# Workloads the applications depend on that critical.py does not detect.\n")
        made.append(f"{args.cluster}/critical-extra.txt")
    state = load_state(c)
    state["stages"] = {"init": {"result": "pass", "at": now()}}
    state["run"] = None
    save_state(c, state)
    print("created: " + (", ".join(made) or "nothing (all present)"))
    todo = ", ".join(f"{c}/{n}" for n in provider.INIT_FILES)
    print(f"next: fill in {todo}, check the provider and context in cluster.md, then run lint")
    return 0


def cmd_lint(args) -> int:
    c = Path(args.cluster_dir)
    problems, notes = [], []
    name = cluster_field(c, "provider")
    if not name:
        problems.append("cluster.md missing or has no provider: line (run init)")
        provider = None
    else:
        provider = load_provider(name)
        notes.append(f"provider {name}")
        p_notes, p_problems = provider.lint(c)
        notes += p_notes
        problems += p_problems
    git = subprocess.run(["git", "-C", str(c), "rev-parse", "--is-inside-work-tree"], capture_output=True, text=True)
    if git.returncode == 0:
        for f in [*(provider.SECRET_FILES if provider else []), "kubeconfig"]:
            if subprocess.run(["git", "-C", str(c), "check-ignore", "-q", str(c / f)]).returncode != 0:
                problems.append(f"{f} is not ignored by git")
    for tool in ("kubectl", "python3"):
        if not shutil.which(tool):
            problems.append(f"{tool} is not installed")
    ctx = context_of(c)
    known = subprocess.run(["kubectl", "config", "get-contexts", "-o", "name"], capture_output=True, text=True).stdout.split()
    if not ctx:
        problems.append("cluster.md has no context: line")
    elif ctx not in known:
        problems.append(f"kubectl context {ctx} not found in the kubeconfig")
    else:
        notes.append(f"kubectl context {ctx}")
    extra = c / "critical-extra.txt"
    if extra.exists():
        for no, line in enumerate(extra.read_text().splitlines(), 1):
            s = line.strip()
            if s and not s.startswith("#") and (s.count("/") != 2 or s.split("/")[1].lower() not in ("deployment", "statefulset", "daemonset")):
                problems.append(f"critical-extra.txt:{no}: expected namespace/kind/name, got {s}")
    for n in notes:
        print(f"OK   {n}")
    for p in problems:
        print(f"FAIL {p}")
    _record(c, "lint", "fail" if problems else "pass")
    print(f"RESULT {'FAIL' if problems else 'PASS'}")
    return 1 if problems else 0


def _record(c: Path, stage: str, result: str, run: str | None = None, note: str | None = None) -> None:
    state = load_state(c)
    i = STAGES.index(stage)
    # A re-run invalidates every later stage.
    state["stages"] = {k: v for k, v in state["stages"].items() if STAGES.index(k) < i}
    state["stages"][stage] = {"result": result, "at": now(), **({"note": note} if note else {})}
    if run:
        state["run"] = run
    elif i <= STAGES.index("check"):
        state["run"] = None  # the run folder belongs to the check that created it
    save_state(c, state)


def cmd_kubeconfig(args) -> int:
    c = Path(args.cluster_dir)
    ctx = context_of(c)
    r = subprocess.run(["kubectl", "config", "view", "--raw", "--minify", "--flatten", "--context", ctx],
                       capture_output=True, text=True)
    if r.returncode != 0 or not r.stdout.strip():
        print(f"cannot export context {ctx}: {r.stderr.strip()}", file=sys.stderr)
        return 1
    target = c / "kubeconfig"
    target.touch(mode=0o600, exist_ok=True)
    os.chmod(target, 0o600)
    target.write_text(r.stdout)
    print(target)
    return 0


def cmd_record(args) -> int:
    _record(Path(args.cluster_dir), args.stage, args.result, args.run, args.note)
    return 0


def cmd_require(args) -> int:
    c = Path(args.cluster_dir)
    state = load_state(c)
    for prior in STAGES[: STAGES.index(args.stage)]:
        entry = state["stages"].get(prior)
        if not entry:
            print(f"BLOCKED {args.stage}: run {prior} first")
            return 1
        if entry["result"] != "pass":
            print(f"BLOCKED {args.stage}: {prior} did not pass ({entry['at']}); fix it and run {prior} again")
            return 1
    if STAGES.index(args.stage) > STAGES.index("check") and not state.get("run"):
        print(f"BLOCKED {args.stage}: no run folder recorded; run check again")
        return 1
    print(f"OK {args.stage}" + (f" run={state['run']}" if state.get("run") else ""))
    return 0


def cmd_status(args) -> int:
    state = load_state(Path(args.cluster_dir))
    print(f"provider: {cluster_field(Path(args.cluster_dir), 'provider') or '-'}")
    print(f"run: {state.get('run') or '-'}")
    for s in STAGES:
        e = state["stages"].get(s)
        print(f"  {s:9} {e['result'] + '  ' + e['at'] + ('  ' + e['note'] if e.get('note') else '') if e else '-'}")
    nxt = next((s for s in STAGES if state["stages"].get(s, {}).get("result") != "pass"), None)
    print(f"next: {nxt or 'done'}")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("providers")
    p = sub.add_parser("init"); p.add_argument("root"); p.add_argument("cluster")
    p.add_argument("--provider", required=True); p.add_argument("--context")
    p = sub.add_parser("lint"); p.add_argument("cluster_dir")
    p = sub.add_parser("require"); p.add_argument("cluster_dir"); p.add_argument("stage", choices=STAGES)
    p = sub.add_parser("record"); p.add_argument("cluster_dir"); p.add_argument("stage", choices=STAGES)
    p.add_argument("result", choices=["pass", "fail"]); p.add_argument("--run"); p.add_argument("--note")
    p = sub.add_parser("status"); p.add_argument("cluster_dir")
    p = sub.add_parser("kubeconfig"); p.add_argument("cluster_dir")
    args = ap.parse_args()
    return {"providers": cmd_providers, "init": cmd_init, "lint": cmd_lint, "require": cmd_require, "record": cmd_record,
            "status": cmd_status, "kubeconfig": cmd_kubeconfig}[args.cmd](args)


if __name__ == "__main__":
    sys.exit(main())
