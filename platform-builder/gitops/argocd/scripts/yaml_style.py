#!/usr/bin/env python3
"""Write YAML in the layout the branch's own values files already use.

A file the skill adds next to hand-written ones should read like them: the same indent, lists
indented under their key or not, blank lines between top-level keys and between nested blocks,
and the same quote character. `learn` reads that layout from sample files, deciding each fact
by majority among the lines that show it; `dump` writes data in it. Blank lines between nested
blocks are decided per depth: files commonly separate `database.redis` from
`database.postgresql` and `containers.main.image` from `resources`, but not `requests` from
`limits`. With no samples, the layout is the one these GitOps repositories use: two spaces,
indented lists, a blank line between top-level keys and between blocks one and two levels
deep, double quotes, quoted `{{ … }}` templates.

    python3 yaml_style.py <sample.yaml> ...     # print the layout the samples use
"""

from __future__ import annotations

import re
import sys
from collections import Counter
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

try:
    import yaml
except ImportError:  # pragma: no cover
    raise SystemExit("PyYAML is required: pip install pyyaml")

_KEY = re.compile(r"^(\s*)(- )?([^\s#\-][^:#]*?|\"[^\"]*\"|'[^']*'):(?:\s+(.*?))?\s*$")
_ITEM = re.compile(r"^(\s*)- ")
_BLOCK_SCALAR = re.compile(r"^[|>][-+0-9]*$")
_COMMENT = re.compile(r"\s+#.*$")


@dataclass
class Style:
    indent: int = 2
    indent_lists: bool = True
    top_blank: bool = True
    #: level (1 = one indent deep) -> a blank line between two sibling blocks there; a level
    #: the samples never show follows the nearest shallower one
    nested_blank: Dict[int, bool] = field(default_factory=lambda: {1: True, 2: True, 3: False})
    quote: str = '"'
    quote_templates: bool = True


def _vote(counter: Counter, default: Any) -> Any:
    return counter.most_common(1)[0][0] if counter else default


def _lines(text: str) -> Iterable[Tuple[int, str, bool]]:
    """(index, line, blank-before) for content lines, block-scalar bodies left out."""
    blank, scalar_indent = False, None
    for i, line in enumerate(text.splitlines()):
        stripped = line.strip()
        indent = len(line) - len(line.lstrip())
        if scalar_indent is not None:
            if not stripped or indent > scalar_indent:
                continue
            scalar_indent = None
        if not stripped:
            blank = True
            continue
        if stripped.startswith("#") or stripped in ("---", "..."):
            continue
        yield i, line, blank
        blank = False
        m = _KEY.match(line)
        if m and _BLOCK_SCALAR.match(_COMMENT.sub("", m.group(4) or "")):
            scalar_indent = len(m.group(1)) + (2 if m.group(2) else 0)


def learn(texts: Iterable[str]) -> Style:
    indents, lists, tops, quotes, templates = (Counter() for _ in range(5))
    nested: Dict[int, Counter] = {}
    for text in texts:
        prev: Optional[Tuple[int, bool]] = None          # indent of the last key, and whether it opened a block
        siblings: dict = {}                              # indent -> the last key there opened a block
        first_top = True
        for _, line, blank in _lines(text):
            item = _ITEM.match(line)
            key = _KEY.match(line)
            value = _COMMENT.sub("", key.group(4) or "") if key else ""
            if item:
                if prev and prev[1]:
                    lists[len(item.group(1)) > prev[0]] += 1
                siblings = {k: v for k, v in siblings.items() if k <= len(item.group(1))}
            if key and not key.group(2):
                indent, block = len(key.group(1)), not value
                if prev and prev[1] and indent > prev[0]:
                    indents[indent - prev[0]] += 1
                if indent == 0:
                    if not first_top:
                        tops[blank] += 1
                    first_top = False
                elif block and siblings.get(indent):
                    nested.setdefault(indent, Counter())[blank] += 1
                siblings = {k: v for k, v in siblings.items() if k <= indent}
                siblings[indent] = block
                prev = (indent, block)
            elif item:
                prev = (len(item.group(1)), False)
            scalar = value if key else (line.strip()[2:] if item else "")
            if scalar[:1] in ('"', "'"):
                quotes[scalar[0]] += 1
            if "{{" in scalar:
                templates[scalar[:1] in ('"', "'")] += 1
    base = Style()
    indent = _vote(indents, base.indent)
    levels = {raw // indent: _vote(votes, False) for raw, votes in nested.items() if raw % indent == 0}
    return Style(indent=indent, indent_lists=_vote(lists, base.indent_lists),
                 top_blank=_vote(tops, base.top_blank), nested_blank=levels or base.nested_blank,
                 quote=_vote(quotes, base.quote), quote_templates=_vote(templates, base.quote_templates))


def _blank_at(style: Style, level: int) -> bool:
    """The samples' choice at this depth, else at the nearest shallower depth they show."""
    known = style.nested_blank
    shallower = [lv for lv in known if lv <= level]
    if shallower:
        return known[max(shallower)]
    return known[min(known)] if known else False


def _dumper(style: Style):
    class Dumper(yaml.SafeDumper):
        def increase_indent(self, flow=False, indentless=False):
            return super().increase_indent(flow, False if style.indent_lists else indentless)

    def string(dumper, value):
        if "\n" in value:
            return dumper.represent_scalar("tag:yaml.org,2002:str", value, style="|")
        plain = yaml.safe_dump(value, width=1000).strip()
        quoted = value == "" or plain[:1] in ('"', "'") or ("{{" in value and style.quote_templates)
        return dumper.represent_scalar("tag:yaml.org,2002:str", value, style=style.quote if quoted else None)

    Dumper.add_representer(str, string)
    return Dumper


def _space(text: str, style: Style) -> str:
    out: List[str] = []
    siblings: dict = {}
    first_top = True
    lines = text.splitlines()
    content = {i for i, _, _ in _lines(text)}
    for i, line in enumerate(lines):
        if i not in content:
            out.append(line)
            continue
        item, key = _ITEM.match(line), _KEY.match(line)
        if item:
            siblings = {k: v for k, v in siblings.items() if k <= len(item.group(1))}
        if key and not key.group(2):
            indent, block = len(key.group(1)), not _COMMENT.sub("", key.group(4) or "")
            if indent == 0:
                if not first_top and style.top_blank:
                    out.append("")
                first_top = False
            elif block and siblings.get(indent) and _blank_at(style, indent // style.indent):
                out.append("")
            siblings = {k: v for k, v in siblings.items() if k <= indent}
            siblings[indent] = block
        out.append(line)
    return "\n".join(out) + "\n"


def dump(data: Any, style: Optional[Style] = None) -> str:
    style = style or Style()
    text = yaml.dump(data, Dumper=_dumper(style), sort_keys=False, default_flow_style=False,
                     indent=style.indent, width=1000, allow_unicode=True)
    text = _space(text, style)
    if yaml.safe_load(text) != data:
        raise AssertionError("the styled YAML does not load back to the same data")
    return text


def main(argv: List[str]) -> int:
    style = learn(Path(p).read_text(encoding="utf-8") for p in argv)
    for key, value in asdict(style).items():
        print(f"{key:16} {value}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
