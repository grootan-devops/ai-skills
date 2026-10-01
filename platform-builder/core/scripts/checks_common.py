"""Platform-agnostic compliance checks.

Everything here inspects artefacts that are IDENTICAL regardless of whether CI runs on
GitLab or GitHub: the Dockerfile, the Helm chart, .dockerignore/.gitignore, and project
hygiene. Roughly 62% of the original GitLab rule engine turned out to live here.

These functions were lifted verbatim from the GitLab engine after it had been validated
against known-good baselines, so behaviour is unchanged by the extraction. Platform
mechanics (CI config shape, job wiring, token model) live in platforms/<name>/ci_checks.py.

Severity contract: every Finding produced here is DETERMINISTIC and reproducible, and is
tagged [engine]. Judgement-based findings are the agent's job -- a review against the
selected libraries' security guides -- and are tagged [judged]. Never blur the two.
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

try:
    import yaml
    HAVE_YAML = True
except ImportError:
    HAVE_YAML = False


# ---------------------------------------------------------------------------
# Platform-aware remediation.
#
# The PRINCIPLE "do not pull an unpinned image straight from a public registry"
# is universal. The REMEDY is not: GitLab offers a Dependency Proxy, GitHub has
# no equivalent and the fix is a pinned digest or an internal mirror. Emitting
# GitLab remediation on a GitHub repository is how a shared check quietly stops
# being shared, so the harness sets the profile before running any check.
# ---------------------------------------------------------------------------

_PLATFORM = "generic"

_IMAGE_REMEDY = {
    "gitlab": "Route it through the GitLab Dependency Proxy: '{suggested}', "
              "or use a trusted internal base image.",
    "github": "Pin it by digest (image@sha256:...) or mirror it into GHCR. "
              "GitHub has no Dependency Proxy equivalent.",
    "generic": "Pin it by digest or mirror it into an internal registry.",
}


def set_platform(name: str) -> None:
    """Select the remediation wording. Called once by the harness."""
    global _PLATFORM
    _PLATFORM = name if name in _IMAGE_REMEDY else "generic"


def _image_remedy(suggested: str) -> str:
    return _IMAGE_REMEDY[_PLATFORM].format(suggested=suggested)

def _load_trusted_registries() -> set:
    """Registry prefixes exempt from the dependency-proxy rule.

    Org-specific, so it is configuration rather than a constant: `trusted_registries`
    in core/libraries.json, overridable with $PLATFORM_BUILDER_TRUSTED_REGISTRIES
    (comma-separated). A prefix may carry a namespace -- "host/org" exempts that org's
    published images without exempting the whole public registry behind it.
    """
    env = os.environ.get("PLATFORM_BUILDER_TRUSTED_REGISTRIES", "")
    if env.strip():
        return {p.strip().rstrip("/") for p in env.split(",") if p.strip()}
    cfg = Path(__file__).resolve().parent.parent / "libraries.json"
    try:
        with cfg.open(encoding="utf-8") as fh:
            return {str(p).rstrip("/") for p in (json.load(fh).get("trusted_registries") or [])}
    except (OSError, ValueError):
        return set()


TRUSTED_INTERNAL_REGISTRIES = _load_trusted_registries()

# The repository's own GitLab project registry, derived from its git remote by
# set_repo_context(). Generic GitLab convention, not org configuration: an image the
# project pushes to its own registry is not an external dependency.
_OWN_REGISTRIES: Set[str] = set()


def origin_project(repo: Path) -> Tuple[str, str]:
    """(host, project path) from the repository's `origin` remote, or ("", "")."""
    try:
        import subprocess  # noqa: WPS433
        out = subprocess.run(["git", "-C", str(repo), "config", "--get", "remote.origin.url"],
                             capture_output=True, text=True, timeout=5)
        url = out.stdout.strip() if out.returncode == 0 else ""
    except Exception:
        url = ""
    m = re.match(r"^(?:[a-z+]+://)?(?:[^@/]+@)?([^/:]+)(?::\d+)?[:/](.+?)(?:\.git)?/?$", url)
    return (m.group(1).lower(), m.group(2)) if m else ("", "")


def set_repo_context(repo: Path) -> None:
    """Trust the repository's own project registry. Called once by the harness."""
    _OWN_REGISTRIES.clear()
    host, path = origin_project(repo)
    if host and path:
        _OWN_REGISTRIES.update({f"{host}:5050/{path}", f"registry.{host}/{path}", f"{host}/{path}"})



CI_RESERVED_KEYS = {
    "stages", "variables", "include", "default", "workflow",
    "image", "services", "cache", "before_script", "after_script", "script",
}


MULTILINE_SHELL_MARKERS = re.compile(
    r"\b(if|for|while|case|function)\b|"   # control flow
    r"<<\s*['\"]?\w+|"                      # heredoc
    r"\(\)\s*\{|"                           # function definition
    r"^\s*#"                                # deliberate comment block
    , re.MULTILINE
)


class Finding:
    def __init__(self, severity: str, category: str, location: str, message: str):
        self.severity = severity  # P0 (Critical), P1 (High), P2 (Medium/Warning)
        self.category = category
        self.location = location
        self.message = message

    def to_dict(self) -> Dict[str, str]:
        return {
            "severity": self.severity,
            "category": self.category,
            "location": self.location,
            "message": self.message,
        }

    def __str__(self) -> str:
        # Everything this engine emits is deterministic and reproducible. The agent's
        # own security review (the libraries' security guides) emits [judged] findings
        # alongside these; the tags keep the two kinds distinguishable in one report.
        return f"[{self.severity}] [engine] {self.category:<24} {self.location}\n     -> {self.message}"


def classify_unproxied_public_image(image_str: str, known_stages: set = None) -> Tuple[bool, str]:
    """
    Evaluates whether an image reference pulls from the internet without using the
    GitLab Dependency Proxy.
    Only trusted internal registries (see `trusted_registries`), local build
    stages, and scratch are exempt. Everything else pulling from the internet
    (Docker Hub, ghcr.io, quay.io, gcr.io, public registries) must use
    ${CI_DEPENDENCY_PROXY_GROUP_IMAGE_PREFIX}/<image> for faster cached pulls.
    Returns (is_unproxied, suggested_replacement).
    """
    if not image_str or not isinstance(image_str, str):
        return False, ""

    known_stages = known_stages or set()
    img = image_str.strip().strip("'\"")

    # 0. An empty default (`ARG CI_DEPENDENCY_PROXY_GROUP_IMAGE_PREFIX=""`) names no image.
    if not img:
        return False, ""

    # 1. Scratch or local multi-stage reference (local only, does not pull from internet)
    if img.lower() == "scratch" or img in known_stages:
        return False, ""

    # 2. Already proxied via CI_DEPENDENCY_PROXY
    if "CI_DEPENDENCY_PROXY" in img:
        return False, ""

    # 3. A variable reference is supplied by CI at build time. Its literal default, where one
    #    is declared, is checked on the ARG line itself.
    if img.startswith("$"):
        return False, ""

    # 4. Check if image explicitly points to a trusted registry or the project's own registry
    for trusted in TRUSTED_INTERNAL_REGISTRIES | _OWN_REGISTRIES:
        if img == trusted or img.startswith((trusted + "/", trusted + ":", trusted + "@")) \
                or f"//{trusted}/" in img:
            return False, ""

    # 5. All other images pull from the internet (Docker Hub, ghcr.io, quay.io, gcr.io, etc.)
    # Strip docker.io/ prefix if present for clean dependency proxy path
    clean_img = img
    for dh_prefix in ["docker.io/library/", "docker.io/", "index.docker.io/"]:
        if clean_img.startswith(dh_prefix):
            clean_img = clean_img[len(dh_prefix):]
            break

    suggested = f"${{CI_DEPENDENCY_PROXY_GROUP_IMAGE_PREFIX}}/{clean_img}"
    return True, suggested


def iter_jobs(data: Dict[str, Any]):
    """Yield (job_name, job_body) for every concrete job in a CI document.

    Skips reserved top-level keys and hidden `.template` jobs, which are anchors
    rather than scheduled work.
    """
    if not isinstance(data, dict):
        return
    for name, body in data.items():
        if name in CI_RESERVED_KEYS or name.startswith("."):
            continue
        if isinstance(body, dict):
            yield name, body


def _logical_instructions(lines: List[str]):
    """Yield (start_line_no, joined_instruction) for each Dockerfile instruction.

    Backslash continuations are joined so a `RUN` spanning several physical lines is
    judged as one command -- the cache mount, the install and its flags usually live
    on different lines.
    """
    buf: List[str] = []
    start = 0
    for idx, raw in enumerate(lines, 1):
        stripped = raw.strip()
        if not buf:
            if not stripped or stripped.startswith("#"):
                continue
            start = idx
        buf.append(stripped.rstrip("\\").strip())
        if not stripped.endswith("\\"):
            yield start, " ".join(buf)
            buf = []
    if buf:
        yield start, " ".join(buf)


# A build image in a RUNTIME stage ships its compilers and credential helpers to
# production. Anchored tightly: a looser `_IMAGE` would match
# CI_DEPENDENCY_PROXY_GROUP_IMAGE_PREFIX, and `BASE_IMAGE` would match BASE_IMAGE_REPO.
_BUILD_IMAGE_VAR = re.compile(r"\$\{?\w*(?:_BUILD_IMAGE|_BUILDER_IMAGE|TOOLKIT\w*_IMAGE)\b", re.I)
_BUILD_STAGE_NAME = re.compile(r"^(build|builder|deps|dependencies|compile|sdk|toolkit)", re.I)
# Process supervisors that make a sane PID 1: forward signals, reap zombies.
_INIT_ARGV0 = {"dumb-init", "tini", "catatonit"}


def _stages(lines: List[str]):
    """Yield (from_line_no, image_ref, stage_name, [(line_no, instruction), ...]).

    Built on _logical_instructions: a `HEALTHCHECK ... \\` continued onto a line that
    starts `CMD [` is one instruction, not two, and a 70-line `RUN` containing
    `for f in ...; do \\` must not read as a dozen.
    """
    stages: List[tuple] = []
    cur = None
    for line_no, ins in _logical_instructions(lines):
        toks = ins.split()
        if not toks:
            continue
        if toks[0].upper() == "FROM":
            image = next((t for t in toks[1:] if not t.startswith("--")), "")
            low = [t.lower() for t in toks]
            name = toks[low.index("as") + 1] if "as" in low and low.index("as") + 1 < len(toks) else None
            cur = (line_no, image, name, [])
            stages.append(cur)
        elif cur is not None:
            cur[3].append((line_no, ins))
    return stages


def _ops(stage) -> List[str]:
    return [i.split(None, 1)[0].upper() for _, i in stage[3] if i.split()]


def _argv0(instruction: str) -> str:
    """First word of a CMD/ENTRYPOINT, in either exec or shell form."""
    body = instruction.split(None, 1)[1].strip() if len(instruction.split(None, 1)) > 1 else ""
    if body.startswith("["):
        try:
            parsed = json.loads(body)
            return str(parsed[0]) if parsed else ""
        except (ValueError, IndexError):
            return ""
    return body.split()[0] if body.split() else ""


def _exec_form(instruction: str) -> Optional[List[str]]:
    """The JSON argv of an exec-form CMD/ENTRYPOINT, or None for shell form."""
    body = instruction.split(None, 1)[1].strip() if len(instruction.split(None, 1)) > 1 else ""
    if not body.startswith("["):
        return None
    try:
        parsed = json.loads(body)
    except ValueError:
        return None
    return [str(p) for p in parsed] if isinstance(parsed, list) else None


_VAR_REF = re.compile(r"\$\{?([A-Za-z_][A-Za-z0-9_]*)")


def _image_refs(lines: List[str]) -> List[Tuple[int, str]]:
    """(line, reference) for every place Docker expects an image: FROM and COPY --from."""
    out: List[Tuple[int, str]] = []
    for line_no, ins in _logical_instructions(lines):
        toks = ins.split()
        if not toks:
            continue
        op = toks[0].upper()
        if op == "FROM":
            out.append((line_no, next((t for t in toks[1:] if not t.startswith("--")), "")))
        elif op == "COPY":
            out += [(line_no, t[len("--from="):]) for t in toks[1:] if t.startswith("--from=")]
    return out


def _image_position_vars(lines: List[str]) -> Tuple[Set[str], Set[str]]:
    """(whole, partial): ARGs that ARE an image reference, and ARGs that are only part of one.

    `FROM ${BASE}` makes BASE a whole image. In `FROM ${REGISTRY}/${PROJECT}/base:${TAG}`
    each ARG is a fragment: its default alone is not an image, the resolved reference is.
    """
    whole: Set[str] = set()
    partial: Set[str] = set()
    for _, ref in _image_refs(lines):
        m = re.fullmatch(r"\$\{?([A-Za-z_][A-Za-z0-9_]*)\}?", ref)
        if m:
            whole.add(m.group(1))
        else:
            partial |= set(_VAR_REF.findall(ref))
    return whole, partial


def _is_image_arg(name: str, image_vars: Tuple[Set[str], Set[str]]) -> bool:
    whole, partial = image_vars
    return name in whole or (name not in partial and bool(re.search(r"IMAGE$", name, re.I)))


def _ci_build_args(root: Path) -> Set[str]:
    """ARG names the CI pipeline passes in (gitlab-ci-library `DOCKER_BUILD_ARG_<NAME>`)."""
    ci = root / ".gitlab-ci.yml"
    try:
        text = ci.read_text(encoding="utf-8", errors="ignore") if ci.exists() else ""
    except OSError:
        text = ""
    return set(re.findall(r"DOCKER_BUILD_ARG_([A-Za-z0-9_]+)\s*:", text))


