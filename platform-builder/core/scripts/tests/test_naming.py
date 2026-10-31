import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import naming  # noqa: E402

LIBRARY_README = "# Lab (cluster name)\nArgoCD server name: in-cluster\n"
LIST_README = "# oneuptime GitOps Environment\n\n- Cluster: Edge West\n- Argo CD cluster name: edge-west\n"
STOCK_README = "# GitOps\n\n- Cluster: <cluster>\n- Argo CD cluster name: <argocd-cluster-name>\n"


class NamingTests(unittest.TestCase):
    def test_without_a_readme_label_the_repository_name_is_the_label(self):
        names = naming.derive("https://git.example.com/devops/environment/edge-east.git", STOCK_README, "oneuptime", "dev")
        self.assertEqual(names["label"], "edge-east")
        self.assertEqual(names["labelSource"], "repository name")
        self.assertEqual(names["chart"], "edge-east-oneuptime")
        self.assertEqual(names["branch"], "oneuptime/dev")
        self.assertEqual(names["namespace"], "oneuptime-dev")
        # The label prefixes the chart once; `cluster` stays unset, so no second segment.
        self.assertEqual(names["rootApplication"], "edge-east-oneuptime-dev-root")

    def test_a_readme_label_wins_over_the_repository_name(self):
        names = naming.derive("git@git.example.com:devops/platform-gitops.git", LIBRARY_README, "oneuptime", "prod")
        self.assertEqual((names["label"], names["labelSource"]), ("lab", "README"))
        self.assertEqual(names["destination"], "in-cluster")
        self.assertEqual(names["rootApplication"], "lab-oneuptime-prod-root")

    def test_the_list_form_is_read_and_made_dns_safe(self):
        names = naming.derive("https://git.example.com/x/scaleway.git", LIST_README, "OneUptime", "dev")
        self.assertEqual(names["label"], "edge-west")
        self.assertEqual(names["destination"], "edge-west")
        self.assertEqual(names["chart"], "edge-west-oneuptime")

    def test_a_generic_repository_name_without_a_readme_label_is_asked(self):
        names = naming.derive("https://git.example.com/devops/gitops.git", None, "oneuptime", "dev")
        self.assertIsNone(names["label"])
        self.assertIsNone(names["chart"])
        self.assertEqual(naming.main(["--repo-url", "https://git.example.com/devops/argocd.git",
                                      "--product", "oneuptime", "--env", "dev"]), 2)

    def test_no_environment_drops_the_suffixes(self):
        names = naming.derive("https://git.example.com/x/edge-east.git", None, "sandbox")
        self.assertEqual((names["branch"], names["namespace"], names["rootApplication"]),
                         ("sandbox", "sandbox", "edge-east-sandbox-root"))


if __name__ == "__main__":
    unittest.main()
