#!/usr/bin/env python3
"""
gitops-helper.py - Core Engine for the gitops-app-manager AI Skill

Provides robust automation for:
1. Branch resolution & similarity matching (e.g., mapping 'development'/'develop' -> 'dev')
2. GitOps repository introspection (Chart.yaml, values.yaml, domains in values/*.yaml)
3. Helm service scaffolding (with mandatory 'enabled: false', 128Mi memory, /dev image suffix)
4. Extras manifest scaffolding (deployment.yaml under extras/manifests/<svc>)
5. Komodo Docker Compose service scaffolding & stack name computation
6. Application CI/CD pipeline snippet generation (.argocd.gitlab-ci.yml / .komodo.gitlab-ci.yml)
7. ArgoCD Root App-of-Apps bootstrap manifest generation
8. ArgoCD Web UI sync URL computation
"""

import argparse
import difflib
import json
import os
import re
import shutil
import ssl
import sys
import subprocess
import urllib.request
import urllib.error
import yaml
from typing import Dict, List, Optional, Tuple, Any

ENV_ALIASES = {
    "dev": ["dev", "development", "develop"],
    "qa": ["qa", "test", "testing"],
    "staging": ["staging", "stage", "stg"],
    "pre-prod": ["pre-prod", "preprod", "pre_prod", "pre production", "pre prod", "uat"],
    "prod": ["prod", "production"],
}

# Inverted mapping: development -> dev, develop -> dev, etc.
INVERTED_ENV_ALIASES = {}
for canonical, aliases in ENV_ALIASES.items():
    for alias in aliases:
        INVERTED_ENV_ALIASES[alias.lower()] = canonical


def normalize_env(env_str: str) -> str:
    """Normalizes an environment string using known aliases."""
    clean = env_str.strip().lower()
    return INVERTED_ENV_ALIASES.get(clean, clean)


def match_branch_similarity(target_branch: str, available_branches: List[str]) -> Tuple[Optional[str], List[str]]:
    """
    Finds exact or similar branches.
    Returns (exact_or_canonical_match, candidate_suggestions).
    """
    clean_target = target_branch.strip()
    if clean_target in available_branches:
        return clean_target, []

    parts = clean_target.split("/")
    if len(parts) == 2:
        product, env = parts[0], parts[1]
        canonical_env = normalize_env(env)

        # Look for product/canonical_env in available branches
        for b in available_branches:
            b_parts = b.split("/")
            if len(b_parts) == 2:
                if b_parts[0].lower() == product.lower():
                    if normalize_env(b_parts[1]) == canonical_env:
                        return b, []

        # If product matches, collect all branches for that product
        product_matches = [b for b in available_branches if b.startswith(f"{product}/")]
        if product_matches:
            # Fuzzy match within the product branches
            close = difflib.get_close_matches(clean_target, product_matches, n=5, cutoff=0.4)
            return None, close if close else product_matches

    # Global fuzzy match across all available branches
    close_all = difflib.get_close_matches(clean_target, available_branches, n=5, cutoff=0.5)
    return None, close_all


def discover_domains_from_values(values_dir: str) -> List[str]:
    """Scans values directory recursively to discover domains used in routes."""
    domains = set()
    if not os.path.isdir(values_dir):
        return []

    domain_pattern = re.compile(r'(?:domain|host|hostname)\s*:\s*["\']?([a-zA-Z0-9.-]+\.[a-zA-Z]{2,})["\']?', re.IGNORECASE)
    url_pattern = re.compile(r'https?://(?:[a-zA-Z0-9.-]+\.)?([a-zA-Z0-9.-]+\.[a-zA-Z]{2,})', re.IGNORECASE)

    for root, _, files in os.walk(values_dir):
        for f in files:
            if f.endswith((".yaml", ".yml")):
                file_path = os.path.join(root, f)
                try:
                    with open(file_path, "r", encoding="utf-8") as fh:
                        content = fh.read()
                        for match in domain_pattern.finditer(content):
                            val = match.group(1).strip()
                            domains.add(val)
                        for match in url_pattern.finditer(content):
                            val = match.group(1).strip()
                            domains.add(val)
                except Exception:
                    pass

    # Filter out common placeholders
    filtered = []
    for d in sorted(domains):
        if not any(placeholder in d for placeholder in ["contoso.com", "example.com", "myorg", "localhost"]):
            filtered.append(d)
    return filtered


def parse_chart_name(chart_file: str) -> str:
    """Extracts the 'name' field from Chart.yaml."""
    if not os.path.isfile(chart_file):
        return ""
    try:
        with open(chart_file, "r", encoding="utf-8") as fh:
            for line in fh:
                match = re.match(r"^name\s*:\s*([^\s#]+)", line)
                if match:
                    return match.group(1).strip()
    except Exception:
        pass
    return ""


def compute_argocd_app_names(
    chart_base: str,
    env: str,
    service_name: str,
    is_manifest: bool = False
) -> Dict[str, Any]:
    """
    Computes exact ArgoCD Application names following the enterprise formula.
    """
    clean_env = normalize_env(env)
    root_app = f"{chart_base}-{clean_env}-root"

    if is_manifest:
        # Single app for extras manifest: {chartBase}-extras-{env}-{dirName}
        extras_app = f"{chart_base}-extras-{clean_env}-{service_name}"
        return {
            "is_manifest": True,
            "root_app": root_app,
            "service_app": extras_app,
            "argocd_apps_arg": extras_app,  # Manifest deployments sync only this 1 app
        }
    else:
        # Helm deployments sync 2 apps: root app and service app
        service_app = f"{chart_base}-{service_name}-{clean_env}"
        return {
            "is_manifest": False,
            "root_app": root_app,
            "service_app": service_app,
            "argocd_apps_arg": f"{root_app} {service_app}",
        }


