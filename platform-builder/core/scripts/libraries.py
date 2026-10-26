#!/usr/bin/env python3
"""Resolve the shared CI/Helm libraries a run reads its facts from.

The skill carries no copy of what `gitlab-ci-library`, `github-ci-library` and
`helm-tpl-library` do -- it starts from each library's README index, follows task-specific
topic links, reads MIGRATION.md when comparing versions, and its checks read the data file
each library ships for them. It must know WHICH copy to read. This module answers that, and
nothing else: it does not interpret the libraries, it locates them.

A source is written as ONE string, `<location>[@<ref>]`:

    /Users/me/library/gitlab-ci-library         local working copy, used in place
    ~/library/gitlab-ci-library                 ~ expanded
    ../gitlab-ci-library                        relative to the target repo
    file:///srv/mirror/gitlab-ci-library        local, explicit scheme
    https://github.com/grootan-devops/gitlab-ci-library         git, default branch
    https://github.com/grootan-devops/gitlab-ci-library@1.0.0   git, tag
    https://github.com/grootan-devops/gitlab-ci-library@main    git, branch
    https://github.com/.../gitlab-ci-library/tree/6de3e62       git, web URL at a commit
    git@github.com:grootan-devops/gitlab-ci-library.git@1.0.0  scp-style + tag

`@` is the separator, and only the LAST `@` that falls after the last `/` counts -- so
the user@host of an scp-style URL is not mistaken for a ref. `#` is accepted as an
alternative separator for callers that find `@` ambiguous.

A local source is READ IN PLACE and never copied, so an uncommitted working copy is
visible to the run -- that is the point of pointing at one. A git source is cloned into
a content-addressed cache and checked out at the ref; the same URL+ref is cloned once.

Precedence, highest first. Each layer names a source per library, and a library not
named falls through to the next layer:

    1. --lib NAME=SOURCE           this invocation
    2. $PLATFORM_BUILDER_LIB_<NAME>  environment (NAME upper-cased, '-' -> '_')
    3. <repo>/.platform-builder.json  the target repo's own source, committed with it
    4. the release the repo pins     its CI includes / reusable workflows, its chart dependency
    5. core/libraries.json            the skill default: the library's latest release

The pin is what the project runs today, so its docs and data describe the project. A new
project, and an upgrade (`--latest`, which skips layer 4), read the latest release: the ref
`latest` is resolved to the highest release tag with `git ls-remote`, never to a branch.

A library whose checks need a data file (`requires` in core/libraries.json) is unusable at
a release that does not ship it, or ships it with a `schema` this skill does not know. The
run stops there and says what to upgrade, rather than measuring a project against rules the
skill would have to guess.

Usage:
    python3 libraries.py [repo] [--platform gitlab|github] [--latest]
                         [--lib NAME=SOURCE ...] [--all] [--refresh] [--json]
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Dict, List, Optional, Tuple

_HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(_HERE))

import pins  # noqa: E402

_ROOT = _HERE.parent.parent
DEFAULTS_FILE = _ROOT / "core" / "libraries.json"
REPO_CONFIG_NAME = ".platform-builder.json"
ENV_PREFIX = "PLATFORM_BUILDER_LIB_"

#: The ref that means "the highest release tag", resolved before anything is cloned.
LATEST = "latest"

#: `schema` versions of the library data files this skill reads. A file with another schema
#: describes rules this skill does not know; reading it anyway would misjudge the project.
SUPPORTED_SCHEMAS = (1,)

#: Seconds to wait for `git ls-remote`; it lists refs, so anything slower is a network problem.
LS_REMOTE_TIMEOUT = 20

#: Documentation entrypoints, not a list of files to preload. Follow README links only
#: for the task at hand, within the same resolved checkout; legacy READMEs may be monolithic.
#: MIGRATION.md is optional and is read for upgrades or compatibility checks.
REQUIRED_DOCS = ("README.md",)
OPTIONAL_DOCS = ("MIGRATION.md",)

_SHA_RE = re.compile(r"^[0-9a-f]{7,40}$")

#: `https://github.com/org/repo/tree/<ref>` and the GitLab `/-/tree/<ref>` form.
#: This is what a browser address bar gives you, so it is what people paste.
#: The ref runs to the end of the path: a branch may contain slashes.
_WEB_TREE_RE = re.compile(r"^(?P<loc>https?://\S+?)/(?:-/)?tree/(?P<ref>[^\s?#]+?)/?$")


def cache_root() -> Path:
    """Where git sources are materialised. Override with $PLATFORM_BUILDER_CACHE."""
    env = os.environ.get("PLATFORM_BUILDER_CACHE")
    if env:
        return Path(env).expanduser()
    base = os.environ.get("XDG_CACHE_HOME") or (Path.home() / ".cache")
    return Path(base).expanduser() / "platform-builder" / "libraries"


# --------------------------------------------------------------------------------------
# Spec parsing
# --------------------------------------------------------------------------------------

@dataclass
class Source:
    """A parsed library source, before resolution."""
    raw: str
    location: str
    ref: Optional[str] = None
    kind: str = "local"          # "local" | "git"

    @property
    def ref_kind(self) -> str:
        """What the ref looks like. Advisory only -- git is the authority."""
        if not self.ref:
            return "default-branch"
        return "commit" if _SHA_RE.match(self.ref) else "branch-or-tag"


def _split_ref(spec: str) -> tuple[str, Optional[str]]:
    """Split `<location>@<ref>` or `<location>#<ref>`.

    Only a separator AFTER the last '/' can introduce a ref. Without that rule the
    `git@` of an scp-style URL, and the `user@host` of an https URL carrying
    credentials, both parse as refs -- and the failure is silent: you get a clone of
    the wrong thing rather than an error.
    """
    if "#" in spec:
        head, _, tail = spec.rpartition("#")
        if head and tail:
            return head, tail

    # `<url>.git@<ref>` is unambiguous even when the ref contains a slash, which the
    # last-'/' rule below cannot see. Covers scp-style too: the `.git@` is the last one.
    if ".git@" in spec:
        head, _, tail = spec.partition(".git@")
        if head and tail:
            return head + ".git", tail

    cut = spec.rfind("/")
    at = spec.rfind("@")
    if at > cut and at > 0:
        return spec[:at], spec[at + 1:]
    return spec, None


def parse_source(spec: str) -> Source:
    spec = spec.strip()
    if not spec:
        raise ValueError("empty library source")

    web = _WEB_TREE_RE.match(spec)
    if web:
        return Source(spec, web.group("loc"), web.group("ref"), "git")

    location, ref = _split_ref(spec)

    if location.startswith("file://"):
        return Source(spec, location[len("file://"):], ref, "local")

    is_git = (
        location.startswith(("http://", "https://", "ssh://", "git://"))
        or re.match(r"^[^/]+@[^/]+:", location) is not None   # scp-style
    )
    if is_git:
        return Source(spec, location, ref, "git")

    return Source(spec, location, ref, "local")


# --------------------------------------------------------------------------------------
# Layered configuration
# --------------------------------------------------------------------------------------

def _read_json(path: Path) -> dict:
    if not path.is_file():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise SystemExit(f"ERROR: {path} is not valid JSON: {exc}")


def load_defaults() -> dict:
    data = _read_json(DEFAULTS_FILE)
    return data.get("libraries", {}) if isinstance(data, dict) else {}


def chart_dependencies() -> Dict[str, str]:
    """{library name: the chart name it is published as}, for libraries a chart depends on."""
    return {n: c["chart"] for n, c in load_defaults().items() if isinstance(c, dict) and c.get("chart")}


def _skills_version() -> Optional[str]:
    """The release of these skills, from the repository's VERSION file when it is installed."""
    version = _ROOT.parent / "VERSION"
    return version.read_text(encoding="utf-8").strip() if version.is_file() else None


