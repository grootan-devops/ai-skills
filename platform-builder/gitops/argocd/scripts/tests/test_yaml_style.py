import sys
import unittest
from pathlib import Path

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import yaml_style  # noqa: E402

#: The layout of a hand-written environment values file.
SPACED = """global:
  routes:
    domain: "dev.example.com"
    tlsSecretName: example-dev-tls
    gateway:
      name: apisix-private

oauth:
  clientId: oneuptime-chat
  scopes:
    - openid
    - email
  url: "https://access-dev.{{ .Values.global.routes.domain }}"

database:
  redis:
    host: oneuptime-redis
    auth:
      username: default

  postgresql:
    host: postgres-cluster-rw.postgres
    auth:
      username: postgres

containers:
  main:
    image:
      repository: "oneuptime/{{ .Values.component }}/dev"

    resources:
      requests:
        memory: 1Gi
      limits:
        memory: 1Gi

externalConfig:
  data: |
    tcp-keepalive 400

    slowlog-max-len 158
"""

#: A public chart's values, written densely.
DENSE = """tenant:
  name: minio
  image:
    repository: docker.io/example/minio
  configSecret:
    name: minio-env-configuration
  env:
  - name: MINIO_PROMETHEUS_AUTH_TYPE
    value: 'public'
operator:
  replicas: 1
"""

DATA = {
    "global": {"image": {"pullSecrets": ["registry-pull-credentials"]},
               "routes": {"domain": "dev.example.com", "ingressClass": "nginx-private"}},
    "routes": {"default": {"host": "app.{{ .Values.global.routes.domain }}"}},
    "containers": {"main": {"image": {"repository": "oneuptime/app/dev", "tag": ""},
                            "resources": {"requests": {"memory": "256Mi"}, "limits": {"memory": "512Mi"}}}},
    "database": {"postgresql": {"host": "postgres-cluster-rw.postgres", "port": 5432}},
    "jira": {"tempoLocationId": "2", "note": "<ask: jira note>"},
}


class LearnTests(unittest.TestCase):
    def test_a_spaced_file(self):
        style = yaml_style.learn([SPACED])
        self.assertEqual((style.indent, style.indent_lists, style.top_blank, style.quote, style.quote_templates),
                         (2, True, True, '"', True))
        self.assertTrue(style.nested_blank[1])     # database.redis / database.postgresql
        self.assertTrue(style.nested_blank[2])     # containers.main.image / resources
        self.assertFalse(style.nested_blank[3])    # resources.requests / limits

    def test_a_dense_file(self):
        style = yaml_style.learn([DENSE])
        self.assertEqual((style.indent_lists, style.top_blank, style.quote), (False, False, "'"))
        self.assertFalse(style.nested_blank[1])

    def test_no_samples_give_the_default_layout(self):
        self.assertEqual(yaml_style.learn([]), yaml_style.Style())

    def test_a_block_scalar_body_is_not_read_as_keys(self):
        style = yaml_style.learn(["data: |\n  a: 1\n\n  b: 2\nother: 1\n"])
        self.assertFalse(style.top_blank)


class DumpTests(unittest.TestCase):
    def test_written_like_the_spaced_file(self):
        text = yaml_style.dump(DATA, yaml_style.learn([SPACED]))
        self.assertIn("    pullSecrets:\n      - registry-pull-credentials\n\n  routes:\n", text)
        self.assertIn("\n\nroutes:\n", text)
        self.assertIn('host: "app.{{ .Values.global.routes.domain }}"', text)
        self.assertIn('tag: ""\n\n    resources:\n', text)
        self.assertIn("        memory: 256Mi\n      limits:\n", text)
        self.assertIn('tempoLocationId: "2"', text)
        self.assertIn('note: "<ask: jira note>"', text)
        self.assertNotIn("\n\n\n", text)
        self.assertEqual(yaml.safe_load(text), DATA)

    def test_written_like_the_dense_file(self):
        text = yaml_style.dump(DATA, yaml_style.learn([DENSE]))
        self.assertNotIn("\n\n", text)
        self.assertIn("    pullSecrets:\n    - registry-pull-credentials\n", text)
        self.assertIn("tag: ''", text)
        self.assertEqual(yaml.safe_load(text), DATA)

    def test_multi_line_strings_stay_literal(self):
        data = {"externalConfig": {"data": "tcp-keepalive 400\n\nslowlog-max-len 158\n"}, "next": {"a": 1}}
        text = yaml_style.dump(data)
        self.assertIn("  data: |\n    tcp-keepalive 400\n\n    slowlog-max-len 158\n\nnext:\n", text)
        self.assertEqual(yaml.safe_load(text), data)

    def test_four_space_files_keep_four_spaces(self):
        style = yaml_style.learn(["a:\n    b:\n        c: 1\n"])
        self.assertEqual(style.indent, 4)
        self.assertIn("\n    routes:\n", yaml_style.dump(DATA, style))


if __name__ == "__main__":
    unittest.main()