def compute_environment_url(hostname: str, env: str, domain: str) -> str:
    """Computes https://{hostname}.{env}.{domain} or returns empty if not exposed."""
    if not hostname or not domain:
        return ""
    clean_env = normalize_env(env)
    # If hostname already contains env/domain, use as is
    if domain in hostname:
        return f"https://{hostname}"
    # Standard formula
    return f"https://{hostname}.{clean_env}.{domain}"


def generate_helm_values_snippet(
    service_name: str,
    chart_name: str,
    chart_repo: str,
    chart_version: str = "0.1.0",
    group_name: Optional[str] = None
) -> str:
    """
    Generates the apps entry for values.yaml with mandatory 'enabled: false'.
    """
    if group_name:
        return f"""apps:
  {group_name}:
    {service_name}:
      enabled: false  # Mandatory: initially disabled for review
      chart:
        repoURL: "{chart_repo}"
        name: "{chart_name}"
        version: "{chart_version}"
"""
    else:
        return f"""apps:
  {service_name}:
    enabled: false  # Mandatory: initially disabled for review
    chart:
      repoURL: "{chart_repo}"
      name: "{chart_name}"
      version: "{chart_version}"
"""


def generate_service_override_values(
    service_name: str,
    product: str,
    env: str,
    image_registry: str = "registry.contoso.com",
    domain: Optional[str] = None,
    exposed: bool = True
) -> str:
    """
    Generates values/<service>.yaml with mandatory defaults:
    - 128Mi memory request and limit
    - /dev image repository suffix for dev environment
    - Ingress host auto-computed
    """
    clean_env = normalize_env(env)
    dev_suffix = "/dev" if clean_env == "dev" else ""
    image_repo = f"{image_registry}/{product}/{service_name}{dev_suffix}"

    routes_block = ""
    if exposed and domain:
        hostname = f"{service_name}.{clean_env}.{domain}"
        routes_block = f"""
routes:
  enabled: true
  host: "{hostname}"
"""

    return f"""# Values override for {service_name} ({clean_env})
image:
  repository: "{image_repo}"

resources:
  requests:
    cpu: "50m"
    memory: "128Mi"
  limits:
    cpu: "200m"
    memory: "128Mi"
{routes_block}"""


ASSETS_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "assets"))


def render_asset_template(template_name: str, mapping: Dict[str, str]) -> str:
    """Loads a template file from assets/ and replaces ${KEY} placeholders."""
    template_path = os.path.join(ASSETS_DIR, template_name)
    if not os.path.isfile(template_path):
        raise FileNotFoundError(f"Asset template not found: {template_path}")
    with open(template_path, "r", encoding="utf-8") as fh:
        content = fh.read()
    for key, val in mapping.items():
        content = content.replace(f"${{{key}}}", str(val))
    return content


def generate_manifest_deployment(
    service_name: str,
    image: str,
    namespace: str = "default",
    port: int = 8080,
    product: str = "myapp",
    component: str = "",
    domain: Optional[str] = None,
    env: str = "dev",
    app_version: str = "1.0.0"
) -> str:
    """
    Renders the production-grade Kubernetes manifest stack directly from
    skills/gitops-app-manager/assets/extras-deployment-template.yaml.
    """
    clean_env = normalize_env(env)
    hostname = f"{service_name}.{clean_env}.{domain}" if domain else f"{service_name}.local"
    comp = component or service_name

    mapping = {
        "SERVICE_NAME": service_name,
        "NAMESPACE": namespace,
        "PRODUCT_NAME": product,
        "COMPONENT_NAME": comp,
        "HOSTNAME": hostname,
        "IMAGE_NAME": image,
        "APP_VERSION": app_version,
    }
    return render_asset_template("extras-deployment-template.yaml", mapping)


def generate_argocd_root_app_manifest(
    product_prefix: str,
    env: str,
    repo_url: str,
    branch: str,
    server: str = "https://kubernetes.default.svc",
    namespace: str = "argo-cd"
) -> str:
    """
    Renders the root App-of-Apps bootstrap manifest directly from
    skills/gitops-app-manager/assets/root-app-template.yaml.
    """
    clean_env = normalize_env(env)
    root_name = f"{product_prefix}-{clean_env}-root"

    mapping = {
        "ROOT_APP_NAME": root_name,
        "GITOPS_REPO_URL": repo_url,
        "GITOPS_BRANCH": branch,
    }
    return render_asset_template("root-app-template.yaml", mapping)


