"""The skills' entry files carry no library release: they point at the libraries' docs.

A version number, a claim about what the "current" library does or a `main` default in an
entry file is library information the skill would have to re-release whenever a library
does. The libraries' README and MIGRATION.md own it; the skills read them at the release a
project pins.
"""
import re
import unittest
from pathlib import Path

_REPO = Path(__file__).resolve().parents[4]
_ENTRY_FILES = sorted(
    p for pattern in ("README.md", "COMPATIBILITY.md",
                      "platform-builder/*.md", "platform-builder/aliases/*/SKILL.md",
                      "platform-builder/core/references/*.md",
                      "platform-builder/gitops/*/references/*.md",
                      "platform-builder/gitops/*/workflows/*.md",
                      "k8s-upgrade/*.md", "k8s-upgrade/core/references/*.md",
                      "k8s-upgrade/providers/*/README.md", "k8s-upgrade/providers/*/references/*.md",
                      "terraform-module-builder/*.md", "terraform-module-consumer/*.md")
    for p in _REPO.glob(pattern))

_VERSION = re.compile(r"(?<![\w.])v?\d+\.\d+\.\d+(?![\w.])")
_CURRENT = re.compile(r"\bcurrent (github|gitlab|helm|argocd|terraform)[\w-]*", re.I)
_MAIN_DEFAULT = re.compile(r"at `main`")
#: terraform-module-builder develops the library itself, so its default is the library's main.
_MAIN_ALLOWED = {"terraform-module-builder/SKILL.md"}


class EntryFileTests(unittest.TestCase):
    def test_entry_files_were_found(self):
        self.assertGreater(len(_ENTRY_FILES), 10)

    def test_no_library_release_in_entry_files(self):
        for path in _ENTRY_FILES:
            rel = path.relative_to(_REPO).as_posix()
            for no, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
                with self.subTest(file=rel, line=no):
                    self.assertIsNone(_VERSION.search(line), f"{rel}:{no} names a version: {line.strip()}")
                    self.assertIsNone(_CURRENT.search(line), f"{rel}:{no} describes a library release: {line.strip()}")
                    if rel not in _MAIN_ALLOWED:
                        self.assertIsNone(_MAIN_DEFAULT.search(line), f"{rel}:{no} defaults to main: {line.strip()}")


if __name__ == "__main__":
    unittest.main()