# Registries anyone can pull from. Renovate needs a `# renovate:` hint for an image version
# held in an ARG; private and internal registries are not tracked that way.
_PUBLIC_REGISTRY_HOSTS = {
    "docker.io", "index.docker.io", "registry-1.docker.io", "ghcr.io", "gcr.io", "k8s.gcr.io",
    "registry.k8s.io", "quay.io", "public.ecr.aws", "mcr.microsoft.com", "docker.elastic.co",
    "nvcr.io", "registry.access.redhat.com", "registry.redhat.io", "cgr.dev",
}


def _is_public_image(ref: str) -> bool:
    """True when a resolved image reference points at a public registry."""
    ref = ref.strip().strip("'\"")
    if not ref or "$" in ref:
        return False            # unresolved: supplied by CI, registry unknown
    first = ref.split("/", 1)[0]
    has_host = "/" in ref and ("." in first or ":" in first or first == "localhost")
    if not has_host:
        return True             # Docker Hub shorthand (python:3.12, org/image:tag)
    host = first.split(":", 1)[0].lower()
    return host in _PUBLIC_REGISTRY_HOSTS or host.endswith(".gcr.io")


def _opaque_block_keys(values_text: str) -> Set[str]:
    """Dotted keys annotated `# @default -- Check values.yaml` in a values.yaml.

    Those are rendered wholesale through `toYaml`, so the block is a single value and
    its children are not part of the key contract.
    """
    out: Set[str] = set()
    stack: List[Tuple[int, str]] = []
    pending = False
    for raw in values_text.splitlines():
        stripped = raw.strip()
        if stripped.startswith("#"):
            if "@default -- Check values.yaml" in stripped:
                pending = True
            continue
        if not stripped:
            continue
        match = re.match(r"^(\s*)([A-Za-z0-9_.\-]+):", raw)
        if not match:
            continue
        indent = len(match.group(1))
        key = match.group(2)
        while stack and stack[-1][0] >= indent:
            stack.pop()
        dotted = f"{stack[-1][1]}.{key}" if stack else key
        stack.append((indent, dotted))
        if pending:
            out.add(dotted)
            pending = False
    return out


# Keys tpl-library reads but a consumer is never expected to restate verbatim -- either they
# are the consumer's own identity, or a nested example-only block.
_VALUES_PARITY_IGNORE = {"component", "subComponent"}


_WRAPPER_SMELL = {"app", "application", "config", "configuration", "settings", "env", "params"}


def check_app_values_shape(chart_dir: Path, tpl_library_values: Path) -> List[Finding]:
    """Consumer application values: top level, ahead of the workload plumbing.

    The library declares no key for application configuration, so whatever the app needs
    is the consumer's to add. Two shapes go wrong on their own (helm-tpl-library chart
    standards, *Application values*): wrapping the domain groups in an `app:` envelope, which lengthens every
    reference a deployer types for no gain, and appending them after the library's keys,
    which buries the only section anyone opens the file for behind hundreds of lines of
    plumbing they inherit and never touch.
    """
    findings: List[Finding] = []
    values_file = chart_dir / "values.yaml"
    if not values_file.exists() or not tpl_library_values.exists():
        return findings
    try:
        import yaml  # noqa: WPS433
        raw = values_file.read_text(encoding="utf-8")
        consumer = yaml.safe_load(raw) or {}
        library = yaml.safe_load(tpl_library_values.read_text(encoding="utf-8")) or {}
    except Exception:
        return findings
    if not isinstance(consumer, dict) or not isinstance(library, dict):
        return findings

    lib_keys = set(library)
    rel = f"{chart_dir.name}/values.yaml"

    # --- an invented envelope around the domain groups ----------------------
    for key in consumer:
        if key in lib_keys or key not in _WRAPPER_SMELL:
            continue
        val = consumer[key]
        if isinstance(val, dict) and any(isinstance(v, dict) for v in val.values()):
            inner = sorted(k for k, v in val.items() if isinstance(v, dict))
            findings.append(Finding(
                "P2", "Application values wrapped in an envelope", rel,
                f"`{key}:` wraps {', '.join(inner[:4])}, so every reference reads "
                f"`.Values.{key}.{inner[0]}...` instead of `.Values.{inner[0]}...`. "
                "Lift the domain groups to the top level; the library declares none of "
                "these names, so nothing collides (helm-tpl-library chart standards, Application values).",
            ))

    # --- appended after the plumbing instead of ahead of it -----------------
    order = [m.group(1) for m in re.finditer(r"^([A-Za-z_][\w-]*):", raw, re.M)]
    if "restartPolicy" in order:
        cut = order.index("restartPolicy")
        late = [k for k in order[cut:] if k not in lib_keys]
        if late:
            findings.append(Finding(
                "P2", "Application values sit below the workload plumbing", rel,
                f"{', '.join(late[:4])} "
                f"{'appears' if len(late) == 1 else 'appear'} after `restartPolicy:`. Application "
                "configuration is what a deployer edits; the plumbing is inherited. Move "
                "these above `restartPolicy:` and keep the library's own key order "
                "otherwise, so the replica still diffs cleanly (helm-tpl-library chart standards).",
            ))
    return findings


def check_values_parity(chart_dir: Path, tpl_library_values: Path) -> List[Finding]:
    """Consumer values.yaml must mirror the library's values.yaml key-for-key.

    The library file is the contract. A key omitted because it is "empty anyway" is
    invisible to the next person: they cannot tell whether the chart opted out of a
    feature or never knew it existed, and `helm-docs` silently drops the row. Optional
    keys stay, with their `### Example` block, carrying the library's own default.
    """
    findings: List[Finding] = []
    values_file = chart_dir / "values.yaml"
    if not values_file.exists() or not tpl_library_values.exists():
        return findings
    try:
        import yaml  # noqa: WPS433
        consumer = yaml.safe_load(values_file.read_text(encoding="utf-8")) or {}
        library = yaml.safe_load(tpl_library_values.read_text(encoding="utf-8")) or {}
    except Exception:
        return findings

    def paths(node: Any, prefix: str = "") -> Set[str]:
        out: Set[str] = set()
        if not isinstance(node, dict):
            return out
        for key, val in node.items():
            dotted = f"{prefix}.{key}" if prefix else str(key)
            out.add(dotted)
            # Below a user-defined map (mounts.pvc.<name>, containers.<name>) the key
            # names are the consumer's own, so only the two fixed levels are compared.
            if isinstance(val, dict) and dotted.count(".") < 3:
                out |= paths(val, dotted)
        return out

    # An opaque block -- the library annotates it `# @default -- Check values.yaml` --
    # is ONE value rendered wholesale through toYaml, not a container of separately
    # documented keys (see the comments law). Its sub-keys are the consumer's to shape:
    # `strategy: {type: Recreate}` is complete, and demanding `strategy.rollingUpdate`
    # under it would be demanding an invalid manifest.
    opaque = _opaque_block_keys(tpl_library_values.read_text(encoding="utf-8"))
    lib_paths = {
        p for p in paths(library)
        if not any(p.startswith(o + ".") for o in opaque)
    }
    consumer_paths = paths(consumer)

    # `containers.<name>` is a free map -- "main" is the library's example, not a fixed
    # name -- so the container contract is compared against each container the consumer
    # `containers.<name>` is a free map -- "main" is the library's example, not a fixed
    # name -- so the container contract is compared against each container the consumer
    # actually declares.
    container_contract = {p for p in lib_paths if p.startswith("containers.main.")}
    lib_paths -= container_contract
    lib_paths.discard("containers.main")
    consumer_containers = consumer.get("containers")
    if isinstance(consumer_containers, dict) and consumer_containers:
        for cname in consumer_containers:
            for cp in container_contract:
                lib_paths.add(cp.replace("containers.main.", f"containers.{cname}.", 1))
    else:
        findings.append(Finding(
            "P1", "values.yaml Not A Library Replica", f"{chart_dir.name}/values.yaml",
            "values.yaml declares no containers. tpl-library renders the workload from "
            "`containers.<name>`; without one the chart produces a pod with no container."
        ))

    # Optional entrypoints rendered by specialized template helpers:
    # persistence (tpl.pvc), cronjobs (tpl.cronjob), jobs (tpl.job), metrics (tpl.servicemonitor / tpl.podmonitor).
    # If templates do not call the helper, the corresponding section is omitted from consumer values.yaml.
    # If the section is configured and active in values.yaml, the template must include the helper.
    templates_dir = chart_dir / "templates"
    manifest_files = [chart_dir / "templates" / "manifest.yaml"] if (chart_dir / "templates" / "manifest.yaml").exists() else (
        list(templates_dir.glob("*.yaml")) + list(templates_dir.glob("*.tpl")) if templates_dir.is_dir() else []
    )
    manifest_texts = [mf.read_text(encoding="utf-8") for mf in manifest_files if mf.is_file()]

    def _has_include(name: str) -> bool:
        pattern = re.compile(rf'include\s+["\']{re.escape(name)}["\']')
        return any(pattern.search(txt) for txt in manifest_texts)

    has_tpl_pvc = _has_include("tpl.pvc")
    if not has_tpl_pvc:
        lib_paths = {p for p in lib_paths if p != "persistence" and not p.startswith("persistence.")}

    consumer_persistence = consumer.get("persistence")
    if isinstance(consumer_persistence, dict):
        has_active_pvc = any(
            isinstance(v, dict) and v.get("enabled") is True and not v.get("existingClaim")
            for v in consumer_persistence.values()
        )
        if has_active_pvc and not has_tpl_pvc:
            findings.append(Finding(
                "P1", "Missing tpl.pvc in manifest.yaml", f"{chart_dir.name}/templates/manifest.yaml",
                "`persistence` defines active PersistentVolumeClaim entries in values.yaml, "
                "but `tpl.pvc` is not invoked in templates/manifest.yaml. The claims will not be created. "
                "Add `{{ include \"tpl.pvc\" . }}` in manifest.yaml."
            ))

    has_tpl_cronjob = _has_include("tpl.cronjob")
    if not has_tpl_cronjob:
        lib_paths = {p for p in lib_paths if p != "cronjobs" and not p.startswith("cronjobs.") and p != "cronjob" and not p.startswith("cronjob.")}

    consumer_cronjobs = consumer.get("cronjobs") or consumer.get("cronjob")
    if isinstance(consumer_cronjobs, dict):
        has_active_cronjob = any(
            isinstance(v, dict) and bool(v.get("schedule")) and not v.get("suspend", False)
            for v in consumer_cronjobs.values()
        )
        if has_active_cronjob and not has_tpl_cronjob:
            findings.append(Finding(
                "P1", "Missing tpl.cronjob in manifest.yaml", f"{chart_dir.name}/templates/manifest.yaml",
                "`cronjobs` defines active CronJob entries in values.yaml, "
                "but `tpl.cronjob` is not invoked in templates/manifest.yaml. The CronJobs will not be created. "
                "Add `{{- include \"tpl.cronjob\" ... }}` in manifest.yaml."
            ))

    has_tpl_job = _has_include("tpl.job")
    if not has_tpl_job:
        lib_paths = {p for p in lib_paths if p != "jobs" and not p.startswith("jobs.") and p != "job" and not p.startswith("job.")}

    consumer_jobs = consumer.get("jobs") or consumer.get("job")
    if isinstance(consumer_jobs, dict):
        has_active_job = any(
            isinstance(v, dict) and v.get("enabled") is True
            for v in consumer_jobs.values()
        )
        if has_active_job and not has_tpl_job:
            findings.append(Finding(
                "P1", "Missing tpl.job in manifest.yaml", f"{chart_dir.name}/templates/manifest.yaml",
                "`jobs` defines active Job entries in values.yaml, "
                "but `tpl.job` is not invoked in templates/manifest.yaml. The Jobs will not be created. "
                "Add `{{- include \"tpl.job\" ... }}` in manifest.yaml."
            ))

    has_tpl_metrics = _has_include("tpl.servicemonitor") or _has_include("tpl.podmonitor")
    if not has_tpl_metrics:
        # global.metrics only feeds tpl.servicemonitor/podmonitor, so it goes with them.
        lib_paths = {
            p for p in lib_paths
            if p not in ("metrics", "global.metrics")
            and not p.startswith(("metrics.", "global.metrics."))
        }

    # No tpl template reads global.tracing; it documents the app's own OTEL settings, so a
    # chart keeps it only when the application actually consumes tracing configuration.
    lib_paths = {p for p in lib_paths if p != "global.tracing" and not p.startswith("global.tracing.")}

    consumer_global = consumer.get("global") or {}
    consumer_metrics_global = consumer_global.get("metrics") or {}
    is_metrics_enabled = consumer_metrics_global.get("enabled") is True
    consumer_metrics = consumer.get("metrics")
    if isinstance(consumer_metrics, dict):
        endpoints = consumer_metrics.get("endpoints")
        if is_metrics_enabled and isinstance(endpoints, list) and len(endpoints) > 0 and not has_tpl_metrics:
            findings.append(Finding(
                "P1", "Missing tpl.servicemonitor in manifest.yaml", f"{chart_dir.name}/templates/manifest.yaml",
                "`metrics.endpoints` defines scrape endpoints in values.yaml and global.metrics.enabled is true, "
                "but neither `tpl.servicemonitor` nor `tpl.podmonitor` is invoked in templates/manifest.yaml. "
                "Add `{{ include \"tpl.servicemonitor\" . }}` in manifest.yaml."
            ))

    missing = sorted(p for p in lib_paths - consumer_paths if p not in _VALUES_PARITY_IGNORE)
    if missing:
        shown = ", ".join(missing[:12])
        more = f" (+{len(missing) - 12} more)" if len(missing) > 12 else ""
        findings.append(Finding(
            "P1", "values.yaml Not A Library Replica", f"{values_file.parent.name}/values.yaml",
            f"Keys present in the tpl-library values contract are missing from the chart: {shown}{more}. "
            "The consumer values.yaml is a replica of the library's values.yaml: every key stays, "
            "even when optional and empty, with its comment block and its `### Example`. Copy the "
            "library file and override the values this application needs."
        ))
    return findings