def skills_source() -> str:
    data = _read_json(DEFAULTS_FILE)
    return str((data.get("skills") or {}).get("source") or "") if isinstance(data, dict) else ""


def platform_libraries(platform: Optional[str]) -> List[str]:
    """Which libraries a run on this platform actually reads."""
    data = _read_json(DEFAULTS_FILE)
    mapping = data.get("platforms", {}) if isinstance(data, dict) else {}
    if platform and platform in mapping:
        return list(mapping[platform])
    names: List[str] = []
    for group in mapping.values():
        for n in group:
            if n not in names:
                names.append(n)
    return names


def _spec_of(entry) -> Optional[str]:
    """Accept either a bare string or {"source": ..., "ref": ...}."""
    if isinstance(entry, str):
        return entry or None
    if isinstance(entry, dict):
        src = entry.get("source") or entry.get("url") or entry.get("path")
        if not src:
            return None
        ref = entry.get("ref") or entry.get("branch") or entry.get("tag") or entry.get("commit")
        return f"{src}@{ref}" if ref else str(src)
    return None


def resolve_spec(name: str, repo: Optional[Path], cli: Dict[str, str],
                 pinned: Optional[str] = None, latest: bool = False) -> tuple[Optional[str], str]:
    """Return (spec, origin-of-that-spec) for one library, highest precedence first.

    `pinned` is the release the repository itself pins (layer 4). `latest` skips it, and
    moves a ref named in .platform-builder.json to the latest release while keeping its
    location (a mirror stays a mirror).
    """
    if name in cli:
        return cli[name], "command line"

    env_key = ENV_PREFIX + name.upper().replace("-", "_")
    if os.environ.get(env_key):
        return os.environ[env_key], f"${env_key}"

    if repo:
        cfg = _read_json(repo / REPO_CONFIG_NAME).get("libraries", {})
        spec = _spec_of(cfg.get(name)) if isinstance(cfg, dict) else None
        if spec:
            if latest:
                src = parse_source(spec)
                if src.kind == "git":
                    return f"{src.location}@{LATEST}", f"{REPO_CONFIG_NAME} (location), --latest"
            return spec, f"{REPO_CONFIG_NAME}"

    default = load_defaults().get(name)
    location = parse_source(_spec_of(default)).location if _spec_of(default) else None
    if pinned and location and not latest:
        return f"{location}@{pinned}", "project pin"

    spec = _spec_of(default)
    if spec:
        return spec, "core/libraries.json (default)"

    return None, "unset"