def generate_ci_injection_block(
    delivery_type: str,
    env: str,
    gitops_repo_url: str,
    gitops_branch: str,
    argocd_apps: Optional[str] = None,
    gitops_chart_values_file: Optional[str] = None,
    gitops_chart_app_yq_path: Optional[str] = None,
    gitops_manifest_file: Optional[str] = None,
    gitops_new_image: Optional[str] = None,
    komodo_stack_name: Optional[str] = None,
    gitops_service_image_yq_path: Optional[str] = None,
    gitops_compose_file: str = "docker-compose.yml",
    environment_url: Optional[str] = None,
    ci_ref: str = "dev"
) -> str:
    """Generates the CI include block for .gitlab-ci.yml."""
    clean_env = normalize_env(env)
    url_line = f"      environment_url: {environment_url}\n" if environment_url else ""

    if delivery_type == "argocd-helm":
        return f"""include:
  - remote: 'https://raw.githubusercontent.com/grootan-devops/gitlab-ci-library/{ci_ref}/deploy/gitops/.argocd.gitlab-ci.yml'
    inputs:
      environment: {clean_env}
      gitops_repo_url: {gitops_repo_url}
      gitops_branch: {gitops_branch}
      gitops_chart_values_file: {gitops_chart_values_file or "values.yaml"}
      gitops_chart_app_yq_path: {gitops_chart_app_yq_path or ".apps."}
      argocd_apps: {argocd_apps}
{url_line}"""

    elif delivery_type == "argocd-manifest":
        return f"""include:
  - remote: 'https://raw.githubusercontent.com/grootan-devops/gitlab-ci-library/{ci_ref}/deploy/gitops/.argocd.gitlab-ci.yml'
    inputs:
      environment: {clean_env}
      gitops_repo_url: {gitops_repo_url}
      gitops_branch: {gitops_branch}
      gitops_manifest_file: {gitops_manifest_file}
      gitops_new_image: {gitops_new_image}
      argocd_apps: {argocd_apps}
{url_line}"""

    elif delivery_type == "komodo":
        return f"""include:
  - remote: 'https://raw.githubusercontent.com/grootan-devops/gitlab-ci-library/{ci_ref}/deploy/gitops/.komodo.gitlab-ci.yml'
    inputs:
      environment: {clean_env}
      gitops_repo_url: {gitops_repo_url}
      gitops_branch: {gitops_branch}
      gitops_compose_file: {gitops_compose_file}
      gitops_service_image_yq_path: {gitops_service_image_yq_path}
      komodo_stack_name: {komodo_stack_name}
{url_line}"""

    return ""


import shutil


def check_cli_tool(name: str) -> Dict[str, Any]:
    """Checks presence of CLI tool and its version."""
    path = shutil.which(name)
    if not path:
        return {"installed": False, "path": None, "version": None}
    version = None
    try:
        raw = subprocess.check_output([name, "--version"], text=True, stderr=subprocess.DEVNULL, timeout=4)
        version = raw.strip().splitlines()[0] if raw.strip() else None
    except Exception:
        version = "detected"
    return {"installed": True, "path": path, "version": version}


def check_clis_status() -> Dict[str, Any]:
    """Inspects status of platform and GitOps CLIs: gh, glab, argocd, km, kubectl, docker."""
    tools = ["gh", "glab", "argocd", "km", "kubectl", "docker"]
    res = {}
    for t in tools:
        res[t] = check_cli_tool(t)
    return res


def check_remote_branch_via_cli(repo_url: str, target_branch: str) -> Dict[str, Any]:
    """
    Checks if a remote branch exists via gh or glab CLI, with explicit 401, 403, 404 detection.
    """
    is_github = "github.com" in repo_url.lower()
    cli_name = "gh" if is_github else "glab"
    cli_path = shutil.which(cli_name)

    if not cli_path:
        return {
            "ok": False,
            "cli": cli_name,
            "error_code": "CLI_NOT_FOUND",
            "message": f"Mandatory CLI '{cli_name}' is not installed or not found on PATH. Aborting.",
            "exists": False
        }

    try:
        proc = subprocess.run(
            ["git", "ls-remote", "--heads", repo_url, target_branch],
            text=True,
            capture_output=True,
            timeout=10
        )
        stderr = proc.stderr.lower()
        stdout = proc.stdout.strip()

        if "401" in stderr or "unauthorized" in stderr or "authentication failed" in stderr:
            return {
                "ok": False,
                "cli": cli_name,
                "error_code": 401,
                "message": f"Authentication failed (401 Unauthorized) when accessing '{repo_url}'. Aborting.",
                "exists": False
            }
        if "403" in stderr or "forbidden" in stderr or "permission denied" in stderr:
            return {
                "ok": False,
                "cli": cli_name,
                "error_code": 403,
                "message": f"Access forbidden (403 Forbidden) for '{repo_url}'. Aborting.",
                "exists": False
            }
        if "404" in stderr or "not found" in stderr or "could not read from remote" in stderr:
            return {
                "ok": False,
                "cli": cli_name,
                "error_code": 404,
                "message": f"Repository or branch endpoint not found (404 Not Found) for '{repo_url}'. Aborting.",
                "exists": False
            }

        if proc.returncode != 0:
            return {
                "ok": False,
                "cli": cli_name,
                "error_code": proc.returncode,
                "message": f"Failed to query remote '{repo_url}': {proc.stderr.strip()}",
                "exists": False
            }

        exists = bool(stdout and target_branch in stdout)
        return {
            "ok": True,
            "cli": cli_name,
            "error_code": None,
            "message": "Branch exists" if exists else "Branch not found",
            "exists": exists
        }

    except subprocess.TimeoutExpired:
        return {
            "ok": False,
            "cli": cli_name,
            "error_code": "TIMEOUT",
            "message": f"Connection timed out while checking '{repo_url}'. Aborting.",
            "exists": False
        }
    except Exception as e:
        return {
            "ok": False,
            "cli": cli_name,
            "error_code": "EXCEPTION",
            "message": str(e),
            "exists": False
        }


