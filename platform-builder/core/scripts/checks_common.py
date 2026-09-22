"""Platform-agnostic compliance checks.

Everything here inspects artefacts that are IDENTICAL regardless of whether CI runs on
GitLab or GitHub: the Dockerfile, the Helm chart, .dockerignore/.gitignore, and project
hygiene. Roughly 62% of the original GitLab rule engine turned out to live here.

These functions were lifted verbatim from the GitLab engine after it had been validated
against known-good baselines, so behaviour is unchanged by the extraction. Platform
mechanics (CI config shape, job wiring, token model) live in platforms/<name>/ci_checks.py.

Severity contract: every Finding produced here is DETERMINISTIC and reproducible, and is
tagged [engine]. Judgement-based findings are the agent's job -- see
core/references/security-core.md -- and are tagged [judged]. Never blur the two.
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


INTERNAL_BASE_VARS = {
    "PYTHON_312_MICRO_BASE_IMAGE",
    "JAVA_25_MICRO_BASE_IMAGE",
    "MICRO_ROOT_BASE_IMAGE",
    "NODE_JS_24_MICRO_BASE_IMAGE",
    "NGINX_MICRO_BASE_IMAGE",
    "TOOLKIT_BUILD_IMAGE",
    "CI_REGISTRY",
    "CI_REGISTRY_IMAGE",
    "IMAGE_REPOSITORY",
    "CONTAINER_DEV_REGISTRY",
}


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
        # own security review (core/references/security-core.md) emits [judged] findings
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

    # 1. Scratch or local multi-stage reference (local only, does not pull from internet)
    if img.lower() == "scratch" or img in known_stages:
        return False, ""

    # 2. Already proxied via CI_DEPENDENCY_PROXY
    if "CI_DEPENDENCY_PROXY" in img:
        return False, ""

    # 3. Enterprise internal base image variables (resolved to ${CI_REGISTRY})
    if img.startswith("$") or img.startswith("${"):
        is_trusted_internal_var = any(var in img for var in INTERNAL_BASE_VARS) or ("_MICRO_BASE_IMAGE" in img) or ("_BASE_IMAGE" in img)
        if is_trusted_internal_var:
            return False, ""

    # 4. Check if image explicitly points to trusted internal enterprise registry
    for trusted in TRUSTED_INTERNAL_REGISTRIES:
        if img.startswith(trusted + "/") or f"//{trusted}/" in img or img == trusted:
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
_PIN_ARG = re.compile(r"^ARG\s+([A-Za-z0-9_]*_(?:VERSION|TAG))\s*=\s*(\S+)", re.I)
_SHIM_ARGV0 = {"tini", "dumb-init", "catatonit", "gosu", "su-exec",
               "s6-svscan", "supervisord", "entrypoint", "docker-entrypoint"}


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
    is the consumer's to add. Two shapes go wrong on their own (helm-chart-standard
    §3.0d): wrapping the domain groups in an `app:` envelope, which lengthens every
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
                "these names, so nothing collides (helm-chart-standard §3.0d).",
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
                "otherwise, so the replica still diffs cleanly (helm-chart-standard §3.0d).",
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
    # hit. It is a defect only when it can reach the NETWORK: an install that resolves
    # online at image-build time re-resolves what the pipeline already pinned and
    # scanned, so the image cannot be reproduced from what CI tested.
    #
    # Installing OFFLINE from the CI cache, bind-mounted by BuildKit, is the library's
    # own sanctioned pattern (Pattern A -- see the Python example in the CI library's
    # README). The cache is the handoff. A dependency directory is NEVER an artifact:
    # every `needs:` in the language modules uses `artifacts: false`, and the cache
    # blocks carry `cache: policy: pull`. The cache is warmed by the dependency-download
    # job; there is no separate install job to point at.
    dependency_installs = [
        (r"\b(npm\s+(ci|install|i)\b|yarn\s+install|pnpm\s+(install|i)\b)", ".npm", "Dependency:Download"),
        (r"\b(pip\s+install|uv\s+sync|uv\s+pip\s+install|poetry\s+install)\b", ".uv", "Dependency:Download"),
        (r"\b(go\s+mod\s+download|mvn\s+dependency:go-offline)\b", ".m2", "the dependency-download job"),
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
            if "=" in arg_def:
                var_name, var_val = arg_def.split("=", 1)
                # `ARG YQ_VERSION=4.53.6` is a version pin, not an image. Without this a
                # multi-stage toolkit image reports one "unproxied image" per tool it pins.
                if re.search(r"_(VERSION|TAG)$", var_name.strip(), re.I):
                    continue
                unproxied, suggested = classify_unproxied_public_image(var_val.strip(), known_stages)
                if unproxied:
                    findings.append(Finding(
                        "P1", "Unproxied External Image", f"{df_file.name}:{line_no}",
                        f"ARG '{var_name.strip()}' default image '{var_val.strip()}' pulls from an external registry without cache. " + _image_remedy(suggested)
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
        for pat, cache_dir, install_tpl in dependency_installs:
            if not re.search(pat, instruction):
                continue
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
            findings.append(Finding(
                "P1", "Dockerfile Packaging Violation", f"{df_file.name}:{start_line}",
                f"Dockerfile resolves dependencies at image-build time with no {cache_dir} cache mount, so "
                "it reaches the network and re-resolves what the pipeline already pinned and scanned. "
                f"Warm the cache in a CI job (extends {install_tpl}), restore it onto Image:Build with "
                f"`cache: policy: pull`, and install offline from a BuildKit bind mount: "
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


def _check_version_pins(df_file: Path, lines: List[str]) -> List[Finding]:
    """A literal version pin with no '# renovate:' above it is invisible to the bot.

    Only ARGs with a literal VALUE are pins. A bare `ARG YQ_VERSION` re-declares a global
    into a stage -- Docker requires it, and treating it as a pin misreads ~50 lines of a
    typical multi-stage toolkit image.
    """
    findings: List[Finding] = []
    for line_no, ins in _logical_instructions(lines):
        m = _PIN_ARG.match(ins)
        if not m or "$" in m.group(2):
            continue
        idx = line_no - 2
        while idx >= 0 and not lines[idx].strip():
            idx -= 1
        annotated = False
        while idx >= 0 and lines[idx].strip().startswith("#"):
            if lines[idx].strip().lower().startswith("# renovate:"):
                annotated = True
                break
            idx -= 1
        if not annotated:
            findings.append(Finding(
                "P2", "Unannotated Version Pin", f"{df_file.name}:{line_no}",
                f"ARG '{m.group(1)}' pins {m.group(2)} with no '# renovate:' annotation on the "
                f"line above. An unannotated pin is invisible to the bot and rots silently."))
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
                f"tag with a '# renovate:' annotation."))
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

    if has_expose and "ENTRYPOINT" in ops and "CMD" not in ops:
        ep = next(i for _, i in runtime[3] if i.split()[0].upper() == "ENTRYPOINT")
        argv0 = _argv0(ep)
        base = argv0.rsplit("/", 1)[-1]
        if argv0 and not (base.endswith((".sh", ".bash", ".py")) or base in _SHIM_ARGV0):
            findings.append(Finding(
                "P2", "Entrypoint Misuse", f"{df_file.name}",
                f"ENTRYPOINT invokes '{argv0}' directly. ENTRYPOINT is for a pre-start shim; "
                f"launching the application itself belongs in CMD, which an operator can "
                f"override without --entrypoint."))
    return findings


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
            f"packaged chart. See helm-chart-standard.md for the full baseline."))

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
    if name_match:
        c_name = name_match.group(1).strip()
        if c_name.endswith("-service") or c_name == "service":
            findings.append(Finding(
                "P1", "Chart Name Suffix Violation", str(chart_yaml_file),
                f"Chart name '{c_name}' uses the forbidden generic suffix '-service'. "
                f"Sub-component must reflect actual functional role: '{{component}}-backend', '{{component}}-frontend', '{{component}}-worker', or '{{component}}-gateway'."
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

        # Check subComponent standard
        sub_match = re.search(r"^subComponent:\s*[\"']?([^\"'\n]+)[\"']?", val_content, re.MULTILINE)
        if sub_match:
            sub_val = sub_match.group(1).strip()
            if sub_val == "service" or "service" in sub_val:
                findings.append(Finding(
                    "P1", "Sub-Component Role Violation", f"{values_yaml_file.name}:subComponent",
                    f"subComponent cannot be generic 'service' (found '{sub_val}'). "
                    "Must strictly be one of: 'backend', 'frontend', 'worker', or 'gateway'. "
                    "Industry naming guidance: use 'backend' for APIs/REST services (e.g. pii-service -> pii-backend), "
                    "'worker' for background consumers/scrubbers/batch jobs (e.g. pii-masker/pii-scrubber -> pii-worker), "
                    "'gateway' for edge proxies/BFFs (e.g. auth-gateway -> auth-gateway), and 'frontend' for SPAs/UIs (e.g. admin-portal -> admin-frontend)."
                ))
            elif sub_val not in ["backend", "frontend", "worker", "gateway"]:
                findings.append(Finding(
                    "P1", "Sub-Component Standard", f"{values_yaml_file.name}:subComponent",
                    f"subComponent '{sub_val}' is invalid. Must strictly be one of: backend, frontend, worker, gateway. "
                    "Guidance: use 'backend' for APIs/REST services, 'worker' for async consumers/scrubbers, "
                    "'gateway' for edge proxies/BFFs, and 'frontend' for SPAs/UIs."
                ))
        else:
            findings.append(Finding(
                "P1", "Missing subComponent", str(values_yaml_file),
                "Missing 'subComponent' in values.yaml. Must strictly be 'backend', 'frontend', 'worker', or 'gateway'."
            ))

        # Check global.partOf (Product Name) and releaseNameLength
        part_of_match = re.search(r"partOf:\s*[\"']?([^\"'\n]+)[\"']?", val_content)
        rel_len_match = re.search(r"releaseNameLength:\s*(\d+)", val_content)

        if part_of_match:
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

        # Check image repository formatting
        if "myorg/" in val_content or "[PRODUCT_NAME]" in val_content:
            findings.append(Finding(
                "P1", "Generic Image Repository", str(values_yaml_file),
                "Image repository contains generic placeholder ('myorg/' or '[PRODUCT_NAME]'). "
                "Must be dynamically templated as '{{ .Values.global.partOf }}/{{ .Values.component }}-{{ .Values.subComponent }}'."
            ))

        # Check cross-service sibling URL override pattern
        if 'include "tpl.resource.siblingName"' in val_content:
            sibling_invocations = re.findall(r'(\w+):\s*[\'"][^\'"]*include "tpl.resource.siblingName"[^\'"]*[\'"]', val_content)
            for sib_line in sibling_invocations:
                if ".internalUrl" not in sib_line and "default" not in sib_line:
                    findings.append(Finding(
                        "P1", "Cross-Service Override Law Violation", str(values_yaml_file),
                        "Cross-service sibling URLs must follow the Override-First Law: "
                        "'{{ tpl .Values.<service>.internalUrl $ | default (printf \"http://%s:8080\" (include \"tpl.resource.siblingName\" ...)) }}'."
                    ))
                    break

        # Cleartext credentials in configmapEnvs.
        #
        # Deliberately narrow: this matches KEY NAMES only, which catches the obvious
        # `PASSWORD: hunter2` and nothing else. It cannot see a credential embedded in a
        # value -- `DATABASE_URL: postgres://svc:pw@host` has an innocuous key -- nor can
        # it tell a real secret from `API_KEY: ""`. Classifying by value is judgement work
        # and belongs to the agent's pass over core/references/security-core.md section 1.
        # Do not grow this list; a longer regex only adds noise.
        cm_match = re.search(r"configmapEnvs:\s*\|(.*?)(?=\n\s*[a-zA-Z0-9_-]+:|$)", val_content, re.DOTALL)
        if cm_match:
            cm_block = cm_match.group(1)
            for token_pattern in [r"PASSWORD\s*:", r"SECRET\s*:", r"TOKEN\s*:", r"API_KEY\s*:"]:
                if re.search(token_pattern, cm_block, re.IGNORECASE):
                    findings.append(Finding(
                        "P0", "Secret Leak in ConfigMap", f"{values_yaml_file.name}:configmapEnvs",
                        f"Sensitive credentials found in configmapEnvs ({token_pattern.strip(r':\s*')}). Must be placed in secretEnvs!"
                    ))

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

    # Check README.md exists and is updated via helm-docs
    readme_file = chart_dir / "README.md"
    if not readme_file.exists():
        findings.append(Finding(
            "P1", "Missing chart/README.md", str(readme_file),
            "chart/README.md is missing. Run 'helm-docs -c chart --template-files \"README.gotmpl\" --sort-values-order file --document-dependency-values' to generate documentation."
        ))
    elif values_yaml_file.exists():
        v_mtime = values_yaml_file.stat().st_mtime
        r_mtime = readme_file.stat().st_mtime
        if v_mtime > r_mtime + 2.0:
            findings.append(Finding(
                "P2", "Outdated chart/README.md (helm-docs drift)", str(readme_file),
                "chart/values.yaml has been modified more recently than chart/README.md. "
                "Whenever values.yaml changes, chart/README.md must be updated via helm-docs: "
                "'helm-docs -c chart --template-files \"README.gotmpl\" --sort-values-order file --document-dependency-values'."
            ))

    return findings


def check_project_hygiene(root: Path) -> List[Finding]:
    findings: List[Finding] = []
    # Python projects must exclusively use pyproject.toml with uv.
    # Legacy requirements*.txt files are strictly forbidden (P0 Critical violation).
    raw_req_files = list(root.glob("requirements*.txt")) + list(root.glob("requirements/*.txt")) + list(root.glob("*requirements*.txt"))
    # Filter out ignore directories like .venv, venv, .agents, .git, etc.
    req_files = sorted(list(set(
        f for f in raw_req_files
        if not any(part.startswith(".") or part in ["venv", ".venv", "node_modules"] for part in f.parts)
    )))
    is_python = (root / "pyproject.toml").exists() or bool(req_files)
    if is_python and req_files:
        for rf in req_files:
            try:
                rel = str(rf.relative_to(root))
            except Exception:
                rel = str(rf)
            findings.append(Finding(
                "P0", "Legacy Python Requirements File", rel,
                f"Python projects must exclusively use 'pyproject.toml' with 'uv'. Legacy requirements file '{rf.name}' is strictly forbidden and must be converted to pyproject.toml and removed."
            ))

    # Check pyproject.toml indentation hygiene
    pyproject_file = root / "pyproject.toml"
    if pyproject_file.exists():
        try:
            content = pyproject_file.read_text(encoding="utf-8")
            for section in ["dependencies", "dev"]:
                m = re.search(rf'{section}\s*=\s*\[(.*?)\]', content, re.DOTALL)
                if m:
                    for line in m.group(1).splitlines():
                        stripped = line.strip()
                        if stripped and not stripped.startswith("#"):
                            if not line.startswith("    "):
                                findings.append(Finding(
                                    "P2", "pyproject.toml Indentation", "pyproject.toml",
                                    f"Array entry '{stripped}' in '{section}' is not properly indented with 4 spaces. Ensure 4-space indentation."
                                ))
                                break
        except Exception:
            pass

    return findings