# --------------------------------------------------------------------------------------
# Resolution
# --------------------------------------------------------------------------------------

@dataclass
class Resolved:
    name: str
    spec: str
    origin: str                  # which precedence layer supplied the spec
    kind: str                    # "local" | "git"
    path: Optional[str] = None
    ref: Optional[str] = None
    ref_kind: str = "default-branch"
    commit: Optional[str] = None
    dirty: bool = False          # local copy with uncommitted changes
    docs: Dict[str, str] = field(default_factory=dict)
    data: Dict[str, str] = field(default_factory=dict)   # data files the skill's checks read
    requested_ref: Optional[str] = None   # what was asked for when it differs: "latest"
    release_tags: List[str] = field(default_factory=list)   # oldest first
    latest_release: Optional[str] = None
    notes: List[str] = field(default_factory=list)
    error: Optional[str] = None

    @property
    def ok(self) -> bool:
        return self.error is None


def _git(args: List[str], cwd: Optional[Path] = None, timeout: int = 180) -> subprocess.CompletedProcess:
    return subprocess.run(["git", *args], cwd=str(cwd) if cwd else None,
                          capture_output=True, text=True, timeout=timeout)


def _describe_local(path: Path, res: Resolved) -> None:
    head = _git(["-C", str(path), "rev-parse", "HEAD"])
    if head.returncode == 0:
        res.commit = head.stdout.strip()
    name = _git(["-C", str(path), "rev-parse", "--abbrev-ref", "HEAD"])
    if name.returncode == 0 and res.ref is None:
        res.ref = name.stdout.strip()
        if res.ref == "HEAD":           # detached: name the release tag it sits on, if any
            tag = _git(["-C", str(path), "describe", "--tags", "--exact-match", "HEAD"])
            res.ref = tag.stdout.strip() if tag.returncode == 0 and tag.stdout.strip() else res.ref
        # Not a ref the caller asked for -- it is whatever the tree happens to be on.
        res.ref_kind = "checked-out"
    status = _git(["-C", str(path), "status", "--porcelain"])
    res.dirty = status.returncode == 0 and bool(status.stdout.strip())


_REMOTE_TAGS: Dict[str, Optional[List[str]]] = {}