def validate_platform_endpoint(url: str, platform_type: str) -> Dict[str, Any]:
    """
    Validates whether platform_endpoint is reachable over the network
    and confirms it is a genuine ArgoCD or Komodo endpoint.
    """
    clean_url = url.rstrip("/")
    p_type = platform_type.lower().strip()

    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE

    req = urllib.request.Request(
        clean_url,
        headers={"User-Agent": "Mozilla/5.0 gitops-app-manager"}
    )

    try:
        with urllib.request.urlopen(req, timeout=6, context=ctx) as resp:
            status_code = resp.status
            body = resp.read().decode("utf-8", errors="ignore").lower()
            headers_str = str(resp.headers).lower()
            return _evaluate_platform_signature(clean_url, p_type, status_code, body, headers_str)

    except urllib.error.HTTPError as e:
        status_code = e.code
        body = e.read().decode("utf-8", errors="ignore").lower()
        headers_str = str(e.headers).lower()
        return _evaluate_platform_signature(clean_url, p_type, status_code, body, headers_str)

    except urllib.error.URLError as e:
        reason = str(e.reason)
        msg = f"Endpoint '{clean_url}' is unreachable: {reason}."
        if "timed out" in reason.lower():
            msg += " (Request timed out. Please verify your VPN or network connection)."
        elif "connection refused" in reason.lower():
            msg += " (Connection refused. Service is not listening on this port)."
        elif "nodename nor servname provided" in reason.lower() or "name or service not known" in reason.lower():
            msg += " (DNS resolution failed. Hostname could not be resolved)."

        return {
            "reachable": False,
            "valid_type": False,
            "platform": p_type,
            "url": clean_url,
            "status_code": None,
            "message": msg
        }
    except Exception as e:
        return {
            "reachable": False,
            "valid_type": False,
            "platform": p_type,
            "url": clean_url,
            "status_code": None,
            "message": f"Endpoint probe failed: {e}"
        }


def _evaluate_platform_signature(url: str, p_type: str, status: int, body: str, headers: str) -> Dict[str, Any]:
    """Helper to check ArgoCD or Komodo signatures in HTTP response."""
    combined = body + " " + headers

    if p_type == "argocd":
        is_argo = (
            "argocd" in combined or
            "argo-cd" in combined or
            "argo cd" in combined or
            "/auth/login" in combined or
            "x-argo" in combined or
            "version" in combined and "builddate" in combined
        )
        if is_argo:
            return {
                "reachable": True,
                "valid_type": True,
                "platform": "argocd",
                "url": url,
                "status_code": status,
                "message": f"Endpoint '{url}' is reachable and verified as an ArgoCD server (HTTP {status})."
            }
        else:
            return {
                "reachable": True,
                "valid_type": False,
                "platform": "argocd",
                "url": url,
                "status_code": status,
                "message": f"Endpoint '{url}' is reachable (HTTP {status}), but did NOT return expected ArgoCD signatures."
            }

    elif p_type == "komodo":
        is_komodo = (
            "komodo" in combined or
            "periphery" in combined or
            "monitor lizard" in combined or
            "komo.do" in combined or
            "/api/v1" in combined
        )
        if is_komodo:
            return {
                "reachable": True,
                "valid_type": True,
                "platform": "komodo",
                "url": url,
                "status_code": status,
                "message": f"Endpoint '{url}' is reachable and verified as a Komodo Core server (HTTP {status})."
            }
        else:
            return {
                "reachable": True,
                "valid_type": False,
                "platform": "komodo",
                "url": url,
                "status_code": status,
                "message": f"Endpoint '{url}' is reachable (HTTP {status}), but did NOT return expected Komodo Core signatures."
            }

    return {
        "reachable": True,
        "valid_type": True,
        "platform": p_type,
        "url": url,
        "status_code": status,
        "message": f"Endpoint '{url}' is reachable (HTTP {status})."
    }


def validate_repo_access(repo_url: str) -> Dict[str, Any]:
    """
    Validates that:
    1. gh or glab CLI is present and authenticated.
    2. The CLI credentials have active access to that specific gitops_repo_url.
    """
    is_github = "github.com" in repo_url.lower()
    cli_name = "gh" if is_github else "glab"
    cli_path = shutil.which(cli_name)

    if not cli_path:
        return {
            "ok": False,
            "cli": cli_name,
            "authenticated": False,
            "has_repo_access": False,
            "message": f"Mandatory CLI '{cli_name}' is not installed. Please install {cli_name} to verify repository access."
        }

    # Verify repository read access directly via git ls-remote HEAD
    try:
        proc = subprocess.run(
            ["git", "ls-remote", "--heads", repo_url, "HEAD"],
            text=True,
            capture_output=True,
            timeout=8
        )
        stderr = proc.stderr.lower()

        if "401" in stderr or "unauthorized" in stderr or "authentication failed" in stderr:
            return {
                "ok": False,
                "cli": cli_name,
                "authenticated": False,
                "has_repo_access": False,
                "error_code": 401,
                "message": f"Authentication failed (401 Unauthorized) accessing '{repo_url}'. Please authenticate via '{cli_name} auth login'."
            }
        if "403" in stderr or "forbidden" in stderr or "permission denied" in stderr:
            return {
                "ok": False,
                "cli": cli_name,
                "authenticated": True,
                "has_repo_access": False,
                "error_code": 403,
                "message": f"Access forbidden (403 Forbidden) for '{repo_url}'. Your account lacks permissions for this GitOps repository."
            }
        if "404" in stderr or "not found" in stderr or "could not read from remote" in stderr:
            return {
                "ok": False,
                "cli": cli_name,
                "authenticated": True,
                "has_repo_access": False,
                "error_code": 404,
                "message": f"Repository not found (404 Not Found) for '{repo_url}'. Please check repository URL."
            }

        if proc.returncode != 0:
            return {
                "ok": False,
                "cli": cli_name,
                "authenticated": True,
                "has_repo_access": False,
                "error_code": proc.returncode,
                "message": f"Failed to access repository '{repo_url}': {proc.stderr.strip()}"
            }

        return {
            "ok": True,
            "cli": cli_name,
            "authenticated": True,
            "has_repo_access": True,
            "message": f"CLI '{cli_name}' is authenticated and has verified access to '{repo_url}'."
        }

    except subprocess.TimeoutExpired:
        return {
            "ok": False,
            "cli": cli_name,
            "authenticated": True,
            "has_repo_access": False,
            "message": f"Connection timed out accessing '{repo_url}'. Please check your network or VPN."
        }
    except Exception as e:
        return {
            "ok": False,
            "cli": cli_name,
            "authenticated": False,
            "has_repo_access": False,
            "message": str(e)
        }


