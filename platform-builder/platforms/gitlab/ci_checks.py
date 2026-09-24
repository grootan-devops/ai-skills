"""GitLab CI adapter.

Implements the adapter contract in core/scripts/audit.py for `.gitlab-ci.yml`:
include/WORKFLOW consistency, job DAG wiring (`needs:` + explicit `optional:`), and
script formatting. Platform-agnostic artefacts (Dockerfile, Helm chart, ignore files)
are checked once by core/scripts/checks_common.py and are deliberately absent here.

Checks were lifted verbatim from the validated standalone GitLab engine; the extraction
is behaviour-preserving by construction.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

try:
    import yaml
    HAVE_YAML = True
except ImportError:
    HAVE_YAML = False

from checks_common import (Finding, iter_jobs, CI_RESERVED_KEYS,
                           MULTILINE_SHELL_MARKERS, classify_unproxied_public_image)

NAME = "gitlab"
CI_FILES = [".gitlab-ci.yml"]
_MAP = Path(__file__).resolve().parent / "workflow-map.json"


def _map() -> Dict[str, Any]:
    with open(_MAP, encoding="utf-8") as fh:
        return json.load(fh)


def module_workflows() -> Dict[str, List[str]]:
    return {n: list(s.get("provides", [])) for n, s in _map()["modules"].items() if s.get("provides")}


def workflow_provider() -> Dict[str, str]:
    shared = set(_map().get("shared_options", {}))
    out: Dict[str, str] = {}
    for name, spec in _map()["modules"].items():
        for opt in spec.get("provides", []):
            if opt not in shared:
                out.setdefault(opt, name)
    return out


def option_signature_job() -> Dict[str, str]:
    sig: Dict[str, str] = {}
    for spec in _map()["modules"].values():
        sig.update(spec.get("signature_jobs", {}))
    return sig


def _declares_build_script(repo: Path) -> bool:
    """True when the project actually has something to compile.

    A build job is only meaningful where a build step exists. Node projects say so in
    package.json scripts; Java and Go always compile. An interpreted service with no
    build script has no output to artifact, and demanding Project:Build there produces a
    finding no one can satisfy except by adding an empty job.
    """
    if (repo / "pom.xml").exists() or (repo / "build.gradle").exists() \
            or (repo / "build.gradle.kts").exists() or (repo / "go.mod").exists():
        return True
    pkg = repo / "package.json"
    if pkg.exists():
        try:
            scripts = json.loads(pkg.read_text(encoding="utf-8")).get("scripts", {})
            return any(k == "build" or k.startswith("build:") for k in scripts)
        except Exception:
            return True   # unreadable manifest: keep the stricter behaviour
    return False


def check_gitlab_ci(ci_file: Path) -> Tuple[List[Finding], Dict[str, Any]]:
    findings: List[Finding] = []
    if not ci_file.exists():
        findings.append(Finding("P0", "Missing CI File", str(ci_file), ".gitlab-ci.yml does not exist."))
        return findings, {}

    content = ci_file.read_text(encoding="utf-8")
    data: Dict[str, Any] = {}

    if HAVE_YAML:
        try:
            data = yaml.safe_load(content) or {}
        except Exception as e:
            findings.append(Finding("P0", "YAML Syntax Error", str(ci_file), f"Failed to parse YAML: {e}"))
            return findings, {}

    # 1. Check Includes, Template Modules, and GitOps Deploy Parameters
    included_files: List[str] = []
    include_blocks = []
    if isinstance(data.get("include"), list):
        include_blocks = data["include"]
    elif "include" in data and isinstance(data["include"], dict):
        include_blocks = [data["include"]]

    for inc in include_blocks:
        if isinstance(inc, str):
            # Bare string shorthand is a local include.
            included_files.append(inc)
            continue
        if isinstance(inc, dict):
            files = inc.get("file", [])
            f_list = files if isinstance(files, list) else [files]
            # `local:` (and `template:`/`remote:`) name a module just as `file:` does.
            # The template library's own pipeline includes itself with `local:`, so
            # ignoring these forms reports every declared workflow as unbacked.
            for key in ("local", "template", "remote"):
                val = inc.get(key)
                if isinstance(val, str):
                    f_list = f_list + [val]
                elif isinstance(val, list):
                    f_list = f_list + val
            included_files.extend(f_list)

            # Audit GitOps deploy inputs
            inputs = inc.get("inputs", {})
            if isinstance(inputs, dict):
                for f in f_list:
                    if "deploy/gitops/.argocd" in str(f):
                        has_chart = bool(inputs.get("gitops_chart_values_file"))
                        has_manifest = bool(inputs.get("gitops_manifest_file"))
                        if has_chart and has_manifest:
                            findings.append(Finding(
                                "P0", "GitOps Mutual Exclusivity Violation", f"{ci_file.name}:include",
                                "Cannot configure both 'gitops_chart_values_file' and 'gitops_manifest_file'. Choose either Helm or Manifest deployment."
                            ))
                        if has_chart and not inputs.get("gitops_chart_app_yq_path"):
                            findings.append(Finding(
                                "P1", "GitOps Missing Parameter", f"{ci_file.name}:include",
                                "'gitops_chart_app_yq_path' is mandatory when 'gitops_chart_values_file' is specified."
                            ))
                        if has_manifest and not inputs.get("gitops_new_image"):
                            findings.append(Finding(
                                "P1", "GitOps Missing Parameter", f"{ci_file.name}:include",
                                "'gitops_new_image' is mandatory when 'gitops_manifest_file' is specified."
                            ))
                    elif "deploy/gitops/.komodo" in str(f):
                        for req in ["environment", "gitops_repo_url", "gitops_branch", "komodo_stack_name", "gitops_service_image_yq_path"]:
                            if not inputs.get(req):
                                findings.append(Finding(
                                    "P1", "GitOps Missing Parameter", f"{ci_file.name}:include",
                                    f"Mandatory Komodo GitOps input '{req}' is missing or empty."
                                ))

    # 2. Check WORKFLOW Variable Sync
    variables = data.get("variables", {})
    workflow_var = variables.get("WORKFLOW")
    workflow_options = []
    if isinstance(workflow_var, dict):
        workflow_options = workflow_var.get("options", [])
    elif isinstance(workflow_var, str):
        workflow_options = [workflow_var]

    # Bidirectional WORKFLOW <-> module consistency (references/workflow-matrix.md section 1).
    # Option without module => clicking it yields an empty pipeline.
    # Module without option => dead weight the user can never dispatch.
    if workflow_options:
        for opt in workflow_options:
            provider = workflow_provider().get(opt)
            if provider and not any(provider in f for f in included_files):
                findings.append(Finding(
                    "P1", "Workflow Mismatch", f"{ci_file.name}:variables.WORKFLOW",
                    f"WORKFLOW option '{opt}' is declared, but its providing module "
                    f"'{provider}' is not in include:. Dispatching it creates an empty pipeline."
                ))

        # Retiring an option by disabling its job with `rules: [{when: never}]` is the
        # sanctioned way to drop inapplicable work (workflow-matrix.md section 4), so a
        # deliberately disabled job is not an unreachable module.
        disabled_jobs = {
            name for name, body in iter_jobs(data)
            if isinstance(body.get("rules"), list)
            and all(isinstance(r, dict) and r.get("when") == "never" for r in body["rules"])
            and body["rules"]
        }
        signature_jobs = option_signature_job()

        for module, provided in module_workflows().items():
            if not any(module in f for f in included_files):
                continue
            missing = [
                o for o in provided
                if o not in workflow_options
                and signature_jobs.get(o) not in disabled_jobs
            ]
            if missing:
                findings.append(Finding(
                    "P2", "Unreachable Module", f"{ci_file.name}:variables.WORKFLOW",
                    f"Module '{module}' is included but its workflow option(s) "
                    f"{', '.join(missing)} are not declared, so those jobs can never be "
                    f"dispatched manually. Declare the option, or disable the job with "
                    f"`rules: [{{when: never}}]` to retire it deliberately."
                ))

    # Check IMAGE_REPOSITORY placeholder & naming standard
    img_repo = variables.get("IMAGE_REPOSITORY", "")
    if isinstance(img_repo, str) and img_repo:
        if "myorg/" in img_repo or "[PRODUCT_NAME]" in img_repo:
            findings.append(Finding(
                "P1", "Generic Image Repository", f"{ci_file.name}:variables.IMAGE_REPOSITORY",
                f"IMAGE_REPOSITORY '{img_repo}' contains generic placeholder ('myorg' or '[PRODUCT_NAME]'). Must use confirmed product name (e.g. 'myapp/order-backend')."
            ))
        elif img_repo.endswith("-service"):
            findings.append(Finding(
                "P1", "Forbidden 'service' Suffix", f"{ci_file.name}:variables.IMAGE_REPOSITORY",
                f"IMAGE_REPOSITORY '{img_repo}' uses forbidden generic suffix '-service'. Sub-component must be 'backend', 'frontend', 'worker', or 'gateway'."
            ))

    findings.extend(check_job_needs(data, ci_file))
    findings.extend(check_script_style(data, ci_file))

    # 3. Strict Job Separation Check
    job_names = set(data.keys()) if isinstance(data, dict) else set()
    dep_jobs = [j for j in job_names if "Dependency:Download" in j or ":Download" in j or "Dependency" in j]
    # Image:Build and Chart:Build are packaging jobs, not the application build. Counting
    # them let a repo with no application build pass by coincidence, and hid the real
    # question: does this project compile anything?
    build_jobs = [j for j in job_names
                  if ("Project:Build" in j or j == "build" or j.endswith(":Build"))
                  and j not in ("Image:Build", "Chart:Build")]
    test_jobs = [j for j in job_names if "Project:Unit:Test" in j or ":Test:Unit" in j or j == "test" or ":Test" in j]

    # The 3-job separation standard (download -> build -> test) presupposes application
    # code. A pipeline that includes no language module has none: chart-only repos,
    # YAML/template libraries, docs repos. Requiring those jobs there is a false positive.
    has_language_module = any(
        f"{lang}/" in f for f in included_files
        for lang in ("nodejs", "python", "golang", "java")
    )
    is_chart_only_pipeline = not has_language_module and not any(
        "image/" in f for f in included_files
    )

    if (not dep_jobs and not is_chart_only_pipeline
            and not any("common/.mono" in f for f in included_files)
            and not any("mono/" in f for f in included_files)):
        findings.append(Finding(
            "P1", "Missing CI Job", f"{ci_file.name}",
            "Missing a stack-specific dependency-download job in stage 'prepare' (for example, "
            "Python:Dependency:Download, Java:Dependency:Download, or Node:Dependency:Download). "
            "Go may use the library-provided Go:Dependency:Download job. Dependencies must be pre-cached."
        ))

    # Interpreted stacks frequently have nothing to compile. Python was already exempt;
    # a plain-JavaScript service is the same case, and so is any repo whose Dockerfile
    # installs from the bind-mounted cache -- there is no build output to produce. Demand
    # Project:Build only where a build script actually exists to run.
    is_python_service = any("python/" in f for f in included_files)
    has_build_script = _declares_build_script(ci_file.parent)
    if (not build_jobs and not is_chart_only_pipeline
            and not any("common/.mono" in f for f in included_files)
            and not any("chart/" in f for f in included_files)
            and not is_python_service and has_build_script):
        findings.append(Finding(
            "P1", "Missing CI Job", f"{ci_file.name}",
            "Missing dedicated Project:Build job in stage 'build'. This project declares a "
            "build script, so its compiled output must be produced and artifacted in CI "
            "rather than built inside the image."
        ))

    if (not test_jobs and not is_chart_only_pipeline
            and not any("common/.mono" in f for f in included_files)
            and not any("chart/" in f for f in included_files)):
        findings.append(Finding(
            "P2", "Missing CI Job", f"{ci_file.name}",
            "Missing dedicated Project:Unit:Test job in stage 'test'."
        ))

    # 3a. DAG Needs Override Check for Multi-Test / Multi-Build Pipelines
    # When multiple test jobs (e.g. Project:Unit:Test:Frontend & Project:Unit:Test:Backend)
    # or custom build jobs exist, downstream library jobs (Sonarqube, Image:Build)
    # that default to single canonical jobs [Project:Unit:Test] or [Project:Build] MUST
    # override needs: at the project level. Otherwise GitLab CI DAG skips waiting for them,
    # running SonarQube without coverage or Image:Build without built assets.
    has_sonarqube = any("sonarqube/" in f for f in included_files) or "sonarqube" in workflow_options
    if has_sonarqube:
        needs_sonar_override = (
            len(test_jobs) > 1 or
            (len(test_jobs) == 1 and "Project:Unit:Test" not in test_jobs) or
            len(build_jobs) > 1 or
            (len(build_jobs) == 1 and "Project:Build" not in build_jobs)
        )
        if needs_sonar_override:
            sonar_job = data.get("Sonarqube")
            is_disabled = (
                isinstance(sonar_job, dict)
                and isinstance(sonar_job.get("rules"), list)
                and all(isinstance(r, dict) and r.get("when") == "never" for r in sonar_job["rules"])
                and sonar_job["rules"]
            )
            if not is_disabled:
                if not isinstance(sonar_job, dict) or "needs" not in sonar_job:
                    findings.append(Finding(
                        "P1", "Missing SonarQube DAG Needs Override", f"{ci_file.name}:Sonarqube",
                        f"Pipeline defines multiple or custom test/build jobs ({', '.join(sorted(test_jobs + build_jobs))}), "
                        "but 'Sonarqube' does not override 'needs:' at the project level. The shared library template only depends "
                        "on 'Project:Build' and 'Project:Unit:Test'. Without overriding 'needs:', GitLab CI DAG runs SonarQube "
                        "immediately without waiting for test completion or collecting coverage reports. "
                        "Override 'Sonarqube.needs' at the project level to explicitly list all test and build jobs."
                    ))
                else:
                    sonar_needs = sonar_job.get("needs")
                    needed_jobs = set()
                    if isinstance(sonar_needs, list):
                        for entry in sonar_needs:
                            if isinstance(entry, dict) and "job" in entry:
                                needed_jobs.add(entry["job"])
                            elif isinstance(entry, str):
                                needed_jobs.add(entry)
                    missing_tests = [tj for tj in test_jobs if tj not in needed_jobs]
                    if missing_tests:
                        findings.append(Finding(
                            "P1", "Incomplete SonarQube DAG Needs", f"{ci_file.name}:Sonarqube.needs",
                            f"SonarQube 'needs:' is missing test job(s): {', '.join(sorted(missing_tests))}. "
                            "All test jobs must be listed in 'Sonarqube.needs' (with artifacts: true, optional: true) "
                            "so SonarQube waits for unit tests and ingests test/coverage reports."
                        ))

    has_image = any("image/" in f for f in included_files) or "image-build-and-push" in workflow_options
    if has_image and build_jobs:
        needs_image_override = len(build_jobs) > 1 or (len(build_jobs) == 1 and "Project:Build" not in build_jobs)
        if needs_image_override:
            image_job = data.get("Image:Build")
            is_disabled = (
                isinstance(image_job, dict)
                and isinstance(image_job.get("rules"), list)
                and all(isinstance(r, dict) and r.get("when") == "never" for r in image_job["rules"])
                and image_job["rules"]
            )
            if not is_disabled:
                if not isinstance(image_job, dict) or "needs" not in image_job:
                    findings.append(Finding(
                        "P1", "Missing Image:Build DAG Needs Override", f"{ci_file.name}:Image:Build",
                        f"Pipeline defines multiple or custom build jobs ({', '.join(sorted(build_jobs))}), "
                        "but 'Image:Build' does not override 'needs:' at the project level. The shared library template only depends "
                        "on 'Project:Build'. Without overriding 'needs:', GitLab CI DAG will run the image build without "
                        "waiting for build artifacts. Override 'Image:Build.needs' at the project level to list all build jobs."
                    ))
                else:
                    image_needs = image_job.get("needs")
                    needed_jobs = set()
                    if isinstance(image_needs, list):
                        for entry in image_needs:
                            if isinstance(entry, dict) and "job" in entry:
                                needed_jobs.add(entry["job"])
                            elif isinstance(entry, str):
                                needed_jobs.add(entry)
                    missing_builds = [bj for bj in build_jobs if bj not in needed_jobs]
                    if missing_builds:
                        findings.append(Finding(
                            "P1", "Incomplete Image:Build DAG Needs", f"{ci_file.name}:Image:Build.needs",
                            f"Image:Build 'needs:' is missing build job(s): {', '.join(sorted(missing_builds))}. "
                            "All upstream build jobs providing artifacts for the image build must be listed in 'Image:Build.needs'."
                        ))

    # Check if frontend SPA project defines placeholder build variables in Project:Build
    entrypoint_script = ci_file.parent / "docker-entrypoint.sh"
    if entrypoint_script.exists():
        try:
            ep_text = entrypoint_script.read_text(encoding="utf-8", errors="ignore")
            if "sed" in ep_text and "<" in ep_text:
                build_job_data = data.get("Project:Build", {})
                build_vars = build_job_data.get("variables", {}) if isinstance(build_job_data, dict) else {}
                has_placeholders = any(isinstance(v, str) and v.startswith("<") and v.endswith(">") for v in build_vars.values())
                if not has_placeholders:
                    findings.append(Finding(
                        "P2", "Missing Frontend Build Variables", f"{ci_file.name}:Project:Build",
                        "Frontend SPA builds with runtime placeholder substitution should declare build-time placeholder variables "
                        "in 'Project:Build.variables' (e.g. VAR_NAME: '<VAR_NAME>'). This allows bundlers to bake placeholder tokens "
                        "into dist/ for runtime substitution by docker-entrypoint.sh without requiring placeholder defaults in dev code."
                    ))
        except Exception:
            pass

    # 3b. Library contract checks learned from consumer repos -------------------
    #
    # Each of these fails at RUNTIME rather than at pipeline creation, which is why
    # they are worth asserting statically: the pipeline is created, green, and wrong.
    variables = data.get("variables") if isinstance(data.get("variables"), dict) else {}

    # PROJECT_CACHE_KEY: common/ defaults it to "", so an omission is silently legal.
    # An empty key collapses all caches in the project to the same identity and makes
    # sonarqube/ build the literal "sonar-". Declare a nonempty stack or project key.
    cache_key = str(variables.get("PROJECT_CACHE_KEY", "") or "").strip()
    if not cache_key:
        findings.append(Finding(
            "P2", "Missing PROJECT_CACHE_KEY", f"{ci_file.name}:variables",
            "PROJECT_CACHE_KEY is not declared. It defaults to an empty string, which leaves "
            "cache identities empty and makes sonarqube/'s key the literal 'sonar-'. Declare "
            "a nonempty stack or project key, e.g. PROJECT_CACHE_KEY: \"node\" or \"chat\"."
        ))

    # A module whose repo-level prerequisite is absent creates a pipeline that fails in
    # the job, long after review.
    # `included_files` was rebuilt here from `file:` alone, silently discarding the
    # `local:`/`template:`/`remote:` handling built above -- so every check below this
    # point missed a module brought in that way. Reuse the one list.
    included_files = [str(f) for f in included_files if f]

    _MODULE_PREREQS = [
        ("sonarqube/", "sonar.properties",
         "The Sonarqube job passes '-Dproject.settings=sonar.properties'; without that file at "
         "the repo root the job fails at runtime. It also needs SONARQUBE_TOKEN and SONAR_URL."),
        ("readme/.migration-guide", "MIGRATION.md",
         "Migration:Check Existence is allow_failure:false, so every release fails without "
         "MIGRATION.md. If this repo publishes no versioned contract others pin, drop the "
         "include instead of adding the file."),
    ]
    for module_frag, required_file, why in _MODULE_PREREQS:
        if any(module_frag in f for f in included_files):
            if not (ci_file.parent / required_file).exists():
                findings.append(Finding(
                    "P1", "Module Prerequisite Missing", f"{ci_file.name}:include",
                    f"'{module_frag}' is included but {required_file} is missing. {why}"
                ))

    # USE_DOCKER_BUILDX is a coupled decision, not a feature toggle. The library default
    # is "false"; .image-common already exports DOCKER_BUILDKIT=1, so BuildKit syntax
    # (RUN --mount) works without it. Setting it adds a builder container and pushes a
    # per-branch buildcache tag, with mode=max, into the image repository.
    if str(variables.get("USE_DOCKER_BUILDX", "")).strip().lower() == "true":
        df = ci_file.parent / "Dockerfile"
        df_text = df.read_text(encoding="utf-8", errors="ignore") if df.exists() else ""
        findings.append(Finding(
            "P2", "USE_DOCKER_BUILDX Enabled", f"{ci_file.name}:variables",
            "USE_DOCKER_BUILDX is 'true' (library default is 'false'). This pushes a "
            "'buildcache-<branch>' tag with mode=max into the image repository on every "
            "branch, requires push credentials in the build job, and pulls a buildkit "
            "container before the Dockerfile is read. Note DOCKER_BUILDKIT=1 is already "
            "set by .image-common, so 'RUN --mount' does NOT require this flag -- confirm "
            "the build actually fails without it. Never pair mode=max with a secret "
            "passed as ARG: intermediate layers leave the runner."))

        # A bind mount reads from the build context, so a denied path yields an EMPTY
        # mount rather than an error -- the install silently goes to the network instead.
        for m in re.finditer(r"--mount=type=bind[^\n]*?source=([^\s,]+)", df_text):
            # Strip a leading "./" only -- lstrip("./") would eat the dot of ".npm".
            src = re.sub(r"^\./", "", m.group(1).strip())
            di = ci_file.parent / ".dockerignore"
            di_text = di.read_text(encoding="utf-8", errors="ignore") if di.exists() else ""
            allowed = any(ln.strip().lstrip("!").rstrip("/*").rstrip("/") == src.rstrip("/")
                          for ln in di_text.splitlines() if ln.strip().startswith("!"))
            if di.exists() and not allowed:
                findings.append(Finding(
                    "P1", "Bind Mount Source Not In Build Context",
                    f"Dockerfile:--mount source={src}",
                    f"The Dockerfile bind-mounts '{src}', but .dockerignore does not "
                    f"re-admit it. A denied mount source is empty rather than an error, so "
                    f"an offline install silently falls back to the network or fails with a "
                    f"cache miss. Add '!{src}' and '!{src}/**' to .dockerignore."))

    # Runtime anchors: .Node:24, .Python:12, .Go, .Java — they own image: and cache.
    _ANCHOR_RE = re.compile(r"^\.(Node|Python|Go|Java)[:0-9]*$")

    # A consumer that re-declares image: has taken ownership of a decision the runtime
    # anchor exists to make, and pins an image the library can no longer move. It is
    # almost always a workaround for a library or base-image defect -- which then never
    # gets reported, because the workaround works.
    for job, details in data.items():
        if job.startswith(".") or not isinstance(details, dict):
            continue
        ext = details.get("extends")
        ext = ext if isinstance(ext, list) else [ext] if isinstance(ext, str) else []
        if not any(isinstance(e, str) and _ANCHOR_RE.match(e) for e in ext):
            continue
        if "image" in details:
            findings.append(Finding(
                "P2", "Consumer Image Override", f"{ci_file.name}:{job}",
                f"Job '{job}' extends a runtime anchor but re-declares 'image:'. The anchor "
                f"owns the image; overriding it pins a version the library can no longer "
                f"move, and is usually a workaround for a library or base-image defect. Fix "
                f"it in the anchor instead (e.g. an ENTRYPOINT that needs "
                f"'entrypoint: [\"\"]' belongs in the anchor's map-form image, as "
                f"'.Python:12' declares it) and drop the override."))

    # extends: order. The runtime anchor (.Node:24, .Python:12, .Go) sets image and cache;
    # the job template sets stage, rules, needs and artifacts. GitLab merges extends left to
    # right with LATER entries winning, so anchor-last lets the anchor overwrite keys the job
    # template owns. Every consumer example in the library README lists the anchor first.
    for job, details in data.items():
        if job.startswith(".") or not isinstance(details, dict):
            continue
        ext = details.get("extends")
        if not isinstance(ext, list) or len(ext) < 2:
            continue
        anchor_idx = [i for i, e in enumerate(ext) if isinstance(e, str) and _ANCHOR_RE.match(e)]
        if anchor_idx and anchor_idx[0] != 0:
            findings.append(Finding(
                "P2", "Extends Order", f"{ci_file.name}:{job}",
                f"Job '{job}' lists the runtime anchor '{ext[anchor_idx[0]]}' after the job "
                f"template. extends merges left to right with later entries winning, so the "
                f"anchor overwrites keys the job template owns. Put the anchor first."
            ))

    # 4. Script Override Boundary Check
    allowed_script_jobs = {"Project:Build", "Project:Unit:Test", "Project:Version:Init"}
    for job, details in data.items():
        if job.startswith(".") or job in CI_RESERVED_KEYS:
            continue
        if isinstance(details, dict) and "script" in details:
            if job not in allowed_script_jobs and not job.startswith("Project:Unit:Test") and job not in build_jobs:
                findings.append(Finding(
                    "P1", "Illicit Script Override", f"{ci_file.name}:{job}",
                    f"Job '{job}' defines a custom 'script:'. Only Project:Build and Project:Unit:Test scripts may be overridden."
                ))

    # 5. Public Image Dependency Proxy Check
    def extract_images_from_item(item: Any) -> List[str]:
        imgs = []
        if isinstance(item, str):
            imgs.append(item)
        elif isinstance(item, dict):
            if "name" in item and isinstance(item["name"], str):
                imgs.append(item["name"])
        elif isinstance(item, list):
            for sub in item:
                imgs.extend(extract_images_from_item(sub))
        return imgs

    ci_images_to_check: List[Tuple[str, str]] = []

    if "image" in data:
        for img in extract_images_from_item(data["image"]):
            ci_images_to_check.append(("root.image", img))

    if "default" in data and isinstance(data["default"], dict):
        if "image" in data["default"]:
            for img in extract_images_from_item(data["default"]["image"]):
                ci_images_to_check.append(("default.image", img))
        if "services" in data["default"]:
            for img in extract_images_from_item(data["default"]["services"]):
                ci_images_to_check.append(("default.services", img))

    for job, details in data.items():
        if not isinstance(details, dict) or job.startswith("."):
            continue
        if "image" in details:
            for img in extract_images_from_item(details["image"]):
                ci_images_to_check.append((f"{job}.image", img))
        if "services" in details:
            for img in extract_images_from_item(details["services"]):
                ci_images_to_check.append((f"{job}.services", img))

    lines_list = content.splitlines()
    for loc, img_name in ci_images_to_check:
        unproxied, suggested = classify_unproxied_public_image(img_name)
        if unproxied:
            found_line = 1
            for lno, lstr in enumerate(lines_list, start=1):
                if img_name in lstr:
                    found_line = lno
                    break
            findings.append(Finding(
                "P1", "External Image Dependency Proxy Violation", f"{ci_file.name}:{found_line} ({loc})",
                f"External container image '{img_name}' pulls from internet without cache. Must use GitLab Dependency Proxy: '{suggested}'."
            ))

    return findings, data


def library_refs(data: Dict[str, Any]) -> Dict[str, str]:
    """{library repo name: ref} for every `include: project:` the pipeline pulls in.

    The ref on an include is the consumer's pinned library version. It was parsed and
    thrown away before, so nothing could tell which migrations the repo still owes.
    """
    found: Dict[str, str] = {}
    inc = data.get("include") if isinstance(data, dict) else None
    for entry in (inc if isinstance(inc, list) else [inc] if inc else []):
        if not isinstance(entry, dict):
            continue
        project, ref = entry.get("project"), entry.get("ref")
        if isinstance(project, str) and isinstance(ref, str) and ref:
            found.setdefault(project.rstrip("/").split("/")[-1], ref)
    return found


def check_job_needs(data: Dict[str, Any], ci_file: Path) -> List[Finding]:
    """Every job declares `needs:`, and every entry sets `optional:` explicitly.

    Without `needs:` a job waits for its entire preceding stage, delaying start and
    burning runner minutes on work whose upstream has already failed. `optional:`
    must be explicit because the correct value is a real decision: `true` where the
    upstream job is gated out of some workflows, `false` where its artifact is
    genuinely required.
    """
    findings: List[Finding] = []
    for name, body in iter_jobs(data):
        # A job that only overrides rules/variables inherits its DAG from the template.
        if not any(k in body for k in ("script", "extends", "trigger")):
            continue
        if "needs" not in body:
            if "extends" in body:
                continue  # needs may be inherited; the template library owns it
            findings.append(Finding(
                "P2", "Missing Job Needs", f"{ci_file.name}:{name}",
                f"Job '{name}' declares no 'needs:', so it waits for the whole previous "
                f"stage. Add needs: with optional: set deliberately."
            ))
            continue

        needs = body["needs"]
        if not isinstance(needs, list):
            continue
        for entry in needs:
            if isinstance(entry, str):
                findings.append(Finding(
                    "P2", "Implicit Needs Optionality", f"{ci_file.name}:{name}",
                    f"Job '{name}' needs '{entry}' in shorthand form, which defaults to "
                    f"optional: false. Use the mapping form and set optional: explicitly."
                ))
            elif isinstance(entry, dict) and "job" in entry and "optional" not in entry:
                findings.append(Finding(
                    "P2", "Implicit Needs Optionality", f"{ci_file.name}:{name}",
                    f"Job '{name}' needs '{entry['job']}' without an explicit 'optional:'. "
                    f"Defaults to false, which fails pipeline creation when that job is "
                    f"gated out of the dispatched workflow."
                ))
    return findings


def check_script_style(data: Dict[str, Any], ci_file: Path) -> List[Finding]:
    """Script entries are single-line list items unless genuinely multi-line.

    Wrapping a lone simple command in a `- |` block scalar adds a level of
    indirection for no benefit and hides the command from a quick scan.
    """
    findings: List[Finding] = []
    for name, body in iter_jobs(data):
        for key in ("script", "before_script", "after_script"):
            entries = body.get(key)
            if not isinstance(entries, list):
                continue
            for idx, entry in enumerate(entries):
                if not isinstance(entry, str):
                    continue
                # A `- |` block scalar round-trips with its trailing newline intact,
                # whereas a plain list item does not. That is the only signal in the
                # parsed document that distinguishes the two forms.
                if not entry.endswith("\n"):
                    continue
                lines = [ln for ln in entry.splitlines() if ln.strip()]
                if len(lines) != 1:
                    continue  # genuinely multi-line, correct use of a block
                if MULTILINE_SHELL_MARKERS.search(entry):
                    continue  # control flow / heredoc / function on one line
                findings.append(Finding(
                    "P2", "Script Block Overuse", f"{ci_file.name}:{name}.{key}[{idx}]",
                    f"Single simple command wrapped in a block scalar: '{lines[0].strip()[:60]}'. "
                    f"Use a plain single-line list item; reserve '- |' for if/loop/heredoc."
                ))
    return findings


# A dependency tree is CACHED, never artifacted. The language modules already express
# this: every `needs:` in nodejs/python/golang uses `artifacts: false`, and the cache
# and its siblings carry `cache: policy: pull`. Publishing node_modules/ or .venv/ as a
# job artifact uploads tens of thousands of files to coordinator storage on every run,
# to deliver a tree the cache already holds.
DEPENDENCY_DIRS = ("node_modules", ".venv", "vendor", ".m2", "site-packages")


def check_dependency_artifacts(data: Dict[str, Any], ci_file: Path) -> List[Finding]:
    findings: List[Finding] = []
    for name, body in iter_jobs(data):
        artifacts = body.get("artifacts")
        if not isinstance(artifacts, dict):
            continue
        paths = artifacts.get("paths")
        if not isinstance(paths, list):
            continue
        for entry in paths:
            entry_str = str(entry)
            for dep_dir in DEPENDENCY_DIRS:
                if re.search(r"(^|/)" + re.escape(dep_dir) + r"/?$", entry_str.rstrip("/")):
                    findings.append(Finding(
                        "P1", "Dependency Directory Artifacted", f"{ci_file.name}",
                        f"Job '{name}' publishes the dependency directory '{entry_str}' as an artifact. "
                        "Dependency trees are restored from the cache, never uploaded as artifacts -- "
                        "the language module already warms and pulls that cache. Delete the artifacts "
                        "block; if the image build needs the tree, bind-mount the cache in the "
                        "Dockerfile and install offline."
                    ))
                    break
    return findings


def ci_checks(repo: Path, shape: Optional[str] = None) -> Tuple[List[Finding], Dict[str, Any]]:
    """Adapter entry point. Returns (findings, parsed CI data for the harness).

    `shape` is accepted and unused: GitLab expresses project shape through the WORKFLOW
    option list, which check_gitlab_ci already validates against the include: list.
    Keeping the signature uniform lets the harness call every adapter identically.
    """
    ci_file = repo / ".gitlab-ci.yml"
    findings, data = check_gitlab_ci(ci_file)
    findings.extend(check_dependency_artifacts(data, ci_file))
    return findings, data