def remote_release_tags(location: str) -> Optional[List[str]]:
    """Release tags of a git remote, oldest first; None when the remote cannot be listed.

    One `ls-remote` answers both "what is the latest release" and "which releases lie between
    a pin and the target" -- a shallow clone holds only the tags on its own tip, so asking the
    clone silently drops every release in between.
    """
    if location not in _REMOTE_TAGS:
        try:
            out = _git(["ls-remote", "--tags", "--refs", location], timeout=LS_REMOTE_TIMEOUT)
        except subprocess.TimeoutExpired:
            out = None
        if out is None or out.returncode != 0:
            _REMOTE_TAGS[location] = None
        else:
            tags = [ln.split("refs/tags/", 1)[1] for ln in out.stdout.splitlines() if "refs/tags/" in ln]
            _REMOTE_TAGS[location] = sorted((t for t in tags if pins.release_key(t)), key=pins.release_key)
    return _REMOTE_TAGS[location]


def _cached_latest(location: str) -> Optional[str]:
    """The highest release a cached clone of `location` is checked out at, for offline runs."""
    root = cache_root()
    best: Optional[str] = None
    for d in (root.glob("*") if root.is_dir() else []):
        url = _git(["-C", str(d), "remote", "get-url", "origin"])
        if url.returncode != 0 or url.stdout.strip() != location:
            continue
        tag = _git(["-C", str(d), "describe", "--tags", "--exact-match", "HEAD"])
        name = tag.stdout.strip() if tag.returncode == 0 else ""
        if pins.release_key(name) and (best is None or pins.release_key(name) > pins.release_key(best)):
            best = name
    return best


def _local_release_tags(path: Path, res: "Resolved") -> List[str]:
    out = _git(["-C", str(path), "tag", "--list"])
    tags = sorted((t for t in out.stdout.split() if pins.release_key(t)), key=pins.release_key) \
        if out.returncode == 0 else []
    shallow = _git(["-C", str(path), "rev-parse", "--is-shallow-repository"])
    if shallow.returncode == 0 and shallow.stdout.strip() == "true":
        res.notes.append("shallow clone: its tag list, and so the migration chain, may be incomplete")
    return tags


def _cache_dir(src: Source) -> Path:
    digest = hashlib.sha1(f"{src.location}@{src.ref or ''}".encode()).hexdigest()[:12]
    slug = re.sub(r"[^A-Za-z0-9._-]", "-", src.location.rstrip("/").split("/")[-1]) or "lib"
    slug = slug[:-4] if slug.endswith(".git") else slug
    return cache_root() / f"{slug}-{digest}"


def _checkout_ref(dest: Path, ref: str) -> bool:
    """Land `dest` exactly on `ref`, whatever kind of ref it is.

    A branch, a tag and a commit each need a different command, and using the wrong
    one fails quietly: `reset --hard origin/<tag>` has no remote-tracking ref to
    resolve, so a cached tag silently stayed wherever it already was.
    """
    branch = _git(["-C", str(dest), "rev-parse", "--verify", "--quiet",
                   f"refs/remotes/origin/{ref}"])
    if branch.returncode == 0:
        return _git(["-C", str(dest), "reset", "--hard", f"origin/{ref}"]).returncode == 0

    tag = _git(["-C", str(dest), "rev-parse", "--verify", "--quiet", f"refs/tags/{ref}"])
    if tag.returncode == 0:
        return _git(["-C", str(dest), "checkout", "--force",
                     f"refs/tags/{ref}"]).returncode == 0

    return _git(["-C", str(dest), "checkout", "--force", ref]).returncode == 0