def check_argocd_auth(server: Optional[str] = None, repo_url: Optional[str] = None) -> Dict[str, Any]:
    """
    Verifies if argocd CLI is installed, authenticated against server,
    and has repository access.
    """
    argocd_path = shutil.which("argocd")
    if not argocd_path:
        return {
            "installed": False,
            "authenticated": False,
            "has_repo_access": False,
            "message": "argocd CLI is not installed. Please install argocd CLI."
        }

    try:
        cmd = ["argocd", "context"]
        out = subprocess.check_output(cmd, text=True, stderr=subprocess.STDOUT, timeout=4)
        if server and server.replace("https://", "").replace("http://", "").rstrip("/") not in out:
            return {
                "installed": True,
                "authenticated": False,
                "has_repo_access": False,
                "current_context": out.strip(),
                "message": f"argocd CLI is not authenticated against '{server}' (Current context: {out.strip()})."
            }

        # Check repository permission if repo_url provided
        has_repo = True
        repo_msg = ""
        if repo_url:
            try:
                repo_list = subprocess.check_output(["argocd", "repo", "list"], text=True, stderr=subprocess.STDOUT, timeout=4)
                has_repo = repo_url in repo_list or repo_url.replace(".git", "") in repo_list
                if not has_repo:
                    repo_msg = f" GitOps repo '{repo_url}' is not yet registered in ArgoCD (will be accessed via root app)."
            except Exception:
                has_repo = True

        return {
            "installed": True,
            "authenticated": True,
            "has_repo_access": has_repo,
            "current_context": out.strip(),
            "message": f"argocd CLI is authenticated against '{server or 'cluster'}'.{repo_msg}"
        }
    except Exception as e:
        return {
            "installed": True,
            "authenticated": False,
            "has_repo_access": False,
            "message": f"argocd CLI authentication failed: {e}. Run 'argocd login {server or '<endpoint>'}'."
        }


def check_komodo_auth(endpoint: Optional[str] = None, repo_url: Optional[str] = None) -> Dict[str, Any]:
    """
    Verifies if km CLI is installed, configured for endpoint,
    and has repository/stack management access.
    """
    km_path = shutil.which("km")
    config_file = os.path.expanduser("~/.config/komodo/komodo.cli.toml")
    has_config = os.path.isfile(config_file)

    if not km_path:
        return {
            "installed": False,
            "authenticated": False,
            "has_repo_access": False,
            "config_file": config_file if has_config else None,
            "message": "km CLI is not installed. Please install km CLI via https://komo.do/docs/ecosystem/cli"
        }

    try:
        out = subprocess.check_output(["km", "config"], text=True, stderr=subprocess.STDOUT, timeout=4)
        if endpoint and endpoint.replace("https://", "").replace("http://", "").rstrip("/") not in out:
            return {
                "installed": True,
                "authenticated": False,
                "has_repo_access": False,
                "config_file": config_file,
                "message": f"km CLI host is not configured for '{endpoint}' (Check ~/.config/komodo/komodo.cli.toml)."
            }

        return {
            "installed": True,
            "authenticated": True,
            "has_repo_access": True,
            "config_file": config_file,
            "message": f"km CLI is configured and authenticated against '{endpoint or 'host'}'."
        }
    except Exception as e:
        return {
            "installed": True,
            "authenticated": has_config,
            "has_repo_access": False,
            "config_file": config_file if has_config else None,
            "message": f"km CLI detected, but configuration/auth check failed: {e}"
        }


def render_gitops_branch_scaffold(product_name: str, env: str, chart_name: Optional[str] = None) -> Dict[str, str]:
    """Renders initial Chart.yaml and values.yaml for an ArgoCD GitOps branch."""
    clean_env = normalize_env(env)
    c_name = chart_name or f"{product_name}-{clean_env}"
    chart_content = render_asset_template("gitops-branch-chart-template.yaml", {
        "CHART_NAME": c_name,
        "PRODUCT_NAME": product_name,
        "ENVIRONMENT": clean_env,
    })
    values_content = render_asset_template("gitops-branch-values-template.yaml", {
        "PRODUCT_NAME": product_name,
        "ENVIRONMENT": clean_env,
    })
    return {
        "Chart.yaml": chart_content,
        "values.yaml": values_content,
    }


def render_komodo_agent_compose(core_address: str, onboarding_key: str, server_name: str) -> str:
    """Renders /opt/komodo-agent/docker-compose.yml for Periphery agent."""
    return render_asset_template("komodo-agent-compose-template.yaml", {
        "KOMODO_CORE_ADDRESS": core_address,
        "PERIPHERY_ONBOARDING_KEY": onboarding_key,
        "SERVER_NAME": server_name,
    })


def render_komodo_smoke_compose(product_name: str, env: str) -> str:
    """Renders basic smoke-test nginx docker-compose.yml on port 80 for Komodo environment."""
    clean_env = normalize_env(env)
    return render_asset_template("komodo-smoke-compose-template.yaml", {
        "PRODUCT_NAME": product_name,
        "ENVIRONMENT": clean_env,
    })


def inspect_kube_context() -> Dict[str, Any]:
    """Inspects kubectl current-context and available contexts."""
    cur = ""
    all_ctx = []
    try:
        cur = subprocess.check_output(["kubectl", "config", "current-context"], text=True, stderr=subprocess.DEVNULL).strip()
    except Exception:
        cur = ""

    try:
        raw = subprocess.check_output(["kubectl", "config", "get-contexts", "-o", "name"], text=True, stderr=subprocess.DEVNULL).strip()
        all_ctx = [c.strip() for c in raw.splitlines() if c.strip()]
    except Exception:
        all_ctx = []

    return {
        "current_context": cur,
        "available_contexts": all_ctx,
        "has_context": bool(cur),
    }


