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
        self.assertIn("| `settings` |  | `object({...})` | `{...}` | No |",
                      updated)
        self.assertTrue(DOCS.update_readme(self.module, check_mode=True))

    def test_heading_bounded_sections_refresh_without_separators(self):
        (self.module / "versions.tf").write_text('''terraform {
  required_version = ">= 1.17.0"

  required_providers {
    google = {
      source  = "hashicorp/google"
      version = ">= 7.0.0"
    }
    archive = {
      source  = "hashicorp/archive"
      version = ">= 2.8.1"
    }
  }
}
''')
        (self.module / "variables.tf").write_text('''variable "routes" {
  description = "Routes under <prefix>/<key> or a | b."
  type = map(map(object({
    target = string
  })))
  default = {}
}

variable "name" {
  description = "Bucket name."
  type        = string
}
''')
        readme = self.module / "README.md"
        readme.write_text('''# Demo

## Requirements & Providers

| old |

## Usage Examples

Kept as written.

## Inputs Specification

| old |

## Outputs Specification

| old |
''')
        self.assertTrue(DOCS.update_readme(self.module))
        updated = readme.read_text()
        self.assertIn("Kept as written.", updated)
        self.assertNotIn(":---:", updated)
        self.assertIn("| `terraform` | `>= 1.17.0` |\n| `google` | `>= 7.0.0` |\n| `archive` | `>= 2.8.1` |\n\n## Usage Examples",
                      updated)
        self.assertIn("| `routes` | Routes under `<prefix>/<key>` or a \\| b. | `map(map(object({...})))` | `{}` | No |", updated)
        self.assertIn("| `name` | Bucket name. | `string` | **Required** | Yes |", updated)
        self.assertIn("| Name | Description | Sensitive |\n| --- | --- | --- |\n| `id` | Resource ID. | No |", updated)
        self.assertTrue(DOCS.update_readme(self.module, check_mode=True))

    def test_single_line_defaults_stay_literal(self):
        long_list = "[" + ", ".join(f'"attribute_{i}"' for i in range(12)) + "]"
        (self.module / "variables.tf").write_text(f'''variable "attributes" {{
  description = "Attributes."
  type        = list(string)
  default     = {long_list}
}}
''')
        rows = DOCS.generate_markdown_tables(">= 1.5.0", [], DOCS.parse_variables(self.module), [])[1]
        self.assertIn(f"| `{long_list}` | No |", rows)

    def test_module_without_providers_gets_terraform_row_only(self):
        (self.module / "versions.tf").write_text('terraform {\n  required_version = ">= 1.17.0"\n}\n')
        readme = self.module / "README.md"
        readme.write_text("# Helper\n\n## Requirements & Providers\n\n| old |\n\n## Inputs Specification\n\n| old |\n\n## Outputs Specification\n\n| old |\n")
        self.assertTrue(DOCS.update_readme(self.module))
        self.assertIn("| Requirement | Version |\n| --- | --- |\n| `terraform` | `>= 1.17.0` |\n\n## Inputs", readme.read_text())

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