def _clone(src: Source, dest: Path, refresh: bool) -> None:
    """Materialise a git source at `dest`, checked out at `src.ref`.

    Only a full commit SHA is immutable enough to serve from cache untouched. A branch
    moves; a tag can be force-pushed; and a short hex ref may be a branch that merely
    looks like a SHA. Everything else is re-fetched. `--refresh` re-clones either way.
    """
    if dest.exists() and refresh:
        shutil.rmtree(dest)

    if dest.exists():
        immutable = bool(src.ref and len(src.ref) == 40 and _SHA_RE.match(src.ref))
        if immutable:
            return
        fetch = _git(["-C", str(dest), "fetch", "--tags", "--force", "origin"])
        if fetch.returncode != 0:
            return                      # offline: fall back on what is cached
        target = src.ref or _default_branch(dest)
        if not _checkout_ref(dest, target):
            raise RuntimeError(f"ref '{target}' could not be checked out in the cached "
                               f"copy of {src.location}; re-run with --refresh")
        return

    dest.parent.mkdir(parents=True, exist_ok=True)

    if src.ref and src.ref_kind != "commit":
        # A branch or tag can be fetched directly and shallowly.
        out = _git(["clone", "--depth", "1", "--branch", src.ref, src.location, str(dest)])
        if out.returncode == 0:
            return
        shutil.rmtree(dest, ignore_errors=True)   # fall through to the full clone

    out = _git(["clone", src.location, str(dest)])
    if out.returncode != 0:
        shutil.rmtree(dest, ignore_errors=True)
        raise RuntimeError(f"git clone failed: {out.stderr.strip().splitlines()[-1] if out.stderr.strip() else 'unknown error'}")

    if src.ref:
        co = _git(["-C", str(dest), "checkout", "--force", src.ref])
        if co.returncode != 0:
            shutil.rmtree(dest, ignore_errors=True)
            raise RuntimeError(f"ref '{src.ref}' not found in {src.location}")


def _default_branch(path: Path) -> str:
    out = _git(["-C", str(path), "symbolic-ref", "--short", "refs/remotes/origin/HEAD"])
    if out.returncode == 0:
        return out.stdout.strip().removeprefix("origin/")
    return "main"


def resolve_one(name: str, spec: str, origin: str, *,
                repo: Optional[Path] = None, refresh: bool = False) -> Resolved:
    try:
        src = parse_source(spec)
    except ValueError as exc:
        return Resolved(name, spec, origin, "local", error=str(exc))

    res = Resolved(name, spec, origin, src.kind, ref=src.ref, ref_kind=src.ref_kind)

    if src.kind == "git" and src.ref == LATEST:
        tags = remote_release_tags(src.location)
        res.requested_ref = LATEST
        if tags is None:
            cached = _cached_latest(src.location)
            if not cached:
                res.error = (f"cannot list the releases of {src.location} (offline?) and no cached "
                             f"release exists: pass a release explicitly, e.g. {src.location}@<tag>")
                return res
            res.notes.append(f"offline: using the cached release {cached}, which may not be the latest")
            src.ref = cached
        elif not tags:
            res.error = f"{src.location} has no release tag: pass a branch or tag explicitly"
            return res
        else:
            src.ref = tags[-1]
        src.raw = f"{src.location}@{src.ref}"
        res.ref, res.ref_kind = src.ref, src.ref_kind

    if src.kind == "local":
        base = repo if repo else Path.cwd()
        path = Path(os.path.expanduser(src.location))
        if not path.is_absolute():
            path = (base / path)
        path = path.resolve()
        if not path.is_dir():
            res.error = f"local path does not exist: {path}"
            return res
        res.path = str(path)
        if src.ref:
            # A ref against a working copy would mean checking it out -- that mutates
            # the user's tree. Refuse rather than silently ignore or silently switch.
            res.error = (f"ref '{src.ref}' given for a LOCAL path; a local copy is read at "
                         f"whatever it is checked out to. Drop the ref, or point at the git URL.")
            return res
        _describe_local(path, res)
    else:
        dest = _cache_dir(src)
        try:
            _clone(src, dest, refresh)
        except (RuntimeError, subprocess.TimeoutExpired) as exc:
            res.error = str(exc)
            return res
        res.path = str(dest)
        head = _git(["-C", str(dest), "rev-parse", "HEAD"])
        if head.returncode == 0:
            res.commit = head.stdout.strip()
        if not src.ref:
            res.ref = _default_branch(dest)

    if src.kind == "git":
        tags = remote_release_tags(src.location)
        res.release_tags = tags if tags is not None else _local_release_tags(Path(res.path), res)
    else:
        res.release_tags = _local_release_tags(Path(res.path), res)
    res.latest_release = res.release_tags[-1] if res.release_tags else None
    if pins.release_key(res.ref) and res.latest_release \
            and pins.release_key(res.ref) < pins.release_key(res.latest_release):
        res.notes.append(f"behind: {res.ref} -> {res.latest_release} (platform update moves the project)")

    for doc in REQUIRED_DOCS + OPTIONAL_DOCS:
        p = Path(res.path) / doc
        if p.is_file():
            res.docs[doc] = str(p)
    missing = [d for d in REQUIRED_DOCS if d not in res.docs]
    if missing and not res.error:
        res.error = (f"missing {', '.join(missing)} at {res.path} -- a library without a "
                     f"README.md has no contract to read, so this is not a usable source")
    res.notes += [f"no {d} at this ref" for d in OPTIONAL_DOCS if d not in res.docs]
    if not res.error:
        res.error = _check_data(name, res)
    return res