def generate_sync_links(argocd_server: str, app_names: List[str]) -> List[str]:
    """Generates direct ArgoCD Web UI sync links."""
    server = argocd_server.rstrip("/")
    links = []
    for app in app_names:
        links.append(f"{server}/applications/argo-cd/{app}?view=tree&resource=&orphaned=false")
    return links


# ==============================================================================
# Service Schema Diff Engine
# ==============================================================================

APP_SCHEMA_KEYS = [
    "image",
    "resources",
    "autoscaling",
    "replicaCount",
    "replicas",
    "routes",
    "ingress",
    "service",
    "env",
    "envFrom",
    "config",
]


def extract_service_schema(values_data: Dict[str, Any], keys: Optional[List[str]] = None) -> Dict[str, Any]:
    """Filters a values dict down to application/service-level operational schema."""
    target_keys = keys or APP_SCHEMA_KEYS
    extracted = {}
    for key in target_keys:
        if key in values_data:
            extracted[key] = values_data[key]
    return extracted


def load_yaml_source(source: str, repo_path: Optional[str] = None) -> Dict[str, Any]:
    """Loads YAML from a file path or git rev:path (e.g. 'HEAD:values.yaml' or 'myapp/dev:values/chat.yaml')."""
    if ":" in source and not os.path.exists(source):
        rev, rel_path = source.split(":", 1)
        cwd = repo_path or os.getcwd()
        try:
            raw = subprocess.check_output(
                ["git", "show", f"{rev}:{rel_path}"],
                cwd=cwd,
                text=True,
                stderr=subprocess.PIPE
            )
            return yaml.safe_load(raw) or {}
        except subprocess.CalledProcessError as e:
            raise RuntimeError(f"Failed to read '{source}' from git in '{cwd}': {e.stderr.strip()}")
    else:
        if not os.path.isfile(source):
            raise FileNotFoundError(f"Source file not found: {source}")
        with open(source, "r", encoding="utf-8") as fh:
            return yaml.safe_load(fh) or {}


def diff_service_values(
    source1: str,
    source2: str,
    repo_path: Optional[str] = None,
    keys: Optional[List[str]] = None
) -> str:
    """
    Diffs only application service operational schema (image, resources, hpa, replicas, routes, env)
    between two values sources (files or git refs like 'branch:values.yaml').
    """
    data1 = load_yaml_source(source1, repo_path)
    data2 = load_yaml_source(source2, repo_path)

    schema1 = extract_service_schema(data1, keys)
    schema2 = extract_service_schema(data2, keys)

    yaml1_lines = yaml.dump(schema1, sort_keys=True, indent=2).splitlines()
    yaml2_lines = yaml.dump(schema2, sort_keys=True, indent=2).splitlines()

    diff = difflib.unified_diff(
        yaml1_lines,
        yaml2_lines,
        fromfile=source1,
        tofile=source2,
        lineterm=""
    )
    return "\n".join(diff) + ("\n" if diff else "")


# ==============================================================================
# Self-Test Suite
# ==============================================================================

def run_tests():
    print("Running gitops-helper self-tests...")

    # 1. Test branch normalization & similarity
    assert normalize_env("development") == "dev"
    assert normalize_env("develop") == "dev"
    assert normalize_env("production") == "prod"
    assert normalize_env("staging") == "staging"

    available = ["myapp/dev", "myapp/qa", "myapp/prod", "payment/dev"]
    match, candidates = match_branch_similarity("myapp/development", available)
    assert match == "myapp/dev", f"Expected myapp/dev, got {match}"

    match, candidates = match_branch_similarity("myapp/develop", available)
    assert match == "myapp/dev", f"Expected myapp/dev, got {match}"

    match, candidates = match_branch_similarity("myapp/pre-prod", available)
    assert match is None
    assert "myapp/prod" in candidates or "myapp/dev" in candidates

    # 2. Test ArgoCD Application Naming
    # Helm
    helm_res = compute_argocd_app_names("acme-cloud-myapp", "dev", "chat-frontend", is_manifest=False)
    assert helm_res["root_app"] == "acme-cloud-myapp-dev-root"
    assert helm_res["service_app"] == "acme-cloud-myapp-chat-frontend-dev"
    assert helm_res["argocd_apps_arg"] == "acme-cloud-myapp-dev-root acme-cloud-myapp-chat-frontend-dev"

    # Manifest Extras (1 app only)
    manifest_res = compute_argocd_app_names("acme-cloud-myapp", "dev", "clamav", is_manifest=True)
    assert manifest_res["service_app"] == "acme-cloud-myapp-extras-dev-clamav"
    assert manifest_res["argocd_apps_arg"] == "acme-cloud-myapp-extras-dev-clamav"

    # 3. Test Hostname & URL computation
    url = compute_environment_url("chat-frontend", "dev", "contoso.com")
    assert url == "https://chat-frontend.dev.contoso.com"

    # 4. Test values snippet contains enabled: false
    values_snip = generate_helm_values_snippet("chat-frontend", "chat-frontend", "oci://registry.contoso.com/charts")
    assert "enabled: false" in values_snip

    # 5. Test service override contains 128Mi and /dev suffix
    override_snip = generate_service_override_values("chat-frontend", "myapp", "dev", domain="contoso.com")
    assert "128Mi" in override_snip
    assert "/dev" in override_snip
    assert "chat-frontend.dev.contoso.com" in override_snip

    # 6. Test production-grade manifest generation
    manifest_doc = generate_manifest_deployment("clamav", "registry.contoso.com/tools/clamav:1.0.0", domain="contoso.com")
    assert "10001" in manifest_doc
    assert "readOnlyRootFilesystem: true" in manifest_doc
    assert "configMapRef:" in manifest_doc
    assert "secretRef:" in manifest_doc
    assert "ServiceAccount" in manifest_doc
    assert "Ingress" in manifest_doc
    assert "livenessProbe:" in manifest_doc
    assert "clamav.dev.contoso.com" in manifest_doc

    # 7. Test sync links
    links = generate_sync_links("https://argocd.contoso.com", ["acme-cloud-myapp-dev-root", "acme-cloud-myapp-chat-frontend-dev"])
    assert len(links) == 2
    assert "https://argocd.contoso.com/applications/argo-cd/acme-cloud-myapp-dev-root?view=tree&resource=&orphaned=false" in links[0]

    # 8. Test service schema diff
    v1 = {"image": {"tag": "1.0.0"}, "resources": {"requests": {"memory": "128Mi"}}, "randomK8sBoilerplate": 123}
    v2 = {"image": {"tag": "1.1.0"}, "resources": {"requests": {"memory": "256Mi"}}, "randomK8sBoilerplate": 999}
    s1 = extract_service_schema(v1)
    s2 = extract_service_schema(v2)
    assert "randomK8sBoilerplate" not in s1
    assert "randomK8sBoilerplate" not in s2
    assert s1["image"]["tag"] == "1.0.0"
    assert s2["image"]["tag"] == "1.1.0"

    print("All unit tests passed successfully!")


