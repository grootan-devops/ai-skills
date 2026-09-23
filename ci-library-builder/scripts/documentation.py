"""Static documentation checks; this is not an agent's context-loading strategy."""
from __future__ import annotations

import html
import re
import unicodedata
from collections import Counter
from pathlib import Path
from urllib.parse import unquote, urlsplit

import yaml

REQUIRED_TOPICS = (
    "quick start", "module catalog", "key variables", "scan exit codes",
    "ignored cves", "integration examples",
)


def slug(text: str) -> str:
    """GitHub-style heading IDs, including Unicode and repeated spaces."""
    text = re.sub(r"<[^>]*>", "", html.unescape(text)).lower()
    text = re.sub(r"\[([^]]+)\]\([^)]+\)", r"\1", text)
    return "".join(c for c in text if c in "_- " or unicodedata.category(c)[0] in "LMN").replace(" ", "-")


def parse_markdown(text: str):
    """Return prose, headings/HTML anchors, fenced blocks and unclosed-fence lines."""
    prose, anchors, blocks, errors = [], set(), [], []
    seen = Counter()
    fence = None
    for number, line in enumerate(text.splitlines(), 1):
        marker = re.match(r"^\s*(`{3,}|~{3,})(.*)$", line)
        if fence:
            token, language, start, body = fence
            if marker and marker[1][0] == token[0] and len(marker[1]) >= len(token) and not marker[2].strip():
                blocks.append((language, start, "\n".join(body)))
                fence = None
            else:
                body.append(line)
            continue
        if marker:
            fence = (marker[1], marker[2].strip().split(" ")[0].lower(), number, [])
            continue
        prose.append((number, line))
        heading = re.match(r"^ {0,3}#{1,6}\s+(.+?)(?:\s+#+)?\s*$", line)
        if heading:
            base = slug(heading[1])
            anchor = base if not seen[base] else f"{base}-{seen[base]}"
            while anchor in anchors:
                seen[base] += 1
                anchor = f"{base}-{seen[base]}"
            seen[base] += 1
            anchors.add(anchor)
        anchors.update(re.findall(r'<(?:a|h[1-6])\b[^>]*(?:id|name)=["\']([^"\']+)', line))
    if fence:
        errors.append(fence[2])
    return prose, anchors, blocks, errors


def links(prose):
    """Inline and reference-style links, excluding inline code and fenced examples."""
    definitions = {}
    for number, line in prose:
        definition = re.match(r'^ {0,3}\[([^]]+)\]:\s*<?([^\s>]+)>?', line)
        if definition:
            definitions[definition[1].casefold()] = definition[2]
    for number, line in prose:
        if re.match(r'^ {0,3}\[([^]]+)\]:', line):
            continue
        line = re.sub(r'(`+).*?\1', '', line)
        for m in re.finditer(r'!?\[[^]]*\]\(\s*(<[^>]+>|[^\s)]+)(?:\s+["\'][^)]*)?\)', line):
            yield number, m[1].strip('<>')
        for m in re.finditer(r'\[([^]]+)\]\[([^]]*)\]', line):
            key = (m[2] or m[1]).casefold()
            if key in definitions:
                yield number, definitions[key]


def local_target(page: Path, destination: str):
    """Relative links stay in the selected checkout; remote URLs are never fetched."""
    url = urlsplit(destination)
    if url.scheme or url.netloc:
        return None
    target = (page.parent / unquote(url.path)).resolve() if url.path else page.resolve()
    if target.is_dir() and (target / "README.md").is_file():
        target /= "README.md"
    return target, unquote(url.fragment)


class ExampleLoader(yaml.SafeLoader):
    """GitLab's !reference is a data tag, not arbitrary Python execution."""


ExampleLoader.add_constructor("!reference", lambda loader, node: loader.construct_sequence(node))


def check_documentation(root: Path, *, contract: bool = True, github_workflows=None):
    root = root.resolve()
    findings = []
    wf_dir = root / ".github/workflows"
    if github_workflows is None:
        github_workflows = wf_dir.is_dir() and not (root / "common/.gitlab-ci.yml").is_file()
    library_names = {root.name}
    if github_workflows:
        library_names.add("github-ci-library")

    def add(severity, where, message):
        findings.append({"severity": severity, "where": where, "message": message,
                         "fix": "Update the README index or the affected documentation page."})

    required = ("README.md", "CHANGELOG.md", "MIGRATION.md") if contract else ("README.md",)
    for name in required:
        if not (root / name).is_file():
            add("P0", name, "Missing documentation entrypoint.")
    pages = [root / "README.md"] if (root / "README.md").is_file() else []
    pages += sorted((root / "docs").rglob("*.md")) if (root / "docs").is_dir() else []
    parsed = {p.resolve(): parse_markdown(p.read_text(encoding="utf-8")) for p in pages}
    graph = {p: set() for p in parsed}
    for page, (prose, _, blocks, errors) in list(parsed.items()):
        relative = page.relative_to(root).as_posix()
        for line in errors:
            add("P1", f"{relative}:{line}", "Unclosed fenced example.")
        for line, destination in links(prose):
            resolved = local_target(page, destination)
            if resolved is None:
                continue
            target, anchor = resolved
            if not target.exists():
                add("P1", f"{relative}:{line}", f"Broken local link: {destination}")
                continue
            if target in graph:
                graph[page].add(target)
            if anchor and target.suffix.lower() == ".md":
                target_doc = parsed.get(target) or parse_markdown(target.read_text(encoding="utf-8"))
                if anchor not in target_doc[1]:
                    add("P1", f"{relative}:{line}", f"Broken anchor: {destination}")
        for language, line, body in blocks:
            if language not in ("yaml", "yml"):
                continue
            try:
                list(yaml.load_all(body, Loader=ExampleLoader))
            except yaml.YAMLError as exc:
                add("P1", f"{relative}:{line}", f"Invalid YAML example: {str(exc).splitlines()[0]}")
        # Only references to THIS library are checked; consumer scenario filenames are not library files.
        text = page.read_text(encoding="utf-8")
        names = "(?:" + "|".join(re.escape(name) for name in sorted(library_names)) + ")"
        for workflow in set(re.findall(r"uses:\s*[^\s/]+/" + names + r"/\.github/workflows/([\w.-]+)@", text)):
            if not (root / ".github/workflows" / workflow).is_file():
                add("P1", relative, f"Unknown library workflow: {workflow}")

    reachable, pending = set(), [root / "README.md"]
    while pending:
        page = pending.pop()
        if page in reachable or page not in graph:
            continue
        reachable.add(page)
        pending.extend(graph[page] - reachable)
    for page in sorted(set(graph) - reachable):
        add("P1", page.relative_to(root).as_posix(), "Documentation is unreachable from README.md.")
    if contract:
        corpus = "\n".join(p.read_text(encoding="utf-8") for p in sorted(reachable))
        for topic in REQUIRED_TOPICS:
            if topic not in corpus.lower():
                add("P1", "README.md", f"No reachable '{topic}' topic.")
        # GitLab hosts its audit workflows on GitHub; those are not its public CI template API.
        if github_workflows:
            for workflow in sorted(wf_dir.glob("*.yml")):
                if workflow.name not in corpus:
                    add("P2", "README.md", f"Workflow '{workflow.name}' is undocumented.")
    return findings