def _check_data(name: str, res: Resolved) -> Optional[str]:
    """Why this release cannot serve the skill's checks, or None when it can."""
    cfg = load_defaults().get(name)
    for rel in (cfg.get("requires") or []) if isinstance(cfg, dict) else []:
        path = Path(res.path) / rel
        at = f"{name} {res.ref or 'at ' + str(res.path)}"
        if not path.is_file():
            if res.latest_release and res.latest_release == res.ref:
                return (f"{at} ships no {rel}, which this skill's checks read, and it is the "
                        f"library's latest release: no release ships the file yet. Keep the skills "
                        f"at the release you used before until one does.")
            upgrade = f" to {res.latest_release}" if res.latest_release else ""
            return (f"{at} ships no {rel}, which this skill's checks read: it needs a release that "
                    f"ships it. Upgrade the project{upgrade} with `platform update` (it reads "
                    f"--latest), or pass a newer source explicitly.")
        try:
            schema = json.loads(path.read_text(encoding="utf-8")).get("schema")
        except (ValueError, AttributeError, OSError) as exc:
            return f"{at}: {rel} is not a readable JSON object ({exc})"
        if schema not in SUPPORTED_SCHEMAS:
            tags = remote_release_tags(skills_source()) if skills_source() else None
            newest, own = (tags[-1] if tags else None), _skills_version()
            known = f"{at}: {rel} has schema {schema!r}, which this skill does not know " \
                    f"(it reads {', '.join(map(str, SUPPORTED_SCHEMAS))})."
            if newest and own and pins.release_key(own) and pins.release_key(newest) <= pins.release_key(own):
                return f"{known} No skills release reads it yet ({own} is the latest)."
            return f"{known} Upgrade the skills to the latest release{f' ({newest})' if newest else ''}."
        res.data[rel] = str(path)
    return None


def project_pins(repo: Optional[Path], platform: Optional[str]) -> Tuple[Dict[str, List[str]], Optional[Path]]:
    """({library: [refs the repository pins]}, its chart dir) -- empty without a repository."""
    if not repo:
        return {}, None
    return pins.repo_pins(repo, platform, chart_dependencies())


def resolve_all(repo: Optional[Path], platform: Optional[str], cli: Dict[str, str],
                names: Optional[List[str]] = None, refresh: bool = False,
                latest: bool = False,
                pinned: Optional[Dict[str, List[str]]] = None,
                gate_charts: bool = True) -> List[Resolved]:
    """Resolve the libraries a run reads.

    A library published as a chart dependency is read only when the repository has a chart
    (unless `gate_charts` is off): a repository without one has nothing to measure against
    it, so an old release of it must not stop the run.
    """
    if pinned is None:
        pinned, cdir = project_pins(repo, platform)
    else:
        cdir = pins.chart_dir(repo) if repo else None
    has_chart = bool(cdir and (cdir / "Chart.yaml").exists())
    wanted = list(names or platform_libraries(platform))
    if gate_charts:
        wanted = [n for n in wanted if has_chart or n not in chart_dependencies()]
    for n in cli:                      # an explicitly passed library is always resolved
        if n not in wanted:
            wanted.append(n)

    out: List[Resolved] = []
    for name in wanted:
        refs = pinned.get(name) or []
        spec, origin = resolve_spec(name, repo, cli, pinned=pins.pick(refs), latest=latest)
        if origin == "project pin" and len(refs) > 1:
            origin = f"project pin, the oldest of {', '.join(refs)}"
        if not spec:
            out.append(Resolved(name, "", origin, "local",
                                error="no source configured -- pass --lib "
                                      f"{name}=<path-or-git-url>, set "
                                      f"${ENV_PREFIX}{name.upper().replace('-', '_')}, or add a "
                                      f"default to core/libraries.json"))
            continue
        res = resolve_one(name, spec, origin, repo=repo, refresh=refresh)
        pin = pins.pick(refs)
        if pin and origin.startswith(REPO_CONFIG_NAME) and parse_source(spec).ref not in (None, pin):
            res.notes.append(f"{REPO_CONFIG_NAME} reads {parse_source(spec).ref}, but the project pins {pin}")
        out.append(res)
    return out