def check_container_overrides(chart_dir: Path) -> List[Finding]:
    """Charts leave the image's ENTRYPOINT and CMD alone: `command: []`, `args: []`.

    A container `command:` replaces the image ENTRYPOINT, so dumb-init silently stops
    being PID 1. `args:` replaces CMD, and a `/bin/sh -c "a; b"` chain there hides the
    process contract in values instead of the image. Both belong in the image: its CMD,
    or a MODE dispatcher script that `exec`s each branch. Overlays (`values.*.yaml`) and
    job containers are held to the same rule; cronjobs reuse the root containers.
    """
    findings: List[Finding] = []
    try:
        import yaml  # noqa: WPS433
    except ImportError:
        return findings
    for vf in sorted(chart_dir.glob("values*.yaml")):
        try:
            data = yaml.safe_load(vf.read_text(encoding="utf-8")) or {}
        except Exception:
            continue
        if not isinstance(data, dict):
            continue
        groups = [("containers", data.get("containers"), True),
                  ("initContainers", data.get("initContainers"), False)]
        jobs = data.get("jobs")
        if isinstance(jobs, dict):
            for jname, job in jobs.items():
                if isinstance(job, dict):
                    groups.append((f"jobs.{jname}.containers", job.get("containers"), True))
                    groups.append((f"jobs.{jname}.initContainers", job.get("initContainers"), False))
        for prefix, cmap, is_workload in groups:
            if not isinstance(cmap, dict):
                continue
            for cname, cont in cmap.items():
                if not isinstance(cont, dict):
                    continue
                where = f"{vf.name}:{prefix}.{cname}"
                if cont.get("command"):
                    findings.append(Finding(
                        "P1" if is_workload else "P2", "Chart Overrides Image Entrypoint", where,
                        "`command:` replaces the image ENTRYPOINT, so dumb-init is no longer PID 1 "
                        "and SIGTERM/zombie handling is lost. Keep `command: []` and put the process "
                        "in the image CMD (or a script it runs that ends with `exec`)."))
                if cont.get("args"):
                    findings.append(Finding(
                        "P1" if is_workload else "P2", "Chart Overrides Image Command", where,
                        "`args:` replaces the image CMD. Keep `args: []` (tpl-library standard) and move "
                        "the start sequence into the image: CMD, or a MODE dispatcher script selected "
                        "by a `mode` value rendered into configmapEnvs."))
    return findings


def check_dockerfile(df_file: Path, shape: Optional[str] = None) -> List[Finding]:
    findings: List[Finding] = []
    if not df_file.exists():
        return findings

    content = df_file.read_text(encoding="utf-8")
    lines = content.splitlines()
    stages = _stages(lines)

    # A CI runner image built for OpenShift takes an arbitrary assigned UID and grants the
    # root GROUP instead of naming a user. Demanding 'USER 10001:10001' of it is wrong, so
    # recognise the idiom rather than reporting three findings against a correct file.
    group_zero = bool(re.search(r"chgrp\s+-R\s+0\b", content)) and bool(
        re.search(r"chmod\s+-R\s+g=u\b", content))

    forbidden_commands = [
        (r"\b(npm\s+run\s+build|yarn\s+build|pnpm\s+build)\b", "P0", "Dockerfile runs compilation build script ('npm/yarn/pnpm build'). Build MUST happen in Project:Build job, not in image packaging."),
        (r"\b(npx\s+tsc)\b", "P0", "Dockerfile runs TypeScript compiler ('npx tsc'). Compilation must occur in CI build stage."),
        (r"\b(mvn\s+clean|mvn\s+package|mvn\s+compile|gradle\s+build)\b", "P0", "Dockerfile runs Maven/Gradle compilation. Build artifacts (target/*.jar) must be built in CI and copied."),
        (r"\b(go\s+build)\b", "P0", "Dockerfile compiles Go binary ('go build'). Binary must be compiled in Project:Build and copied."),
        (r"\b(pytest|vitest|jest|go\s+test|mvn\s+test)\b", "P0", "Dockerfile executes test suite. Tests MUST run in Project:Unit:Test job."),
    ]

    # Dependency resolution is packaging's twin failure, and the one interpreted stacks
    # hit. It is a defect when it can reach the network: an install that resolves online
    # at image-build time re-resolves what the pipeline already pinned and scanned.
    # Cache locations and, critically, handoff mechanics depend on the selected platform.
    dependency_installs = [
        (r"\b(npm\s+(ci|install|i)\b|yarn\s+install|pnpm\s+(install|i)\b)", ".npm", ".npm", "Node:Dependency:Download"),
        (r"\b(pip\s+install|uv\s+sync|uv\s+pip\s+install|poetry\s+install)\b", ".uv", ".uv-cache", "Python:Dependency:Download"),
        (r"\bgo\s+mod\s+download\b", ".cache", ".go-cache", "Go:Dependency:Download"),
        (r"\bmvn\s+dependency:go-offline\b", ".m2", ".m2", "Java:Dependency:Download"),
    ]

    known_stages = set()
    for line in lines:
        stripped = line.strip()
        if stripped.upper().startswith("FROM "):
            tokens = stripped.split()
            lower_tokens = [t.lower() for t in tokens]
            if "as" in lower_tokens:
                as_idx = lower_tokens.index("as")
                if as_idx + 1 < len(tokens):
                    known_stages.add(tokens[as_idx + 1])

    has_copy = False
    proxy_reported: Set[int] = set()
    declared_arg_defaults: Set[str] = set()
    image_vars = _image_position_vars(lines)
    ci_args = _ci_build_args(df_file.parent)
    arg_defaults: Dict[str, str] = {}
    for _, ins in _logical_instructions(lines):
        if ins.split() and ins.split()[0].upper() == "ARG":
            for tok in ins.split()[1:]:
                if "=" in tok:
                    k, v = tok.split("=", 1)
                    arg_defaults.setdefault(k, v.strip().strip("'\""))

    def _resolved(ref: str) -> str:
        return _VAR_SUB.sub(lambda m: arg_defaults.get(m.group(1) or m.group(2), "$" + (m.group(1) or m.group(2))), ref)

    for line_no, line in enumerate(lines, start=1):
        stripped = line.strip()
        if stripped.startswith("#"):
            continue

        if _PLATFORM == "gitlab" and stripped.upper().startswith("FROM "):
            tokens = stripped.split()
            image_token = None
            for tok in tokens[1:]:
                if not tok.startswith("--"):
                    image_token = tok
                    break
            # A reference assembled from ARG fragments is judged whole, once resolved;
            # a bare `${VAR}` reference is judged on its ARG line instead.
            if image_token and "$" in image_token and not re.fullmatch(r"\$\{?[A-Za-z_]\w*\}?", image_token):
                resolved = _resolved(image_token)
                image_token = resolved if "$" not in resolved else image_token
            if image_token:
                unproxied, suggested = classify_unproxied_public_image(image_token, known_stages)
                if unproxied:
                    proxy_reported.add(line_no)
                    findings.append(Finding(
                        "P1", "Unproxied External Image", f"{df_file.name}:{line_no}",
                        f"Dockerfile base image '{image_token}' pulls from an external registry without cache. " + _image_remedy(suggested)
                    ))

        if _PLATFORM == "gitlab" and stripped.upper().startswith("ARG "):
            arg_def = stripped[4:].strip()
            # Support single or multiple ARG declarations on the same logical line (e.g. ARG VAR1=val1 VAR2=val2)
            arg_tokens = arg_def.split()
            for token in arg_tokens:
                if "=" in token:
                    var_name, var_val = token.split("=", 1)
                    declared_arg_defaults.add(var_name.strip())
                    # `ARG YQ_VERSION=4.53.6` is a version pin, not an image.
                    if re.search(r"_(VERSION|TAG)$", var_name.strip(), re.I):
                        continue
                    # Only an ARG that feeds an image reference names an image;
                    # `ARG BUILD_TRANSLATIONS="false"` is a build switch.
                    if not _is_image_arg(var_name.strip(), image_vars):
                        continue
                    unproxied, suggested = classify_unproxied_public_image(var_val.strip(), known_stages)
                    if unproxied:
                        findings.append(Finding(
                            "P1", "Unproxied External Image", f"{df_file.name}:{line_no}",
                            f"ARG '{var_name.strip()}' default image '{var_val.strip()}' pulls from an external registry without cache. " + _image_remedy(suggested)
                        ))
                else:
                    var_name = token.strip()
                    # CI passes it (DOCKER_BUILD_ARG_<NAME>), e.g. a prebuilt project base
                    # image in the project's own registry: a default here would only
                    # duplicate a value CI owns.
                    if var_name in ci_args or var_name.endswith("_PREFIX"):
                        continue
                    if var_name not in declared_arg_defaults and (
                        var_name.endswith("_IMAGE") or "_BASE_IMAGE" in var_name or "_BUILD_IMAGE" in var_name
                    ):
                        suggested_default = ""
                        for known_arg, default_img in [
                            ("PYTHON_312_MICRO_BASE_IMAGE", "grootantech/micro-python-3-12:latest"),
                            ("NODE_JS_24_MICRO_BASE_IMAGE", "grootantech/micro-node-24:latest"),
                            ("JAVA_25_MICRO_BASE_IMAGE", "grootantech/micro-java-25:latest"),
                            ("NGINX_MICRO_BASE_IMAGE", "grootantech/micro-nginx:latest"),
                            ("MICRO_ROOT_BASE_IMAGE", "grootantech/micro-root:latest"),
                            ("TOOLKIT_BUILD_IMAGE", "grootantech/toolkit:latest"),
                        ]:
                            if var_name == known_arg:
                                suggested_default = default_img
                                break
                        remedy = f" (e.g. '{var_name}={suggested_default}')" if suggested_default else f" (e.g. '{var_name}=grootantech/<image>:latest')"
                        findings.append(Finding(
                            "P2", "Missing Default Base Image in ARG", f"{df_file.name}:{line_no}",
                            f"ARG '{var_name}' declares no default enterprise image{remedy}. "
                            "Providing a ':latest' enterprise default enables local developer 'docker build' "
                            "without manual build-args, avoids unapproved public base images, and allows CI "
                            "to override with exact versioned tags."
                        ))

        if stripped.startswith("COPY "):
            has_copy = True

        for pat, sev, msg in forbidden_commands:
            if re.search(pat, line):
                findings.append(Finding(sev, "Dockerfile Packaging Violation", f"{df_file.name}:{line_no}", msg))

    # Dependency installs are judged per logical instruction, not per line: the cache
    # mount and the --offline flag routinely sit on a different physical line from the
    # command itself, joined by a backslash continuation.
    # An image-only repository has no application manifest, so any install in its
    # Dockerfile is tooling for the image itself, not project dependencies the pipeline
    # already resolved. The packaging rule has nothing to say about it.
    for start_line, instruction in (() if shape == "image-only" else _logical_instructions(lines)):
        for pat, gitlab_cache_dir, github_cache_dir, install_tpl in dependency_installs:
            if not re.search(pat, instruction):
                continue
            cache_dir = github_cache_dir if _PLATFORM == "github" else gitlab_cache_dir
            mounts_cache = re.search(
                r"--mount=type=(bind|cache)[^\s]*source=" + re.escape(cache_dir) + r"\b", instruction
            ) or re.search(r"--mount=type=cache[^\s]*target=[^\s]*" + re.escape(cache_dir) + r"\b", instruction)
            offline = re.search(r"--(offline|no-index)\b", instruction)
            if mounts_cache and offline:
                continue
            if mounts_cache and not offline:
                findings.append(Finding(
                    "P1", "Dockerfile Packaging Violation", f"{df_file.name}:{start_line}",
                    f"Dockerfile bind-mounts the {cache_dir} CI cache but does not pass --offline, so the "
                    "install can still fall through to the network and resolve something the pipeline "
                    "never scanned. Add --offline."
                ))
                continue
            if _PLATFORM == "github":
                remedy = (
                    " The current github-ci-library docker.yml does not restore the language "
                    "workflow's Actions cache into the Docker build context. Do not copy the "
                    "GitLab PROJECT_CACHE_KEY/cache: policy: pull recipe; an explicit cache or "
                    "artifact handoff in the image workflow is required before using an offline "
                    "BuildKit mount."
                )
            elif _PLATFORM == "gitlab":
                remedy = (
                    f" Warm the cache in {install_tpl}, restore it onto Image:Build with the "
                    "same PROJECT_CACHE_KEY and cache path (`cache: policy: pull`), then install "
                    "offline from a read-write BuildKit bind mount."
                )
            else:
                remedy = (
                    " Provide an explicit cache handoff from the selected CI platform into the "
                    "Docker build context, then install offline from a read-write BuildKit mount."
                )
            findings.append(Finding(
                "P1", "Dockerfile Packaging Violation", f"{df_file.name}:{start_line}",
                f"Dockerfile resolves dependencies at image-build time with no {cache_dir} cache mount, so "
                "it reaches the network and re-resolves what the pipeline already pinned and scanned. "
                f"{remedy} Suggested Dockerfile mount: "
                f"`RUN --mount=type=bind,source={cache_dir},target=/tmp/{cache_dir},rw ... --offline`. "
                f"Do NOT publish the dependency directory as an artifact -- it is cached, not artifacted."
            ))

    if not has_copy:
        findings.append(Finding(
            "P1", "Dockerfile Hygiene", f"{df_file.name}",
            "Dockerfile does not have any 'COPY' instructions. Image should package pre-built artifacts."
        ))

    final_user_line = None
    for line in lines:
        if line.strip().startswith("USER "):
            final_user_line = line.strip()

    if group_zero and not final_user_line:
        pass
    elif not final_user_line or "USER 0" in final_user_line or "USER root" in final_user_line:
        findings.append(Finding(
            "P1", "Container Security", f"{df_file.name}",
            "Container does not enforce a non-root final USER. Must strictly be 'USER 10001:10001' (or 'USER 10001')."
        ))
    elif len(final_user_line.split()) > 1 and final_user_line.split()[1] not in ["10001:10001", "10001"]:
        findings.append(Finding(
            "P1", "Hard UID/GID Restriction Violation", f"{df_file.name}",
            f"Dockerfile final user '{final_user_line}' violates the enterprise standard. Must strictly be 'USER 10001:10001' (or 'USER 10001')."
        ))

    # The opening half of the USER bracket, on the RUNTIME stage only. A builder stage is
    # discarded, so its user never ships -- and hadolint DL3002 ("last USER should not be
    # root") is evaluated per stage, so declaring USER 0 there would fail the lint job that
    # gates every consumer. Enforcing it on both tools at once is impossible; the stage
    # that ships is the one that matters.
    if stages and not group_zero:
        runtime = stages[-1]
        has_root = any(
            i.split()[0].upper() == "USER" and len(i.split()) > 1 and i.split()[1] in ("0", "root", "0:0")
            for _, i in runtime[3]
        )
        if not has_root:
            findings.append(Finding(
                "P2", "Missing Root Setup Declaration", f"{df_file.name}:{runtime[0]}",
                "No 'USER 0' after the runtime stage's FROM. The runtime stage opens with an "
                "explicit 'USER 0' for the setup phase and closes with 'USER 10001:10001' "
                "before the runtime instructions. Without the opening declaration the setup "
                "phase runs as whatever user the base image leaves behind, which can change "
                "under you. A builder stage takes no 'USER 0' (hadolint DL3002)."))

    has_chown = any("--chown=" in line for line in lines if line.strip().startswith("COPY "))
    if has_copy and not has_chown and not group_zero:
        findings.append(Finding(
            "P2", "Container Permissions", f"{df_file.name}",
            "COPY instructions should enforce non-root ownership using '--chown=10001:10001'."
        ))

    findings += _check_from_tags(df_file, stages, proxy_reported)
    findings += _check_runtime_base(df_file, stages)
    findings += _check_version_pins(df_file, lines)
    findings += _check_copy_from_tags(df_file, stages)
    findings += _check_runtime_instructions(df_file, stages, shape)

    return findings


