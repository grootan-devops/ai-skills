#!/usr/bin/env python3
"""The Argo CD backend's library: argocd-gitops-tpl-library and its environment map.

An environment's root chart and its extras/ chart depend on argocd-gitops-tpl-library at one
release. docs/environment-map.json inside that package says how the library reads an `apps`
entry and what an environment values file may override; the generic loading and schema gate
are in core/scripts/gitops_library.py.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any, Dict

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "core" / "scripts"))

import gitops_library as library  # noqa: E402

GITOPS_LIBRARY = "argocd-gitops-tpl-library"
ENVIRONMENT_MAP = "docs/environment-map.json"
library.SOURCES[GITOPS_LIBRARY] = "https://github.com/grootan-devops/argocd-gitops-tpl-library"


def environment_map(root: Path) -> Dict[str, Any]:
    """The environment map of the GitOps library release the root and extras charts pin."""
    pin = library.dependency_version(root, GITOPS_LIBRARY)
    if pin is None:
        raise SystemExit(f"{root}/Chart.yaml does not depend on {GITOPS_LIBRARY}")
    extras = library.dependency_version(root / "extras", GITOPS_LIBRARY) if (root / "extras" / "Chart.yaml").exists() else None
    if extras is not None and extras != pin:
        raise SystemExit(f"the root chart pins {GITOPS_LIBRARY} {pin} but extras/ pins {extras}: "
                         "both charts must pin the same release")
    return library.load_data(library.vendored(root, GITOPS_LIBRARY), ENVIRONMENT_MAP, GITOPS_LIBRARY)
