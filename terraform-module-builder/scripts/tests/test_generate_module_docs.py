"""Regression checks for safe, deterministic module README table updates."""

import importlib.util
from pathlib import Path
import tempfile
import unittest


SCRIPT = Path(__file__).resolve().parents[1] / "generate-module-docs.py"
SPEC = importlib.util.spec_from_file_location("generate_module_docs", SCRIPT)
DOCS = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(DOCS)


class ModuleDocsTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.module = Path(self.temp.name)
        (self.module / "versions.tf").write_text('''terraform {
  required_version = ">= 1.5.0"
  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = ">= 6.0.0"
    }
  }
}
''')
        (self.module / "variables.tf").write_text('''variable "settings" {
  type = object({
    enabled = bool
  })
  default = {
    enabled   = false
    mode      = null
  }
}
''')
        (self.module / "outputs.tf").write_text('''output "id" {
  description = "Resource ID."
  value       = "id"
}
''')

    def test_missing_readme_fails_without_inventing_examples(self):
        self.assertFalse(DOCS.update_readme(self.module))
        self.assertFalse((self.module / "README.md").exists())

    def test_existing_narrative_and_public_source_survive_table_refresh(self):
        source = "git::https://github.com/example/terraform-modules.git//modules/aws/demo?ref=1.0.0"
        readme = self.module / "README.md"
        readme.write_text(f'''# Demo

Verified behavior: creates a demo resource.

## Requirements & Providers

| old |

---

## Usage Examples

```hcl
module "demo" {{ source = "{source}" }}
```

## Inputs Specification

| old |

---

## Outputs Specification

| old |
''')
        self.assertTrue(DOCS.update_readme(self.module))
        updated = readme.read_text()
        self.assertIn(source, updated)
        self.assertIn("Verified behavior: creates a demo resource.", updated)
        self.assertIn("| `settings` | | `object({...})` | `{ enabled = false mode = null }` | No |",
                      updated)
        self.assertTrue(DOCS.update_readme(self.module, check_mode=True))

    def test_missing_generated_section_fails_closed(self):
        readme = self.module / "README.md"
        original = "# Demo\n\n## Requirements & Providers\n\n| old |\n\n---\n"
        readme.write_text(original)
        self.assertFalse(DOCS.update_readme(self.module))
        self.assertEqual(readme.read_text(), original)

    def test_missing_versions_does_not_invent_provider_requirements(self):
        (self.module / "versions.tf").unlink()
        readme = self.module / "README.md"
        readme.write_text("# Reviewed module\n")
        self.assertFalse(DOCS.update_readme(self.module))
        self.assertEqual(readme.read_text(), "# Reviewed module\n")


if __name__ == "__main__":
    unittest.main()