def _check_runtime_base(df_file: Path, stages) -> List[Finding]:
    """The runtime stage must not be a build image -- every compiler in it ships."""
    if not stages:
        return []
    from_line, image, _name, _ = stages[-1]
    earlier = {st[2] for st in stages[:-1] if st[2]}
    if not (_BUILD_IMAGE_VAR.search(image) or (image in earlier and _BUILD_STAGE_NAME.match(image))):
        return []
    return [Finding(
        "P1", "Runtime From Build Image", f"{df_file.name}:{from_line}",
        f"The runtime stage is built FROM '{image}', a build image. Its compilers, package "
        f"managers and credential helpers all ship to production. Base the runtime stage on "
        f"a micro base image and COPY --from the builder stage.")]


_VAR_SUB = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}|\$([A-Za-z_][A-Za-z0-9_]*)")


def _has_renovate_above(lines: List[str], line_no: int) -> bool:
    idx = line_no - 2
    while idx >= 0 and not lines[idx].strip():
        idx -= 1
    while idx >= 0 and lines[idx].strip().startswith("#"):
        if lines[idx].strip().lower().startswith("# renovate:"):
            return True
        idx -= 1
    return False


def _check_version_pins(df_file: Path, lines: List[str]) -> List[Finding]:
    """An image version for a PUBLIC registry, pinned in an ARG, carries `# renovate:`.

    Renovate reads a literal `FROM image:tag` by itself, but a version held in an ARG is
    invisible to it without the annotation. Only ARGs that feed a FROM / COPY --from which
    resolves to a public registry (Docker Hub, GHCR, GCR, Quay, ECR Public, ...) are
    checked: private and internal registries, and ARGs that are not image versions, are not
    tracked this way. A bare `ARG YQ_VERSION` re-declares a global and is not a pin.
    """
    findings: List[Finding] = []
    defaults: Dict[str, str] = {}
    arg_lines: Dict[str, int] = {}
    for line_no, ins in _logical_instructions(lines):
        toks = ins.split()
        if toks and toks[0].upper() == "ARG":
            for tok in toks[1:]:
                if "=" in tok:
                    name, val = tok.split("=", 1)
                    defaults.setdefault(name, val.strip().strip("'\""))
                    arg_lines.setdefault(name, line_no)

    def resolve(ref: str) -> str:
        return _VAR_SUB.sub(lambda m: defaults.get(m.group(1) or m.group(2), "$" + (m.group(1) or m.group(2))), ref)

    def is_pin(name: str, val: str) -> bool:
        if not val or "$" in val:
            return False
        if re.search(r"_(VERSION|TAG)$", name, re.I):
            return val.lower() != "latest"
        tail = val.rsplit("/", 1)[-1]
        return "@sha256:" in val or (":" in tail and tail.split(":", 1)[1].lower() != "latest")

    flagged: Set[str] = set()
    for _line_no, ins in _logical_instructions(lines):
        toks = ins.split()
        if not toks:
            continue
        refs: List[str] = []
        if toks[0].upper() == "FROM":
            refs.append(next((t for t in toks[1:] if not t.startswith("--")), ""))
        elif toks[0].upper() == "COPY":
            refs += [t[len("--from="):] for t in toks[1:] if t.startswith("--from=")]
        for ref in refs:
            names = [a or b for a, b in _VAR_SUB.findall(ref)]
            if not names:
                continue
            resolved = resolve(ref)
            if not _is_public_image(resolved):
                continue
            for name in names:
                if name in flagged or name not in defaults or not is_pin(name, defaults[name]):
                    continue
                if _has_renovate_above(lines, arg_lines[name]):
                    continue
                flagged.add(name)
                findings.append(Finding(
                    "P2", "Unannotated Version Pin", f"{df_file.name}:{arg_lines[name]}",
                    f"ARG '{name}' pins {defaults[name]} for the public image '{resolved}' with no "
                    f"'# renovate:' annotation on the line above, so the bot cannot see it. "
                    f"Pins for private registries need no annotation."))
    return findings


def _check_from_tags(df_file: Path, stages, skip_lines: Set[int]) -> List[Finding]:
    """A `FROM` with no tag, or `:latest`, on every platform.

    The dependency-proxy check above is GitLab-only because GitHub has no proxy to route
    through. Pinning is not platform-specific, so it is checked here for both -- otherwise
    a GitHub Dockerfile gets no base-image check at all. hadolint's DL3006/DL3007 cover the
    same ground inside the CI lint job; this catches it during onboarding and migration,
    before any pipeline has run.

    `skip_lines` carries the lines that already produced an unproxied finding, so a GitLab
    repo does not get two findings on one line for the same reference.
    """
    findings: List[Finding] = []
    names = {st[2] for st in stages if st[2]}
    for from_line, image, _name, _body in stages:
        if from_line in skip_lines or not image:
            continue
        if "$" in image or image.lower() == "scratch" or image in names:
            continue
        if "@sha256:" in image:
            continue
        tail = image.rsplit("/", 1)[-1]
        tag = tail.split(":", 1)[1] if ":" in tail else None
        if tag and tag.lower() != "latest":
            continue
        findings.append(Finding(
            "P1", "Floating Base Image Tag", f"{df_file.name}:{from_line}",
            f"Base image '{image}' is {'untagged' if not tag else 'pinned to :latest'}, so "
            f"the image built today is not the one built yesterday. " + _image_remedy(image)))
    return findings


def _check_copy_from_tags(df_file: Path, stages) -> List[Finding]:
    """`COPY --from=<image>` pulls exactly like FROM, but hadolint DL3006/DL3007 do not
    reach it -- so a floating tag there is checked here rather than by the linter."""
    findings: List[Finding] = []
    names = {st[2] for st in stages if st[2]}
    for st in stages:
        for line_no, ins in st[3]:
            if not ins.upper().startswith("COPY "):
                continue
            m = re.search(r"--from=(\S+)", ins)
            if not m:
                continue
            ref = m.group(1)
            if ref in names or ref.isdigit() or "$" in ref:
                continue
            tail = ref.rsplit("/", 1)[-1]
            tag = tail.split(":", 1)[1] if ":" in tail else None
            if "@sha256:" in ref or (tag and tag.lower() != "latest"):
                continue
            findings.append(Finding(
                "P1", "Floating Copy Source", f"{df_file.name}:{line_no}",
                f"COPY --from='{ref}' is {'untagged' if not tag else 'pinned to :latest'}. "
                f"What it copies in today is not what it copied yesterday. Pin an explicit "
                f"tag."))
    return findings


def _check_runtime_instructions(df_file: Path, stages, shape: Optional[str]) -> List[Finding]:
    """EXPOSE and the CMD-over-ENTRYPOINT preference, on service images only.

    A CI runner image, a base image and a smoke-test image all legitimately have no port
    and no long-running process. detect_shape() already draws that line, so ask it rather
    than guessing from the presence of a CMD.
    """
    if shape not in ("service", "service-with-chart") or not stages:
        return []
    findings: List[Finding] = []
    runtime = stages[-1]
    ops = _ops(runtime)
    has_start = "CMD" in ops or "ENTRYPOINT" in ops
    has_expose = "EXPOSE" in ops

    if has_start and not has_expose:
        findings.append(Finding(
            "P2", "Missing EXPOSE", f"{df_file.name}",
            "Service image declares no EXPOSE. The port is the image's only self-describing "
            "contract, and the chart's containerPort cannot be checked against anything "
            "without it."))

    # PID 1 has one shape: ENTRYPOINT ["/usr/bin/dumb-init", "--"] and the process -- or a
    # start script that ends every branch with `exec` -- in CMD. The runtime stage declares
    # it itself: a base image's ENTRYPOINT is invisible here, and some bases ship a
    # shell-form one that drops CMD. Keeping the script in CMD (not ENTRYPOINT) means an
    # override such as `docker run <image> sh` still runs under dumb-init.
    if has_start:
        eps = [i for _, i in runtime[3] if i.split()[0].upper() == "ENTRYPOINT"]
        cmds = [i for _, i in runtime[3] if i.split()[0].upper() == "CMD"]
        argv = _exec_form(eps[-1]) if eps else None
        canonical = ["/usr/bin/dumb-init", "--"]
        where = f"{df_file.name}"
        if not eps:
            findings.append(Finding(
                "P1", "Missing PID 1 Init", where,
                "The runtime stage declares no ENTRYPOINT. Declare "
                "ENTRYPOINT [\"/usr/bin/dumb-init\", \"--\"] and keep the process in CMD: "
                "dumb-init forwards SIGTERM to the whole process group and reaps zombies, which a "
                "bare python/java/node PID 1 does not."))
        elif argv is None:
            findings.append(Finding(
                "P1", "Non-Canonical PID 1", where,
                "ENTRYPOINT is in shell form, which runs under /bin/sh and drops CMD. Use exactly "
                "ENTRYPOINT [\"/usr/bin/dumb-init\", \"--\"] with the process in CMD."))
        elif argv != canonical:
            base = argv[0].rsplit("/", 1)[-1] if argv else ""
            if base in _INIT_ARGV0:
                rest = [a for a in argv[1:] if a != "--"]
                findings.append(Finding(
                    "P1", "Non-Canonical PID 1", where,
                    f"ENTRYPOINT is {argv}. Keep it exactly [\"/usr/bin/dumb-init\", \"--\"]"
                    + (f" and move {rest} into CMD" if rest else "")
                    + ", so an overridden command still runs under dumb-init."))
            else:
                findings.append(Finding(
                    "P1", "Missing PID 1 Init", where,
                    f"The runtime stage starts {argv[0] if argv else 'nothing'!r} as PID 1. Declare "
                    "ENTRYPOINT [\"/usr/bin/dumb-init\", \"--\"] and move the process or start "
                    "script into CMD; the script ends every branch with `exec`."))
        elif not cmds:
            findings.append(Finding(
                "P1", "Missing CMD", where,
                "ENTRYPOINT is dumb-init, but no CMD names the process for it to run."))
        if cmds:
            findings += _check_start_script(df_file, runtime, cmds[-1])
    return findings