# --------------------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------------------

def _parse_cli_libs(pairs: List[str]) -> Dict[str, str]:
    out: Dict[str, str] = {}
    for pair in pairs or []:
        if "=" not in pair:
            raise SystemExit(f"ERROR: --lib expects NAME=SOURCE, got '{pair}'")
        name, _, spec = pair.partition("=")
        name, spec = name.strip(), spec.strip()
        if not name or not spec:
            raise SystemExit(f"ERROR: --lib expects NAME=SOURCE, got '{pair}'")
        out[name] = spec
    return out


# --------------------------------------------------------------------------------------
# Migration chain
# --------------------------------------------------------------------------------------

def _semver_key(tag: Optional[str]):
    """Sort key for a release tag, or None if it is not one (a pre-release is not)."""
    return pins.release_key(tag)


def list_release_tags(path: Path) -> List[str]:
    """Release tags in a library checkout, oldest first. Other tags are ignored."""
    out = _git(["-C", str(path), "tag", "--list"])
    if out.returncode != 0:
        return []
    tagged = [(k, t) for t in out.stdout.split() if (k := _semver_key(t)) is not None]
    return [t for _, t in sorted(tagged)]


def versions_between(path: Path, current: Optional[str], target: Optional[str],
                     tags: Optional[List[str]] = None) -> List[str]:
    """Release tags a consumer on `current` has not yet adopted, up to `target`.

    Ordered oldest first, `current` exclusive and `target` inclusive. Skipping the
    intermediate releases is the whole failure this exists to prevent: 1.0.0 -> 2.0.0
    must still apply 1.1.0 and 1.2.0.

    An untagged library yields an empty chain: there are no releases to migrate across.
    An unparseable `current` -- a branch, a SHA, or nothing at all -- yields the FULL
    chain rather than an empty one, because "we cannot tell what you have applied" must
    not read as "you are up to date". The caller says which case it is.
    """
    tags = tags if tags is not None else list_release_tags(path)
    if not tags:
        return []
    cur = _semver_key(current) if current else None
    tgt = _semver_key(target) if target else None
    if tgt is None:
        tgt = _semver_key(tags[-1])
    return [t for t in tags
            if (cur is None or _semver_key(t) > cur) and _semver_key(t) <= tgt]


def migration_sections(migration_md: Path, versions: List[str]) -> List[tuple]:
    """(version, body) for each requested version, in the order given.

    A version with no section in MIGRATION.md yields an empty body rather than being
    dropped: "released, nothing to do" and "released, undocumented" look identical to
    a consumer otherwise.
    """
    if not versions or not migration_md.is_file():
        return [(v, "") for v in versions]
    try:
        lines = migration_md.read_text(encoding="utf-8").splitlines()
    except OSError:
        return [(v, "") for v in versions]

    bodies: Dict[str, List[str]] = {}
    current: Optional[str] = None
    for line in lines:
        if line.startswith("## "):
            head = line[3:].strip()
            current = head.lstrip("v") if _semver_key(head) else None
            if current:
                bodies[current] = []
            continue
        if current is not None:
            bodies[current].append(line)
    return [(v, "\n".join(bodies.get(v.lstrip("v"), [])).strip()) for v in versions]


def migration_chain(res: "Resolved", current_ref: Optional[str]) -> List[tuple]:
    """Ordered (version, body) a consumer pinned at `current_ref` still owes."""
    if not res.path:
        return []
    versions = versions_between(Path(res.path), current_ref, res.ref, res.release_tags or None)
    return migration_sections(Path(res.path) / "MIGRATION.md", versions)