def main():
    parser = argparse.ArgumentParser(description="GitOps App Manager Helper CLI")
    parser.add_argument("--test", action="store_true", help="Run internal self-tests")
    parser.add_argument("--introspect", help="Path to local GitOps repo clone")
    parser.add_argument("--match-branch", help="Target branch name to match")
    parser.add_argument("--available-branches", nargs="*", default=[], help="List of available branches")
    parser.add_argument("--check-context", action="store_true", help="Inspect current and available Kubernetes contexts")
    parser.add_argument("--json", action="store_true", help="Output results in JSON format")
    parser.add_argument("--render-manifest", action="store_true", help="Render production Kubernetes manifest from assets/extras-deployment-template.yaml")
    parser.add_argument("--render-root-app", action="store_true", help="Render root app bootstrap manifest from assets/root-app-template.yaml")
    parser.add_argument("--service-name", help="Service name")
    parser.add_argument("--image", help="Container image")
    parser.add_argument("--namespace", default="default", help="Kubernetes namespace (default: default)")
    parser.add_argument("--port", type=int, default=8080, help="Container port (default: 8080)")
    parser.add_argument("--product", default="myapp", help="Product name or prefix (default: myapp)")
    parser.add_argument("--component", default="", help="Component name")
    parser.add_argument("--domain", help="Base domain (e.g. contoso.com)")
    parser.add_argument("--env", default="dev", help="Environment (default: dev)")
    parser.add_argument("--app-version", default="1.0.0", help="App version (default: 1.0.0)")
    parser.add_argument("--repo-url", help="GitOps repository URL")
    parser.add_argument("--branch", help="GitOps branch")
    parser.add_argument("--diff-values", nargs=2, metavar=("SOURCE1", "SOURCE2"), help="Diff application service schema (image, resources, hpa, replicas, routes, env) between two values files or git refs (e.g. branch:path)")
    parser.add_argument("--repo-path", help="Git repository path when using git refs with --diff-values")
    parser.add_argument("--diff-keys", nargs="*", help="Specific schema keys to compare (default: image, resources, autoscaling, replicaCount, replicas, routes, ingress, service, env, envFrom, config)")
    parser.add_argument("--check-clis", action="store_true", help="Inspect installation and version of platform CLIs (gh, glab, argocd, km, kubectl, docker)")
    parser.add_argument("--check-remote-branch", nargs=2, metavar=("REPO_URL", "BRANCH"), help="Verify remote branch via gh/glab with 401/403/404 handling")
    parser.add_argument("--check-argocd", nargs="?", const="", help="Check if argocd CLI is authenticated against endpoint")
    parser.add_argument("--check-komodo", nargs="?", const="", help="Check if km CLI is configured against endpoint")
    parser.add_argument("--validate-endpoint", nargs=2, metavar=("URL", "TYPE"), help="Validate platform endpoint reachability and verify ArgoCD or Komodo signature")
    parser.add_argument("--validate-repo", metavar="REPO_URL", help="Validate gh/glab CLI authentication and repository access permissions")
    parser.add_argument("--render-branch-scaffold", nargs=2, metavar=("PRODUCT", "ENV"), help="Render initial Chart.yaml and values.yaml for a new GitOps branch")
    parser.add_argument("--chart-name", help="Chart name override for branch scaffolding")
    parser.add_argument("--render-komodo-agent", nargs=3, metavar=("CORE_URL", "ONBOARDING_KEY", "SERVER_NAME"), help="Render /opt/komodo-agent/docker-compose.yml")
    parser.add_argument("--render-komodo-smoke", nargs=2, metavar=("PRODUCT", "ENV"), help="Render smoke-test nginx docker-compose.yml on port 80")

    args = parser.parse_args()

    if args.validate_endpoint:
        url, p_type = args.validate_endpoint
        res = validate_platform_endpoint(url, p_type)
        if args.json:
            print(json.dumps(res, indent=2))
        else:
            state = "OK" if (res["reachable"] and res["valid_type"]) else "FAILED"
            print(f"[{state}] {res['message']}")
        sys.exit(0 if (res["reachable"] and res["valid_type"]) else 1)

    if args.validate_repo:
        repo_url = args.validate_repo
        res = validate_repo_access(repo_url)
        if args.json:
            print(json.dumps(res, indent=2))
        else:
            state = "OK" if res["ok"] else "FAILED"
            print(f"[{state}] {res['message']}")
        sys.exit(0 if res["ok"] else 1)

    if args.check_clis:
        status = check_clis_status()
        if args.json:
            print(json.dumps(status, indent=2))
        else:
            print("Platform CLI Status:")
            for tool, data in status.items():
                state = f"OK ({data['version']})" if data["installed"] else "NOT FOUND"
                print(f"  {tool:10}: {state}")
        sys.exit(0)

    if args.check_remote_branch:
        repo_url, branch = args.check_remote_branch
        res = check_remote_branch_via_cli(repo_url, branch)
        if args.json:
            print(json.dumps(res, indent=2))
        else:
            if not res["ok"]:
                print(f"Error checking branch: {res['message']} (Code: {res['error_code']})")
                sys.exit(1)
            elif res["exists"]:
                print(f"Branch '{branch}' exists on remote '{repo_url}'.")
            else:
                print(f"Branch '{branch}' not found on remote '{repo_url}'.")
        sys.exit(0 if (res["ok"] and res["exists"]) else (0 if res["ok"] else 1))

    if args.check_argocd is not None:
        server = args.check_argocd if args.check_argocd else None
        res = check_argocd_auth(server, args.repo_url)
        if args.json:
            print(json.dumps(res, indent=2))
        else:
            print(f"ArgoCD Auth: {res['message']}")
        sys.exit(0 if res.get("authenticated") else 1)

    if args.check_komodo is not None:
        server = args.check_komodo if args.check_komodo else None
        res = check_komodo_auth(server, args.repo_url)
        if args.json:
            print(json.dumps(res, indent=2))
        else:
            print(f"Komodo CLI Auth: {res['message']}")
        sys.exit(0 if res.get("authenticated") else 1)

    if args.render_branch_scaffold:
        prod, env = args.render_branch_scaffold
        docs = render_gitops_branch_scaffold(prod, env, args.chart_name)
        if args.json:
            print(json.dumps(docs, indent=2))
        else:
            print("### Chart.yaml ###")
            print(docs["Chart.yaml"])
            print("\n### values.yaml ###")
            print(docs["values.yaml"])
        sys.exit(0)

    if args.render_komodo_agent:
        core_url, key, sname = args.render_komodo_agent
        compose = render_komodo_agent_compose(core_url, key, sname)
        print(compose)
        sys.exit(0)

    if args.render_komodo_smoke:
        prod, env = args.render_komodo_smoke
        smoke = render_komodo_smoke_compose(prod, env)
        print(smoke)
        sys.exit(0)

    if args.diff_values:
        src1, src2 = args.diff_values
        try:
            diff_text = diff_service_values(src1, src2, repo_path=args.repo_path, keys=args.diff_keys)
            if not diff_text.strip():
                print("No schema differences detected in application service values.")
            else:
                print(diff_text)
            sys.exit(0)
        except Exception as err:
            print(f"Error diffing values: {err}", file=sys.stderr)
            sys.exit(1)

    if args.check_context:
        ctx_info = inspect_kube_context()
        if args.json:
            print(json.dumps(ctx_info, indent=2))
        else:
            if ctx_info["has_context"]:
                print(f"Active Kubernetes Context: {ctx_info['current_context']}")
                print(f"Available Contexts: {', '.join(ctx_info['available_contexts'])}")
            else:
                print("No active Kubernetes context detected.")
                if ctx_info["available_contexts"]:
                    print(f"Available Contexts: {', '.join(ctx_info['available_contexts'])}")
                else:
                    print("No contexts found in kubeconfig. Please specify KUBECONFIG path.")
        sys.exit(0)

    if args.render_manifest:
        if not args.service_name or not args.image:
            parser.error("--render-manifest requires --service-name and --image")
        manifest = generate_manifest_deployment(
            service_name=args.service_name,
            image=args.image,
            namespace=args.namespace,
            port=args.port,
            product=args.product,
            component=args.component,
            domain=args.domain,
            env=args.env,
            app_version=args.app_version,
        )
        print(manifest)
        sys.exit(0)

    if args.render_root_app:
        if not args.repo_url or not args.branch:
            parser.error("--render-root-app requires --repo-url and --branch")
        manifest = generate_argocd_root_app_manifest(
            product_prefix=args.product,
            env=args.env,
            repo_url=args.repo_url,
            branch=args.branch,
        )
        print(manifest)
        sys.exit(0)

    if args.test:
        run_tests()
        sys.exit(0)

    if args.match_branch:
        match, candidates = match_branch_similarity(args.match_branch, args.available_branches)
        output = {
            "target": args.match_branch,
            "matched_branch": match,
            "candidates": candidates,
            "success": match is not None
        }
        if args.json:
            print(json.dumps(output, indent=2))
        else:
            if match:
                print(f"Matched: {match}")
            else:
                print(f"No exact match. Similar candidates: {', '.join(candidates)}")
        sys.exit(0)

    if args.introspect:
        repo_dir = args.introspect
        chart_name = parse_chart_name(os.path.join(repo_dir, "Chart.yaml"))
        domains = discover_domains_from_values(os.path.join(repo_dir, "values"))
        output = {
            "chart_name": chart_name,
            "discovered_domains": domains,
            "has_extras": os.path.isdir(os.path.join(repo_dir, "extras")),
            "has_values": os.path.isdir(os.path.join(repo_dir, "values")),
        }
        if args.json:
            print(json.dumps(output, indent=2))
        else:
            print(f"Chart Name: {chart_name}")
            print(f"Domains: {domains}")
        sys.exit(0)

    parser.print_help()


if __name__ == "__main__":
    main()