def _check_start_script(df_file: Path, runtime, cmd: str) -> List[Finding]:
    """A start script run from CMD replaces itself with the service: every branch `exec`s.

    Without `exec` the shell stays between dumb-init and the service, and the container's
    exit code is the shell's. A MODE dispatcher (`case "$MODE" in ...`) needs `exec` (or an
    `exit`) in each branch, and an unknown mode should exit non-zero.
    """
    argv = _exec_form(cmd) or cmd.split()[1:]
    script = next((a for a in argv if a.endswith((".sh", ".bash"))), "")
    if not script:
        return []
    base = script.rsplit("/", 1)[-1]
    sources = [
        df_file.parent / tok
        for _, ins in runtime[3] if ins.split()[0].upper() in ("COPY", "ADD")
        for tok in ins.split()[1:-1]
        if not tok.startswith("--") and tok.rsplit("/", 1)[-1] == base
    ]
    path = next((p for p in sources if p.is_file()), None)
    if not path:
        return []
    text = path.read_text(encoding="utf-8", errors="ignore")
    body = [ln.strip() for ln in text.splitlines() if ln.strip() and not ln.strip().startswith("#")]
    where = f"{df_file.name}:CMD {script}"
    if not any(re.match(r"^exec\s", ln) or " exec " in f" {ln} " for ln in body):
        return [Finding(
            "P2", "Start Script Without exec", where,
            f"'{base}' never uses `exec`, so the shell stays PID 1's child and the service's exit "
            "code is lost. End the script (every branch of a MODE dispatcher) with `exec <service>`.")]
    missing: List[str] = []
    in_case, label, has_exit = False, "", False
    for ln in body:
        if re.match(r"^case\b", ln):
            in_case = True
            continue
        if in_case and re.match(r"^esac\b", ln):
            in_case = False
            continue
        if not in_case:
            continue
        m = re.match(r"^([^()]+)\)\s*(.*)$", ln)
        if m and not label:
            label, rest = m.group(1).strip(), m.group(2)
            has_exit = bool(re.search(r"\b(exec|exit)\b", rest))
            if rest.endswith(";;"):
                if not has_exit:
                    missing.append(label)
                label = ""
            continue
        if label:
            if re.search(r"\b(exec|exit)\b", ln):
                has_exit = True
            if ln.endswith(";;"):
                if not has_exit:
                    missing.append(label)
                label = ""
    if missing:
        return [Finding(
            "P2", "Start Script Without exec", where,
            f"'{base}' branch(es) {', '.join(missing)} neither `exec` nor `exit`. Each MODE branch "
            "ends with `exec <service>`; an unknown mode exits non-zero.")]
    return []


def check_dockerignore(root: Path) -> List[Finding]:
    findings: List[Finding] = []
    df_file = root / "Dockerfile"
    di_file = root / ".dockerignore"

    if df_file.exists():
        if not di_file.exists():
            findings.append(Finding(
                "P1", "Missing .dockerignore", str(di_file),
                "Missing '.dockerignore'. All containerized repositories must include a '.dockerignore' using the inverted allowlist standard (*, !src, !dist, etc.)."
            ))
            return findings

        content = di_file.read_text(encoding="utf-8")
        lines = [line.strip() for line in content.splitlines() if line.strip() and not line.strip().startswith("#")]

        has_default_deny = any(line in ["*", "**", "**/*"] for line in lines[:3])
        if not has_default_deny:
            findings.append(Finding(
                "P2", ".dockerignore Non-Standard", str(di_file),
                "'.dockerignore' should follow the Inverted Allowlist Standard: block everything by default ('*' or '**') at the top."
            ))

        copies_context = False
        for _, ins in _logical_instructions(df_file.read_text(encoding="utf-8").splitlines()):
            op = ins.split(None, 1)[0].upper() if ins.split() else ""
            if op in ("COPY", "ADD") and "--from=" not in ins:
                copies_context = True
                break

        has_unignore = any(line.startswith("!") for line in lines)
        if copies_context and not has_unignore:
            findings.append(Finding(
                "P2", ".dockerignore Empty Allowlist", str(di_file),
                "'.dockerignore' denies everything, but the Dockerfile copies from the build "
                "context, so nothing it needs can reach it. Admit exactly those paths with '!'."
            ))

    return findings


def check_gitignore(root: Path, chart_dir: Optional[Path] = None) -> List[Finding]:
    findings: List[Finding] = []
    gi_file = root / ".gitignore"
    if not gi_file.exists():
        findings.append(Finding(
            "P1", "Missing .gitignore", str(gi_file),
            "Missing '.gitignore'. All repositories must maintain a '.gitignore' to prevent committing caches, secrets, and build artifacts."
        ))
        return findings

    content = gi_file.read_text(encoding="utf-8")
    existing_lines = {line.strip().strip("/").lower() for line in content.splitlines() if line.strip() and not line.strip().startswith("#")}

    # Detect stack requirements
    is_python = (root / "pyproject.toml").exists() or (root / "uv.lock").exists() or bool(list(root.glob("*.py")))
    is_node = (root / "package.json").exists()
    is_java = (root / "pom.xml").exists() or (root / "build.gradle").exists()
    is_go = (root / "go.mod").exists()

    has_chart = (
        (chart_dir is not None and (chart_dir / "Chart.yaml").exists())
        or (root / "Chart.yaml").exists()
        or (root / "chart" / "Chart.yaml").exists()
    )

    required_checks = [
        (".env", "Environment file '.env' must be ignored."),
    ]
    if has_chart:
        required_checks.extend([
            ("charts", "Helm dependency directory 'charts' must be ignored."),
            ("chart.lock", "Helm dependency lock file 'Chart.lock' must be ignored."),
        ])

    if is_python:
        required_checks.extend([
            (".uv", "Python package cache '.uv/' must be ignored."),
            (".pytest_cache", "Test cache '.pytest_cache/' must be ignored."),
            ("__pycache__", "Python bytecode '__pycache__/' must be ignored."),
            (".venv", "Virtual environment '.venv/' must be ignored."),
        ])

    if is_node:
        required_checks.extend([
            ("node_modules", "Dependencies 'node_modules/' must be ignored."),
            ("dist", "Build artifacts 'dist/' must be ignored."),
        ])

    if is_java:
        required_checks.extend([
            ("target", "Maven build directory 'target/' must be ignored."),
            (".gradle", "Gradle cache '.gradle/' must be ignored."),
        ])

    if is_go:
        required_checks.extend([
            ("bin", "Compiled binaries 'bin/' must be ignored."),
        ])

    for pattern, msg in required_checks:
        clean_pat = pattern.strip("/").lower()
        if clean_pat not in existing_lines and f"*.{clean_pat}" not in existing_lines:
            findings.append(Finding(
                "P2", "Missing .gitignore Pattern", str(gi_file.name),
                f"{msg} Add '{pattern}' to '.gitignore'."
            ))

    return findings


#: Patterns that must never appear unanchored. An unanchored pattern matches a basename at
#: ANY depth, so it reaches into charts/ and hides resolved dependencies from the loader.
_HELMIGNORE_FORBIDDEN = {
    "*.tgz": ("hides the dependency archives helm downloads into charts/. The chart then "
              "reports 'missing these dependencies' with the files plainly present, and any "
              "library-chart include fails with 'no template ... associated with template "
              "gotpl'. Neither message names .helmignore. Use '/*.tgz' to anchor it to the "
              "chart root, or drop it."),
    "charts": ("hides the entire resolved dependency directory. Never ignore it."),
    "charts/": ("hides the entire resolved dependency directory. Never ignore it."),
}

#: Entries whose absence leaves repo furniture inside the released artifact.
_HELMIGNORE_EXPECTED = (
    "Chart.lock",
    "README.gotmpl",
    ".gitlab-ci.yml",
    ".yamllint.yml",
    ".gitleaks.toml",
    "CODEOWNERS",
    "CONTRIBUTING.md",
    "LICENSE.md",
    "Makefile",
    "SECURITY.md",
    "VERSION",
    "test/",
    ".helmignore",
)


def check_helmignore(chart_dir: Path) -> List[Finding]:
    """Validate a chart's .helmignore.

    Split out from check_helm_chart because its failure mode is unlike every other chart
    defect: the chart is correct, the dependency is downloaded and intact, and helm still
    reports it missing. The cost of the one-line mistake is hours, so it is worth its own
    assertion rather than a line in a bigger check.
    """
    findings: List[Finding] = []
    hi_file = chart_dir / ".helmignore"

    if not hi_file.exists():
        findings.append(Finding(
            "P2", "Missing .helmignore", str(hi_file),
            "Chart has no '.helmignore', so repo furniture (CI config, README.gotmpl, "
            "Chart.lock, tests) is packaged into the released chart. Add the baseline."))
        return findings

    lines = [ln.strip() for ln in hi_file.read_text(encoding="utf-8").splitlines()]
    active = [ln for ln in lines if ln and not ln.startswith("#")]

    for pattern, why in _HELMIGNORE_FORBIDDEN.items():
        if pattern in active:
            findings.append(Finding(
                "P1", "Unanchored .helmignore Pattern", f"{hi_file.name}:{pattern}",
                f"'{pattern}' {why}"))

    missing = [e for e in _HELMIGNORE_EXPECTED
               if e not in active and e.rstrip("/") not in active]
    if missing:
        findings.append(Finding(
            "P2", "Incomplete .helmignore", str(hi_file.name),
            f"Missing baseline entries: {', '.join(missing)}. These end up inside the "
            f"packaged chart. The baseline is in the helm-tpl-library chart standards, Repository ignore files."))

    return findings