def known_library_names() -> List[str]:
    """Every library the skill knows about, in declaration order."""
    return list(load_defaults().keys())


def add_library_args(ap) -> None:
    """Register `--lib NAME=SOURCE` plus one named flag per known library.

    `--github-ci-library <url>` reads better at a terminal than `--lib name=<url>`,
    and it is the form the runbook documents. Both end up in the same override map.
    """
    ap.add_argument("--lib", action="append", metavar="NAME=SOURCE", default=[],
                    help="override one library; repeatable")
    for name in known_library_names():
        ap.add_argument(f"--{name}", metavar="SOURCE", default=None,
                        help=f"source for {name}: local path, git URL with an optional "
                             f"@ref, or a <repo>/tree/<ref> web URL")


def cli_lib_overrides(args) -> Dict[str, str]:
    """Merge the named flags and `--lib` into one override map. Named flags win."""
    out = _parse_cli_libs(getattr(args, "lib", []) or [])
    for name in known_library_names():
        val = getattr(args, name.replace("-", "_"), None)
        if val:
            out[name] = val.strip()
    return out


def render(results: List[Resolved]) -> str:
    lines = ["=" * 78, "Library sources", "=" * 78]
    for r in results:
        lines.append(f"\n{r.name}")
        lines.append(f"  source   : {r.spec or '(none)'}   [via {r.origin}]")
        if r.error:
            lines.append(f"  STATUS   : UNUSABLE -- {r.error}")
            continue
        lines.append(f"  kind     : {r.kind}")
        lines.append(f"  path     : {r.path}")
        if r.ref:
            lines.append(f"  ref      : {r.ref} ({r.ref_kind})")
        if r.commit:
            lines.append(f"  commit   : {r.commit[:12]}")
        if r.dirty:
            lines.append("  WARNING  : working copy has uncommitted changes -- this run reads "
                         "them, a pipeline using the published library will not")
        if r.requested_ref:
            lines.append(f"  requested: {r.requested_ref}")
        lines.append(f"  index    : {r.docs['README.md']}")
        lines.append("  follow   : task-specific README links, relative to each page at this same ref")
        if "MIGRATION.md" in r.docs:
            lines.append(f"  migration: {r.docs['MIGRATION.md']} (upgrades/compatibility only)")
        for rel, path in r.data.items():
            lines.append(f"  data     : {path} (read by the checks, not a page to load)")
        for n in r.notes:
            lines.append(f"  note     : {n}")
    bad = [r for r in results if not r.ok]
    lines.append("\n" + "-" * 78)
    lines.append(f"{len(results) - len(bad)} usable, {len(bad)} unusable")
    if bad:
        lines.append(">> Do not scaffold against an unusable source. Fix it or ask the user.")
    return "\n".join(lines)


def main() -> None:
    ap = argparse.ArgumentParser(
        description="Resolve shared CI/Helm library sources (local path or git ref).")
    ap.add_argument("repo_path", nargs="?", default=".",
                    help="target repo; relative local sources resolve against it")
    ap.add_argument("--platform", choices=["gitlab", "github"],
                    help="only resolve the libraries this platform reads")
    add_library_args(ap)
    ap.add_argument("--latest", action="store_true",
                    help="read every library at its latest release instead of the project's pin "
                         "(an upgrade, `platform update`)")
    ap.add_argument("--all", action="store_true", help="resolve every known library")
    ap.add_argument("--refresh", action="store_true", help="re-fetch git sources, ignoring cache")
    ap.add_argument("--json", action="store_true", help="machine-readable output")
    args = ap.parse_args()

    repo = Path(args.repo_path).resolve()
    results = resolve_all(repo if repo.is_dir() else None,
                          None if args.all else args.platform,
                          cli_lib_overrides(args), refresh=args.refresh, latest=args.latest,
                          names=known_library_names() if args.all else None,
                          gate_charts=not args.all)

    if args.json:
        print(json.dumps({"repository": str(repo),
                          "platform": args.platform,
                          "cache": str(cache_root()),
                          "libraries": [asdict(r) for r in results]}, indent=2))
    else:
        print(render(results))

    sys.exit(1 if any(not r.ok for r in results) else 0)


if __name__ == "__main__":
    main()