def check_helm_chart(chart_dir: Path) -> List[Finding]:
    findings: List[Finding] = []
    if not chart_dir.exists():
        findings.append(Finding("P1", "Missing Helm Chart", str(chart_dir), "Chart directory 'chart/' is missing. Standardized repos must include a Helm chart."))
        return findings

    chart_yaml_file = chart_dir / "Chart.yaml"
    values_yaml_file = chart_dir / "values.yaml"
    manifest_file = chart_dir / "templates" / "manifest.yaml"

    if not chart_yaml_file.exists():
        findings.append(Finding("P0", "Missing Chart.yaml", str(chart_yaml_file), "chart/Chart.yaml is missing."))
        return findings

    chart_content = chart_yaml_file.read_text(encoding="utf-8")

    # A `type: library` chart (tpl-library itself) publishes reusable templates and has no
    # manifest, no values contract, and no tpl-library dependency of its own. Applying the
    # application-chart standard to it produces nothing but false positives.
    if re.search(r"^type:\s*library\s*$", chart_content, re.MULTILINE):
        if not (chart_dir / "README.gotmpl").exists():
            findings.append(Finding(
                "P2", "Missing helm-docs Template", str(chart_dir / "README.gotmpl"),
                "Library chart should ship README.gotmpl so helm-docs can regenerate README.md."
            ))
        return findings

    # Verify dependency on tpl-library
    if "tpl-library" not in chart_content:
        findings.append(Finding(
            "P0", "Helm Template Library", str(chart_yaml_file),
            "Chart.yaml must declare a dependency on the enterprise 'tpl-library' library."
        ))

    # Verify Chart.yaml has a meaningful description
    desc_match = re.search(r"^description:\s*(.+)$", chart_content, re.MULTILINE)
    if not desc_match or not desc_match.group(1).strip() or len(desc_match.group(1).strip()) < 12:
        findings.append(Finding(
            "P2", "Chart Description Incomplete", str(chart_yaml_file),
            "Chart.yaml description is empty or too short. Define a meaningful project purpose."
        ))

    # Verify Chart.yaml name does not use generic -service
    name_match = re.search(r"^name:\s*(.+)$", chart_content, re.MULTILINE)
    c_name = ""
    if name_match:
        c_name = name_match.group(1).strip().strip("'\"")
        if c_name.endswith("-service") or c_name == "service":
            findings.append(Finding(
                "P2", "Generic Chart Name", str(chart_yaml_file),
                f"Chart name '{c_name}' ends in the generic '-service'. Suggest a name that says "
                f"what the workload does and confirm it with the user."
            ))

    # Verify manifest.yaml includes tpl.deployment
    if not manifest_file.exists():
        findings.append(Finding("P1", "Missing Manifest Template", str(manifest_file), "chart/templates/manifest.yaml is missing."))
    else:
        manifest_content = manifest_file.read_text(encoding="utf-8")
        if 'include "tpl.deployment"' not in manifest_content:
            findings.append(Finding(
                "P1", "Manifest Non-Standard", str(manifest_file),
                "manifest.yaml should include 'tpl.deployment' from tpl-library."
            ))

    # Verify README.gotmpl exists and contains overview/purpose
    readme_gotmpl = chart_dir / "README.gotmpl"
    if not readme_gotmpl.exists():
        findings.append(Finding(
            "P2", "Missing README.gotmpl", str(readme_gotmpl),
            "chart/README.gotmpl is missing. Helm charts must include README.gotmpl for helm-docs."
        ))
    else:
        gotmpl_content = readme_gotmpl.read_text(encoding="utf-8")
        if "Overview & Purpose" not in gotmpl_content and 'template "chart.description"' not in gotmpl_content:
            findings.append(Finding(
                "P2", "README.gotmpl Non-Standard", str(readme_gotmpl),
                "chart/README.gotmpl should include '## Overview & Purpose' and '{{ template \"chart.description\" . }}'."
            ))

    # Check values.yaml structure
    if not values_yaml_file.exists():
        findings.append(Finding("P0", "Missing values.yaml", str(values_yaml_file), "chart/values.yaml is missing."))
    else:
        val_content = values_yaml_file.read_text(encoding="utf-8")

        # subComponent is optional for a single-mode chart and suggested, never enforced
        # from a fixed vocabulary; check_modes() requires it where one chart runs several
        # modes. A generic 'service' only earns a suggestion.
        sub_match = re.search(r"^subComponent:\s*[\"']?([^\"'\n#]*)[\"']?", val_content, re.MULTILINE)
        sub_val = sub_match.group(1).strip() if sub_match else ""
        if sub_val in ("null", "~", '""', "''"):
            sub_val = ""
        if sub_val == "service":
            findings.append(Finding(
                "P2", "Generic subComponent", f"{values_yaml_file.name}:subComponent",
                "subComponent 'service' says nothing about the workload. Suggest the role or mode "
                "it runs (e.g. 'api', 'worker', 'frontend') and use what the user confirms."
            ))

        # partOf and component are mandatory: suggest values, confirm them with the user.
        part_of_match = re.search(r"partOf:\s*[\"']?([^\"'\n]*)[\"']?", val_content)
        rel_len_match = re.search(r"releaseNameLength:\s*(\d+)", val_content)

        if not part_of_match or not part_of_match.group(1).strip():
            findings.append(Finding(
                "P1", "Missing partOf", f"{values_yaml_file.name}:global.partOf",
                "global.partOf (the product name) is required. Suggest one from the repository "
                "and its sibling charts, confirm it with the user, and set releaseNameLength to "
                "its length."
            ))
        elif part_of_match:
            part_of_val = part_of_match.group(1).strip()
            if not part_of_val or part_of_val in ["myorg", "myproduct", "[PRODUCT_NAME]"]:
                findings.append(Finding(
                    "P1", "Product Name Standard", f"{values_yaml_file.name}:global.partOf",
                    f"global.partOf '{part_of_val}' is invalid. Must be set to confirmed product name in lowercase (e.g. 'myapp')."
                ))
            elif rel_len_match:
                rel_len = int(rel_len_match.group(1))
                if rel_len != len(part_of_val):
                    findings.append(Finding(
                        "P1", "Release Name Length Mismatch", f"{values_yaml_file.name}:global.releaseNameLength",
                        f"global.releaseNameLength ({rel_len}) does not match character length of global.partOf '{part_of_val}' ({len(part_of_val)})."
                    ))

        comp_match = re.search(r"^component:\s*[\"']?([^\"'\n#]*)[\"']?", val_content, re.MULTILINE)
        comp_val = comp_match.group(1).strip() if comp_match else ""
        if not comp_val:
            findings.append(Finding(
                "P1", "Missing component", f"{values_yaml_file.name}:component",
                "component is required. Suggest one (the product area this workload serves), "
                "confirm it with the user, and use what they say."
            ))

        # Chart name: '<partOf>-<component>' or '<partOf>-<component>-<subComponent>'. A
        # suggestion, not a rule -- the user may name the chart differently.
        if name_match and part_of_match and comp_val:
            part_of_val = part_of_match.group(1).strip()
            if part_of_val and part_of_val not in ["myorg", "myproduct", "[PRODUCT_NAME]"]:
                accepted = {f"{part_of_val}-{comp_val}"}
                if sub_val:
                    accepted.add(f"{part_of_val}-{comp_val}-{sub_val}")
                if c_name not in accepted:
                    findings.append(Finding(
                        "P2", "Chart Name Suggestion", str(chart_yaml_file),
                        f"Chart name '{c_name}' is neither {' nor '.join(sorted(accepted))}. Suggest one "
                        f"of those and keep whatever the user confirms."
                    ))

        # Check image repository formatting
        if "myorg/" in val_content or "[PRODUCT_NAME]" in val_content:
            findings.append(Finding(
                "P1", "Generic Image Repository", str(values_yaml_file),
                "Image repository contains generic placeholder ('myorg/' or '[PRODUCT_NAME]'). "
                "Set the repository CI pushes to (ask the user if unknown)."
            ))

        # A sibling-service URL stays overridable per environment: the templated default
        # sits behind `.Values.<service>.internalUrl`. Only URL-like keys are judged; a
        # sibling name used for something else (a PVC claimName) is not a URL.
        for raw_line in val_content.splitlines():
            if 'include "tpl.resource.siblingName"' not in raw_line or raw_line.strip().startswith("#"):
                continue
            key = raw_line.split(":", 1)[0].strip().strip("-").strip()
            if re.search(r"(url|uri|host|endpoint|addr|server)", key, re.I) and "internalUrl" not in raw_line:
                findings.append(Finding(
                    "P1", "Cross-Service Override Law Violation", f"{values_yaml_file.name}:{key}",
                    "Cross-service sibling URLs follow the Override-First Law: "
                    "'{{ tpl .Values.<service>.internalUrl $ | default (printf \"http://%s\" "
                    "(include \"tpl.resource.siblingName\" ...)) }}'."
                ))
                break

        # Check for database grouping
        if "DATABASE_" in val_content or "POSTGRES_" in val_content:
            if "database:" not in val_content:
                findings.append(Finding(
                    "P1", "Helm Values Standard", str(values_yaml_file),
                    "Database configurations must be organized under the '.Values.database' section."
                ))

        # Check for storage grouping
        if "S3_" in val_content or "BUCKET" in val_content:
            if "storage:" not in val_content:
                findings.append(Finding(
                    "P1", "Helm Values Standard", str(values_yaml_file),
                    "Storage/S3 configurations must be organized under the '.Values.storage' section."
                ))

        # Check Values Comment Law: Root/parent mapping keys should not have comments
        if re.search(r"#\s*--[^\n]*\n\s*containers:\s*$", val_content, re.MULTILINE):
            findings.append(Finding(
                "P2", "Values Comment Law Violation", str(values_yaml_file),
                "Parent key 'containers:' should not have '# --' comments; only leaf properties or toYaml blocks receive comments."
            ))

        # Check securityContext @default tag
        if "securityContext:" in val_content and "# -- Security context" in val_content:
            if "@default -- Check values.yaml" not in val_content:
                findings.append(Finding(
                    "P2", "Values Comment Law Violation", str(values_yaml_file),
                    "securityContext should include '# @default -- Check values.yaml' to avoid distorted markdown table rendering in helm-docs."
                ))

        # Check configmapEnvs / secretEnvs @default tag
        if "configmapEnvs:" in val_content and "# -- ConfigMap-based" in val_content:
            if "@default -- Check values.yaml" not in val_content:
                findings.append(Finding(
                    "P2", "Values Comment Law Violation", str(values_yaml_file),
                    "configmapEnvs and secretEnvs should include '# @default -- Check values.yaml' to prevent bloated markdown tables in helm-docs."
                ))

    # Check values.schema.json modularity and synchronization with values.yaml
    schema_file = chart_dir / "values.schema.json"
    values_data: Dict[str, Any] = {}
    if values_yaml_file.exists() and HAVE_YAML:
        try:
            values_data = yaml.safe_load(values_yaml_file.read_text(encoding="utf-8")) or {}
        except Exception:
            pass

    # Enforce hard UID/GID 10001:10001 restriction in Helm values
    if values_data:
        main_sec = values_data.get("containers", {}).get("main", {}).get("securityContext", {})
        pod_sec = values_data.get("pod", {}).get("securityContext", {})
        u = main_sec.get("runAsUser") if "runAsUser" in main_sec else pod_sec.get("runAsUser")
        g = main_sec.get("runAsGroup") if "runAsGroup" in main_sec else pod_sec.get("runAsGroup")
        if u != 10001 or g != 10001:
            findings.append(Finding(
                "P1", "Hard SecurityContext Law Violation (10001:10001)", str(values_yaml_file),
                f"Helm chart values.yaml must enforce 'runAsUser: 10001' and 'runAsGroup: 10001' (found runAsUser={u}, runAsGroup={g})."
            ))

    if not schema_file.exists():
        findings.append(Finding(
            "P1", "Missing values.schema.json", str(schema_file),
            "chart/values.schema.json is missing. Helm charts must include schema validation."
        ))
    else:
        try:
            schema_json = json.loads(schema_file.read_text(encoding="utf-8"))
            if not schema_json.get("$defs") and not schema_json.get("definitions"):
                findings.append(Finding(
                    "P2", "Monolithic Schema Warning", str(schema_file),
                    "values.schema.json should be split and modularized using '$defs' for database, storage, and container definitions."
                ))
            # Verify all top-level keys in values.yaml exist in values.schema.json properties
            if values_data:
                schema_props = schema_json.get("properties", {})
                missing_schema_keys = [k for k in values_data.keys() if k not in schema_props]
                if missing_schema_keys:
                    findings.append(Finding(
                        "P1", "Unsynchronized values.schema.json", str(schema_file),
                        f"Keys {missing_schema_keys} defined in 'chart/values.yaml' are missing from 'chart/values.schema.json'. "
                        "When values.yaml changes, values.schema.json must be updated as well."
                    ))
        except Exception:
            pass

    # Check README.md exists and matches what helm-docs generates now
    readme_file = chart_dir / "README.md"
    if not readme_file.exists():
        findings.append(Finding(
            "P1", "Missing chart/README.md", str(readme_file),
            f"chart/README.md is missing. Generate it inside chart/: `{HELM_DOCS_COMMAND}` "
            "(core/scripts/chart-docs.sh runs exactly that)."
        ))
    else:
        findings += _check_readme_drift(chart_dir, readme_file)

    return findings


#: The one helm-docs invocation, run inside the chart directory. Plain `helm-docs` renders a
#: different README (other ordering, no dependency values), which is how hand edits start.
HELM_DOCS_COMMAND = ("helm-docs --template-files README.gotmpl --sort-values-order file "
                     "--document-dependency-values")


def _check_readme_drift(chart_dir: Path, readme_file: Path) -> List[Finding]:
    """Regenerate the README in memory and compare, instead of trusting file timestamps.

    Needs `helm-docs` on PATH; without it drift cannot be proven, so nothing is reported.
    """
    import shutil  # noqa: WPS433
    import subprocess  # noqa: WPS433
    if not shutil.which("helm-docs") or not (chart_dir / "README.gotmpl").exists():
        return []
    try:
        out = subprocess.run(
            ["helm-docs", "--chart-search-root", ".", "--template-files", "README.gotmpl",
             "--sort-values-order", "file", "--document-dependency-values", "--dry-run"],
            cwd=str(chart_dir), capture_output=True, text=True, timeout=60)
    except Exception:
        return []
    if out.returncode != 0 or not out.stdout.strip():
        return []
    generated = out.stdout.strip()
    current = readme_file.read_text(encoding="utf-8").strip()
    if generated == current:
        return []
    return [Finding(
        "P2", "Outdated chart/README.md (helm-docs drift)", str(readme_file),
        f"chart/README.md differs from what helm-docs generates now. Regenerate it inside chart/ "
        f"with `{HELM_DOCS_COMMAND}` (core/scripts/chart-docs.sh); never edit README.md by hand."
    )]


#: AI/agent tooling that must never reach a commit. Matched on any path segment, so nested
#: copies (`services/api/CLAUDE.md`) count too.
_AI_TOOLING_DIRS = {".claude", ".agents", ".codex", ".gemini", ".cursor", ".windsurf", ".aider"}
_AI_TOOLING_FILES = {"AGENTS.md", "AGENT.md", "CLAUDE.md", "GEMINI.md", "GPT.md",
                     "skills-lock.json", ".cursorrules", ".windsurfrules", ".aider.conf.yml"}


def _git_paths(root: Path, *args: str) -> List[str]:
    try:
        import subprocess  # noqa: WPS433
        out = subprocess.run(["git", "-C", str(root), *args], capture_output=True, text=True, timeout=20)
        return [ln for ln in out.stdout.splitlines() if ln] if out.returncode == 0 else []
    except Exception:
        return []


def _ai_tooling(paths: List[str]) -> List[str]:
    hits: Set[str] = set()
    for p in paths:
        parts = p.split("/")
        for i, part in enumerate(parts):
            if part in _AI_TOOLING_DIRS:
                hits.add("/".join(parts[:i + 1]) + "/")
                break
        else:
            if parts[-1] in _AI_TOOLING_FILES:
                hits.add(p)
    return sorted(hits)


def check_project_hygiene(root: Path) -> List[Finding]:
    """Repository hygiene that is not about any one artefact.

    AI/agent tooling is reported, never removed: the user decides whether it is deleted.
    Dependency manifests and lockfiles are deliberately not judged here -- dependency
    management is outside this Skill.
    """
    findings: List[Finding] = []
    tracked = _ai_tooling(_git_paths(root, "ls-files"))
    if tracked:
        findings.append(Finding(
            "P2", "AI/Agent Files Tracked", str(root.name),
            f"Committed AI/agent tooling: {', '.join(tracked[:10])}"
            f"{' (+more)' if len(tracked) > 10 else ''}. These must not be committed. Ask the "
            "user whether to delete them; do not delete or commit anything without that answer."))
    untracked = _ai_tooling(_git_paths(root, "ls-files", "--others", "--exclude-standard"))
    if untracked:
        findings.append(Finding(
            "P2", "AI/Agent Files Present", str(root.name),
            f"Uncommitted AI/agent tooling in the working tree: {', '.join(untracked[:10])}. Never "
            "stage these; ask the user whether to delete them."))
    findings += check_changelog(root)
    return findings


def check_changelog(root: Path) -> List[Finding]:
    """The changelog's first line is its H1 (markdownlint MD041, which Changelog:Lint runs).

    A licence or HTML comment above the heading fails the lint; move it below the H1.
    """
    cl = root / "CHANGELOG.md"
    if not cl.exists():
        return []
    first = next((ln for ln in cl.read_text(encoding="utf-8", errors="ignore").splitlines() if ln.strip()), "")
    if first.startswith("# "):
        return []
    return [Finding(
        "P2", "Changelog Not Starting With H1", "CHANGELOG.md:1",
        f"CHANGELOG.md starts with '{first[:40]}' instead of its '# ' heading, so markdownlint "
        "MD041 fails. Move any licence or comment block below the H1.")]


# ---------------------------------------------------------------------------
# Chart runtime contract -- checks learned from charts that rendered but did not run.
# ---------------------------------------------------------------------------

def _load_yaml(path: Path) -> Any:
    if not HAVE_YAML or not path.exists():
        return None
    try:
        return yaml.safe_load(path.read_text(encoding="utf-8"))
    except Exception:
        return None


def _merge_values(base: Any, over: Any) -> Any:
    """Helm's values merge: maps merge, anything else replaces, null deletes the key."""
    if not isinstance(base, dict) or not isinstance(over, dict):
        return over
    out = dict(base)
    for key, val in over.items():
        if val is None:
            out.pop(key, None)
        elif isinstance(val, dict) and isinstance(out.get(key), dict):
            out[key] = _merge_values(out[key], val)
        else:
            out[key] = val
    return out


def _overlay_files(chart_dir: Path) -> List[Path]:
    """Per-release overlays: values.<release>.yaml (and the values-<release>.yaml variant)."""
    found = set(chart_dir.glob("values.*.yaml")) | set(chart_dir.glob("values-*.yaml"))
    return sorted(p for p in found if p.name != "values.yaml")


def _variants(chart_dir: Path) -> List[Tuple[str, Dict[str, Any], Dict[str, Any]]]:
    """(file name, effective values, raw overlay) for values.yaml and every overlay."""
    base = _load_yaml(chart_dir / "values.yaml")
    if not isinstance(base, dict):
        return []
    out = [("values.yaml", base, base)]
    for ov in _overlay_files(chart_dir):
        raw = _load_yaml(ov)
        if isinstance(raw, dict):
            out.append((ov.name, _merge_values(base, raw), raw))
    return out


def _manifest_includes(chart_dir: Path) -> Set[str]:
    tdir = chart_dir / "templates"
    texts = [p.read_text(encoding="utf-8", errors="ignore")
             for p in (list(tdir.glob("*.yaml")) + list(tdir.glob("*.tpl")) if tdir.is_dir() else [])]
    return {m for t in texts for m in re.findall(r'include\s+["\'](tpl\.[\w.]+)["\']', t)}


def check_chart_runtime(chart_dir: Path) -> List[Finding]:
    """Ports, probes, mounts and pod security that break a chart only once it runs.

    tpl-library derives a container's ports from `service.*.spec.ports`, so an empty port
    list leaves routes and named-port probes pointing at nothing. It skips an emptyDir
    without `enabled: true`. An empty pod securityContext fails chart scanning.
    """
    findings: List[Finding] = []
    seen: Set[Tuple[str, str]] = set()

    def add(sev: str, cat: str, loc: str, msg: str) -> None:
        if (cat, loc) not in seen:
            seen.add((cat, loc))
            findings.append(Finding(sev, cat, loc, msg))

    for name, vals, raw in _variants(chart_dir):
        service = vals.get("service")
        routes = vals.get("routes") if isinstance(vals.get("routes"), dict) else {}
        enabled_routes = {k: r for k, r in routes.items()
                          if isinstance(r, dict) and r.get("enabled", True) is not False}
        port_names: Set[str] = set()
        if isinstance(service, dict):
            for svc in service.values():
                spec = svc.get("spec") if isinstance(svc, dict) else None
                for p in (spec or {}).get("ports") or []:
                    if isinstance(p, dict) and p.get("name"):
                        port_names.add(str(p["name"]))
        if isinstance(service, dict) and enabled_routes and not port_names:
            add("P1", "Service Without Ports", f"{name}:service",
                "Routes are enabled but no `service.*.spec.ports` entry is declared. tpl-library "
                "derives the container ports from these, so the route (and any probe on a named "
                "port) has nothing to reach. Declare the port, e.g. `name: http`.")
        for rkey, route in enabled_routes.items():
            for path in route.get("paths") or []:
                port = path.get("port") if isinstance(path, dict) else None
                if isinstance(port, str) and not port.isdigit() and port not in port_names:
                    add("P1", "Route Port Not Declared", f"{name}:routes.{rkey}",
                        f"Route path uses port '{port}', which no `service.*.spec.ports` entry names.")

        containers = vals.get("containers") if isinstance(vals.get("containers"), dict) else {}
        for cname, cont in containers.items():
            probes = cont.get("probes") if isinstance(cont, dict) else None
            if not isinstance(probes, dict) or probes.get("enabled", True) is False:
                continue
            for kind in ("readiness", "liveness", "startup"):
                probe = probes.get(kind)
                if not isinstance(probe, dict):
                    continue
                for handler in ("httpGet", "tcpSocket", "grpc"):
                    h = probe.get(handler)
                    port = h.get("port") if isinstance(h, dict) else None
                    if isinstance(port, str) and not port.isdigit() and port not in port_names:
                        add("P1", "Probe Port Not Declared", f"{name}:containers.{cname}.probes.{kind}",
                            f"The {kind} probe targets port '{port}', which no `service.*.spec.ports` "
                            "entry names, so the container never exposes it.")
                if raw.get("service", "absent") is None and isinstance(probe.get("httpGet"), dict):
                    add("P2", "HTTP Probe Without Service", f"{name}:containers.{cname}.probes.{kind}",
                        "This release sets `service: ~` (no HTTP port) but keeps an httpGet probe. "
                        "Use an exec probe (or disable probes) for a non-HTTP process.")

        if raw.get("service", "absent") is None and enabled_routes:
            add("P2", "Routes On Release Without Service", f"{name}:routes",
                "This release sets `service: ~` but its routes are still enabled. Set "
                "`routes.default.enabled: false` for a non-HTTP process.")

        # Reported where the value is written: values.yaml, or an overlay that sets it.
        own = raw if name != "values.yaml" else vals
        pod = own.get("pod") if isinstance(own.get("pod"), dict) else {}
        if "securityContext" in pod and not pod.get("securityContext"):
            add("P1", "Empty Pod securityContext", f"{name}:pod.securityContext",
                "pod.securityContext is empty, which chart scanning rejects (e.g. KSV-0118). Keep "
                "the library's non-root defaults (runAsUser/runAsGroup/fsGroup 10001, runAsNonRoot, "
                "seccompProfile RuntimeDefault).")

        mounts = own.get("mounts") if isinstance(own.get("mounts"), dict) else {}
        for mname, m in (mounts.get("emptyDir") or {}).items():
            if isinstance(m, dict) and m.get("enabled") is not True:
                add("P1", "emptyDir Not Enabled", f"{name}:mounts.emptyDir.{mname}",
                    "tpl-library mounts an emptyDir only when `enabled: true` is set; without it the "
                    "path silently stays unwritable.")
        persistence = vals.get("persistence") if isinstance(vals.get("persistence"), dict) else {}
        for mname, m in (mounts.get("pvc") or {}).items():
            claim = m.get("claimName") if isinstance(m, dict) else None
            if isinstance(claim, str) and claim and "{{" not in claim and mname in persistence:
                add("P2", "Hard-coded PVC Claim", f"{name}:mounts.pvc.{mname}.claimName",
                    f"claimName '{claim}' is a literal, but tpl.pvc names the claim it creates from "
                    "the release and component. Derive it with tpl.resource.siblingName (or leave "
                    "it empty) so every release mounts the claim that actually exists.")
    return findings


# The credential word must END the values path: `.Values.auth.password` is a credential,
# `.Values.auth.token.expiresIn` is configuration about one.
_CRED_REF = re.compile(
    r"\.Values\.[\w.]*\b(password|passwd|secret|secretKey|token|apiKey|api_key|clientSecret|"
    r"privateKey|encryptionKey|credentials?)\b(?![\w.])", re.I)
_CRED_KEY = re.compile(r"(PASSWORD|PASSWD|SECRET|TOKEN|API_?KEY|PRIVATE_KEY)\s*$", re.I)


def _container_groups(data: Dict[str, Any]):
    """(prefix, containers map) for workload, init, job and cronjob containers."""
    yield "containers", data.get("containers")
    yield "initContainers", data.get("initContainers")
    for block in ("jobs", "cronjobs"):
        items = data.get(block)
        if isinstance(items, dict):
            for jname, job in items.items():
                if isinstance(job, dict):
                    yield f"{block}.{jname}.containers", job.get("containers")
                    yield f"{block}.{jname}.initContainers", job.get("initContainers")


def check_secret_placement(chart_dir: Path) -> List[Finding]:
    """Credentials live in `secretEnvs` -- never in configmapEnvs, `env` values or `args`.

    Applies to every container, init container, job and cronjob, in values.yaml and every
    overlay. Matches credential-named keys and templated references to credential values
    (`{{ .Values.x.auth.password }}` inside a ConfigMap is still a password in a ConfigMap).
    """
    findings: List[Finding] = []
    files = [chart_dir / "values.yaml"] + _overlay_files(chart_dir)
    for vf in files:
        data = _load_yaml(vf)
        if not isinstance(data, dict):
            continue
        for prefix, cmap in _container_groups(data):
            if not isinstance(cmap, dict):
                continue
            for cname, cont in cmap.items():
                if not isinstance(cont, dict):
                    continue
                where = f"{vf.name}:{prefix}.{cname}"
                cm = cont.get("configmapEnvs")
                cm_text = cm if isinstance(cm, str) else (yaml.safe_dump(cm) if HAVE_YAML and isinstance(cm, dict) else "")
                for line in (cm_text or "").splitlines():
                    if not line.strip() or line.strip().startswith("#") or ":" not in line:
                        continue
                    key = line.split(":", 1)[0].strip()
                    if _CRED_KEY.search(key) or _CRED_REF.search(line):
                        findings.append(Finding(
                            "P0", "Secret Leak in ConfigMap", f"{where}.configmapEnvs",
                            f"'{key}' carries a credential in configmapEnvs. Move it to secretEnvs."))
                        break
                for env in cont.get("env") or []:
                    if isinstance(env, dict) and isinstance(env.get("value"), str) and _CRED_REF.search(env["value"]):
                        findings.append(Finding(
                            "P0", "Secret Leak in Container Env", f"{where}.env",
                            f"env '{env.get('name')}' renders a credential as a plain value. Move it to secretEnvs."))
                        break
                for arg in cont.get("args") or []:
                    if isinstance(arg, str) and _CRED_REF.search(arg):
                        findings.append(Finding(
                            "P0", "Secret Leak in Container Args", f"{where}.args",
                            "A credential is passed on the command line, visible in the pod spec. "
                            "Move it to secretEnvs and read it from the environment."))
                        break
    return findings


def _schema_node(schema: Dict[str, Any], path: List[str]) -> Optional[Dict[str, Any]]:
    """Walk a JSON schema along a values path, following local $refs."""
    def deref(node: Any) -> Any:
        seen = 0
        while isinstance(node, dict) and isinstance(node.get("$ref"), str) and node["$ref"].startswith("#/") and seen < 10:
            cur: Any = schema
            for part in node["$ref"][2:].split("/"):
                cur = cur.get(part) if isinstance(cur, dict) else None
            node, seen = cur, seen + 1
        return node

    node = deref(schema)
    for part in path:
        if not isinstance(node, dict):
            return None
        props = node.get("properties") or {}
        if part in props:
            node = deref(props[part])
            continue
        pattern = next((v for k, v in (node.get("patternProperties") or {}).items() if re.match(k, part)), None)
        if pattern is not None:
            node = deref(pattern)
            continue
        extra = node.get("additionalProperties")
        node = deref(extra) if isinstance(extra, dict) else None
    return node if isinstance(node, dict) else None


def check_schema_contract(chart_dir: Path) -> List[Finding]:
    """values.schema.json must accept the chart's own values.

    `helm lint --strict -f values.yaml` runs on the defaults alone, so a `minLength` on a
    key whose default is "" (say, an image repository the library derives) fails every
    pipeline. Mode/subComponent enums list the real modes -- no empty value -- and every
    value values.yaml or an overlay sets.
    """
    findings: List[Finding] = []
    schema_file = chart_dir / "values.schema.json"
    if not schema_file.exists():
        return findings
    try:
        schema = json.loads(schema_file.read_text(encoding="utf-8"))
    except ValueError:
        return findings
    variants = _variants(chart_dir)
    if not variants:
        return findings
    base = variants[0][1]

    def leaves(node: Any, prefix: List[str]):
        if isinstance(node, dict):
            for k, v in node.items():
                yield from leaves(v, prefix + [str(k)])
        else:
            yield prefix, node

    for path, val in leaves(base, []):
        if val != "":
            continue
        node = _schema_node(schema, path)
        if node and isinstance(node.get("minLength"), int) and node["minLength"] > 0:
            findings.append(Finding(
                "P1", "Schema Rejects Default Value", f"values.schema.json:{'.'.join(path)}",
                f"'{'.'.join(path)}' defaults to \"\" in values.yaml but the schema sets minLength "
                f"{node['minLength']}, so `helm lint --strict` fails on the chart's own defaults. "
                "Drop the minLength (the library fills an empty value) or give it a real default."))

    for key in ("mode", "subComponent"):
        node = _schema_node(schema, [key])
        enum = node.get("enum") if node else None
        if not isinstance(enum, list):
            continue
        if "" in enum:
            findings.append(Finding(
                "P2", "Empty Enum Value", f"values.schema.json:{key}",
                f"The `{key}` enum allows \"\". List only the real modes and default values.yaml to "
                "the primary one."))
        for fname, vals, raw in variants:
            val = raw.get(key) if fname != "values.yaml" else vals.get(key)
            if val is not None and val not in enum:
                findings.append(Finding(
                    "P1", "Value Not In Schema Enum", f"{fname}:{key}",
                    f"{fname} sets {key} '{val}', which values.schema.json does not list "
                    f"({enum}). Helm (and any GitOps sync) rejects the release."))
    return findings


def check_modes(chart_dir: Path) -> List[Finding]:
    """One chart, several modes: every mode runs as its own subComponent.

    subComponent is optional for a single-mode chart. Once overlays run different modes
    (API + worker, queue + worker), releases that share a subComponent render identical
    resource names, so each mode needs its own.
    """
    findings: List[Finding] = []
    variants = _variants(chart_dir)
    if len(variants) < 2:
        return findings
    rows = [(f, str(v.get("mode") or ""), str(v.get("component") or ""), str(v.get("subComponent") or ""))
            for f, v, _ in variants]
    modes = {m for _, m, _, _ in rows if m}
    if len(modes) < 2:
        return findings
    by_name: Dict[Tuple[str, str], Set[str]] = {}
    for fname, mode, comp, sub in rows:
        if not mode:
            continue
        if not sub:
            findings.append(Finding(
                "P1", "Missing subComponent For Mode", f"{fname}:subComponent",
                f"This chart runs several modes ({', '.join(sorted(modes))}); {fname} runs "
                f"'{mode}' without a subComponent. Suggest one per mode (e.g. the mode name), "
                "confirm it with the user."))
        else:
            by_name.setdefault((comp, sub), set()).add(mode)
    for (comp, sub), name_modes in by_name.items():
        if len(name_modes) > 1:
            findings.append(Finding(
                "P1", "Duplicate subComponent Across Modes", "values*.yaml:subComponent",
                f"Modes {', '.join(sorted(name_modes))} all run as '{comp}-{sub}', so their releases "
                "render the same resource names. Give each mode its own subComponent."))
    return findings


_OPTIONAL_BLOCKS = (
    ("jobs", ("tpl.job",)),
    ("cronjobs", ("tpl.cronjob",)),
    ("persistence", ("tpl.pvc",)),
    ("metrics", ("tpl.servicemonitor", "tpl.podmonitor")),
    ("global.metrics", ("tpl.servicemonitor", "tpl.podmonitor")),
)


def check_optional_blocks(chart_dir: Path) -> List[Finding]:
    """jobs, cronjobs, persistence and metrics exist only where the service uses them.

    Each is paired with its tpl entrypoint. A block with no entrypoint renders nothing and
    documents a feature the chart does not have; drop it from values.yaml and the schema.
    (global.tracing is judged by whether the application reads it, which no file shows.)
    """
    findings: List[Finding] = []
    values = _load_yaml(chart_dir / "values.yaml")
    if not isinstance(values, dict):
        return findings
    includes = _manifest_includes(chart_dir)
    schema: Dict[str, Any] = {}
    try:
        schema = json.loads((chart_dir / "values.schema.json").read_text(encoding="utf-8"))
    except Exception:
        schema = {}
    for block, helpers in _OPTIONAL_BLOCKS:
        if any(h in includes for h in helpers):
            continue
        parts = block.split(".")
        cur: Any = values
        for p in parts:
            cur = cur.get(p, "absent") if isinstance(cur, dict) else "absent"
        in_values = cur != "absent"
        in_schema = _schema_node(schema, parts) is not None if schema else False
        if in_values or in_schema:
            where = ", ".join(w for w, on in (("values.yaml", in_values), ("values.schema.json", in_schema)) if on)
            findings.append(Finding(
                "P2", "Unused Optional Block", f"{chart_dir.name}:{block}",
                f"`{block}` is in {where}, but no template includes {' or '.join(helpers)}. Optional "
                "features are added only when the service uses them; remove the block and its "
                "schema property."))
    return findings


def check_values_leaf_docs(chart_dir: Path) -> List[Finding]:
    """Under jobs/cronjobs/persistence every leaf carries its own `# --` line.

    helm-docs gives each documented leaf a README row. A `# --` on a parent map instead
    collapses the whole object into one JSON-blob row -- unless that parent is marked
    `# @default -- Check values.yaml` (rendered as one opaque value on purpose).
    """
    vf = chart_dir / "values.yaml"
    if not vf.exists():
        return []
    lines = vf.read_text(encoding="utf-8").splitlines()
    key_re = re.compile(r"^(\s*)([A-Za-z0-9_.\-]+):(.*)$")
    entries = []  # (line_idx, indent, key, has_inline_value, comment_block)
    comment: List[str] = []
    scalar_indent: Optional[int] = None   # inside a `key: |` block scalar
    list_indent: Optional[int] = None     # inside the items of a block list
    for idx, raw in enumerate(lines):
        s = raw.strip()
        indent_now = len(raw) - len(raw.lstrip())
        if scalar_indent is not None:
            if not s or indent_now > scalar_indent:
                continue
            scalar_indent = None
        if s.startswith("- "):
            list_indent = indent_now if list_indent is None else min(list_indent, indent_now)
            comment = []
            continue
        if list_indent is not None and s and not s.startswith("#"):
            if indent_now > list_indent:
                continue
            list_indent = None
        if s.startswith("#"):
            comment.append(s)
            continue
        if not s:
            comment = []
            continue
        m = key_re.match(raw)
        if m and not s.startswith("- "):
            entries.append((idx, len(m.group(1)), m.group(2), bool(m.group(3).strip()), comment))
            if re.match(r"^\s*[|>][-+]?\d*\s*(#.*)?$", m.group(3)):
                scalar_indent = len(m.group(1))
        comment = []

    undocumented: List[str] = []
    parent_documented: List[str] = []
    stack: List[Tuple[int, str, bool]] = []   # (indent, dotted, opaque)
    for pos, (idx, indent, key, inline, block) in enumerate(entries):
        while stack and stack[-1][0] >= indent:
            stack.pop()
        dotted = f"{stack[-1][1]}.{key}" if stack else key
        opaque_parent = any(o for _, _, o in stack)
        top = dotted.split(".", 1)[0]
        has_doc = any(c.startswith("# --") for c in block)
        opaque = any("@default -- Check values.yaml" in c for c in block)
        nxt = entries[pos + 1] if pos + 1 < len(entries) else None
        is_parent = not inline and nxt is not None and nxt[1] > indent
        if is_parent and not inline:
            # a block list (`key:` then `- item`) is a leaf value, not a map
            first_child = next((lines[j].strip() for j in range(idx + 1, len(lines)) if lines[j].strip() and not lines[j].strip().startswith("#")), "")
            if first_child.startswith("- "):
                is_parent = False
        if top in ("jobs", "cronjobs", "persistence") and "." in dotted and not opaque_parent:
            if is_parent and has_doc and not opaque:
                parent_documented.append(dotted)
            elif not is_parent and not has_doc:
                undocumented.append(dotted)
        stack.append((indent, dotted, opaque))

    findings: List[Finding] = []
    if undocumented:
        findings.append(Finding(
            "P2", "Undocumented Values Leaf", f"{vf.name}",
            f"{len(undocumented)} leaf key(s) under jobs/cronjobs/persistence have no `# --` line, "
            f"so helm-docs gives them no README row: {', '.join(undocumented[:8])}"
            f"{' (+more)' if len(undocumented) > 8 else ''}. Add `# --` and `# @section --` above each."))
    if parent_documented:
        findings.append(Finding(
            "P2", "Parent Map Documented", f"{vf.name}",
            f"`# --` sits on parent map(s) {', '.join(parent_documented[:6])}, which helm-docs renders "
            "as one blob row. Document the leaves instead, or mark the parent "
            "`# @default -- Check values.yaml` when it really is one opaque value."))
    return findings


_COMMENT_DIRECTIVE = re.compile(
    r"^#\s*(renovate:|syntax=|escape=|check=|hadolint|shellcheck|yamllint|noqa|nosec|!|@section|@default)", re.I)


def check_comment_style(root: Path, chart_dir: Optional[Path]) -> List[Finding]:
    """Comments in CI, Dockerfiles, templates and overlays: one line, saying why.

    A comment explains a deviation from the library default in a single line. Banners and
    multi-line prose describe what the next line already says and drift from it. Tool
    directives (`# renovate:`, `# syntax=`, `# hadolint ...`) and a licence header at the
    top of a file are not prose. values.yaml is exempt: its `# --` lines feed helm-docs.
    """
    targets: List[Path] = [p for p in sorted(root.glob("Dockerfile*")) if p.is_file()]
    targets += [p for p in (root / ".gitlab-ci.yml",) if p.exists()]
    if chart_dir is not None and chart_dir.exists():
        tdir = chart_dir / "templates"
        if tdir.is_dir():
            targets += sorted(list(tdir.glob("*.yaml")) + list(tdir.glob("*.tpl")))
        targets += _overlay_files(chart_dir)
    findings: List[Finding] = []
    for path in targets:
        lines = path.read_text(encoding="utf-8", errors="ignore").splitlines()
        runs: List[Tuple[int, int]] = []
        start, count, seen_code = None, 0, False
        for idx, raw in enumerate(lines + [""], 1):
            s = raw.strip()
            is_comment = s.startswith("#") and not _COMMENT_DIRECTIVE.match(s)
            if is_comment:
                if start is None:
                    start, count = idx, 0
                count += 1
                continue
            if start is not None and count >= 2:
                header = not seen_code and any(w in " ".join(lines[start - 1:start - 1 + count]).lower()
                                               for w in ("license", "licence", "copyright"))
                if not header:
                    runs.append((start, count))
            start, count = None, 0
            if s:
                seen_code = True
        if runs:
            shown = ", ".join(f"{path.name}:{a} ({n} lines)" for a, n in runs[:3])
            findings.append(Finding(
                "P2", "Multi-line Comment", f"{path.relative_to(root) if path.is_relative_to(root) else path.name}",
                f"Comment blocks: {shown}{' (+more)' if len(runs) > 3 else ''}. Keep at most one line "
                "that says why something differs from the library default; drop banners and prose."))
    return findings


def check_image_path_alignment(repo: Path, chart_dir: Path, ci_vars: Dict[str, Any]) -> List[Finding]:
    """The image the chart renders must be the image CI pushes (GitLab).

    gitlab-ci-library's Common:Init pushes to CI_PROJECT_PATH unless IMAGE_REPOSITORY is set.
    A chart that leaves `repository` empty gets `<partOf>/<component>[/<subComponent>]` from
    the library instead, and silently deploys an image that was never pushed. The library's
    placeholder registry and pull secret (`cr.io`, `cr-cred`) are never right for a real
    project -- ask the user for the registry, pull secret and repository path.
    """
    findings: List[Finding] = []
    values = _load_yaml(chart_dir / "values.yaml")
    if not isinstance(values, dict):
        return findings
    glob_ = values.get("global") if isinstance(values.get("global"), dict) else {}
    image_g = glob_.get("image") if isinstance(glob_.get("image"), dict) else {}
    registry = image_g.get("registry")
    if registry == "cr.io":
        findings.append(Finding(
            "P1", "Library Placeholder Registry", "values.yaml:global.image.registry",
            "global.image.registry is still the library placeholder 'cr.io'. Set the registry CI "
            "pushes to (ask the user)."))
    if image_g.get("pullSecrets") == ["cr-cred"]:
        findings.append(Finding(
            "P2", "Library Placeholder Pull Secret", "values.yaml:global.image.pullSecrets",
            "global.image.pullSecrets is still the library placeholder 'cr-cred'. Set the pull "
            "secret that exists in the target namespaces (ask the user)."))

    _host, project_path = origin_project(repo)
    ci_repo = str(ci_vars.get("IMAGE_REPOSITORY") or "") or project_path
    ci_repo = ci_repo.replace("${CI_PROJECT_PATH}", project_path).replace("$CI_PROJECT_PATH", project_path)
    if not ci_repo or "$" in ci_repo:
        return findings
    main = ((values.get("containers") or {}).get("main") or {}) if isinstance(values.get("containers"), dict) else {}
    explicit = str(((main.get("image") or {}).get("repository")) or "") if isinstance(main, dict) else ""
    if "{{" in explicit:
        return findings
    if explicit:
        chart_repo = explicit
    else:
        part_of = values.get("partOf") or glob_.get("partOf") or ""
        comp, sub = values.get("component") or "", values.get("subComponent") or ""
        path = f"{comp}/{sub}" if comp and sub else (comp or sub)
        chart_repo = f"{part_of}/{path}" if part_of and path else path
    if chart_repo and chart_repo.strip("/") != ci_repo.strip("/"):
        findings.append(Finding(
            "P1", "Chart Image Differs From CI Push Path", "values.yaml:containers.main.image.repository",
            f"The chart renders repository '{chart_repo}'{' (derived by the library)' if not explicit else ''}, "
            f"but CI pushes to '{ci_repo}'. Set containers.main.image.repository to the CI push path "
            "(confirm it with the user)."))
    ci_registry = str(ci_vars.get("IMAGE_REGISTRY") or "")
    if ci_registry and "$" not in ci_registry and registry and registry != ci_registry:
        findings.append(Finding(
            "P1", "Chart Image Differs From CI Push Path", "values.yaml:global.image.registry",
            f"The chart pulls from registry '{registry}' but CI pushes to '{ci_registry}'."))
    return findings
