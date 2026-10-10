import builtins
import contextlib
import io
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import validate  # noqa: E402

LIBRARY_SCHEMA = Path(__file__).resolve().parents[2] / "assets" / "observability.schema.json"

SCHEMA = {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "version": {"type": "integer"},
        "tracing": {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "enabled": {"oneOf": [{"type": "boolean"}, {"type": "string"}]},
                "exporter": {"$ref": "#/$defs/exporter"},
            },
        },
        "logging": {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "output": {"oneOf": [
                    {"type": "string", "enum": ["stdout", "otlp"]},
                    {"type": "array", "items": {"type": "string", "enum": ["stdout", "otlp"]}},
                ]},
            },
        },
    },
    "$defs": {
        "exporter": {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "endpoint": {"type": "string"},
                "protocol": {"type": "string", "enum": ["grpc", "http/protobuf"]},
                "headers": {"type": "object", "additionalProperties": {"type": "string"}},
            },
        }
    },
}

GOOD = """
version: 1
tracing:
  enabled: true
  exporter:
    endpoint: "${OBS_OTLP_ENDPOINT}"
    protocol: grpc
    headers:
      x-backend-token: "${file:%(token)s}"
logging:
  output: [stdout, otlp]
"""

RESOURCE = """
version: 1
resource:
  service: {name: shop, version: "1.0.0"}
  environment: dev
"""

RESOLVABLE = """
version: 1
resource:
  service:
    name: "${OBS_SERVICE_NAME}"
    namespace: "${OBS_SERVICE_NAMESPACE:-}"
    version: "${OBS_SERVICE_VERSION}"
  environment: "${OBS_ENVIRONMENT}"
  attributes:
    team: payments
    vcs.ref.head.revision: "${GIT_SHA:-}"
tracing:
  enabled: "${OBS_TRACING:-true}"
  exporter:
    endpoint: "${OBS_TRACES_ENDPOINT:-http://alloy:4317}"
    protocol: "${OBS_PROTOCOL:-grpc}"
    timeout: "${OBS_TIMEOUT:-10s}"
    insecure: "${OBS_INSECURE:-false}"
    headers:
      x-token: "${file:%(token)s}"
  sampler:
    name: parentbased_traceidratio
    arg: "${OBS_SAMPLER_ARG}"
metrics:
  prometheus:
    port: "${OBS_METRICS_PORT:-9464}"
  otlp:
    enabled: "${OBS_OTLP_METRICS:-FALSE}"
    interval: "${OBS_INTERVAL:-60s}"
    temporality: "${OBS_TEMPORALITY:-delta}"
logging:
  output: [stdout, "${OBS_LOG_OUTPUT:-otlp}"]
  access_log:
    request:
      body:
        max_bytes: "${OBS_MAX_BYTES:-4096}"
profiling:
  sample_rate: "${OBS_SAMPLE_RATE:-100}"
rum:
  enabled: "${OBS_RUM:-true}"
  exporter:
    endpoint: "${OBS_RUM_ENDPOINT:-/telemetry}"
  app_identifier: "${OBS_SERVICE_NAME}"
  session_replay:
    enabled: "${OBS_REPLAY:-true}"
    host: "${OBS_RUM_HOST:-/telemetry/session-replay}"
"""

ENV = {"OBS_SERVICE_NAME": "shop", "OBS_SERVICE_VERSION": "1.4.2", "OBS_ENVIRONMENT": "dev", "OBS_SAMPLER_ARG": "1.0"}


class ValidateTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.token = self.root / "token"
        self.token.write_text("not-printed\n", encoding="utf-8")

    def tearDown(self):
        self.tmp.cleanup()

    def write(self, text: str) -> Path:
        path = self.root / "observability.yml"
        path.write_text(text, encoding="utf-8")
        return path

    def run_both(self, path, env, require_env=False):
        with_lib = validate.validate(path, SCHEMA, env, require_env, use_jsonschema=True)
        fallback = validate.validate(path, SCHEMA, env, require_env, use_jsonschema=False)
        return with_lib, fallback

    def test_valid_document_resolves(self):
        path = self.write(GOOD % {"token": self.token})
        for findings in self.run_both(path, {"OBS_OTLP_ENDPOINT": "http://collector:4317"}, require_env=True):
            self.assertEqual(findings, [])

    def test_missing_env_is_warning_then_error_in_ci(self):
        path = self.write(GOOD % {"token": self.token})
        relaxed = validate.validate(path, SCHEMA, {}, require_env=False, use_jsonschema=False)
        self.assertEqual([f["level"] for f in relaxed], ["warning"])
        strict = validate.validate(path, SCHEMA, {}, require_env=True, use_jsonschema=False)
        self.assertEqual([f["level"] for f in strict], ["error"])
        self.assertIn("OBS_OTLP_ENDPOINT", strict[0]["message"])

    def test_default_and_escape(self):
        path = self.write('version: 1\ntracing:\n  exporter:\n    endpoint: "${OBS_X:-http://a:4317}/$${NOT_A_VAR}"\n')
        self.assertEqual(validate.validate(path, SCHEMA, {}, True, use_jsonschema=False), [])

    def test_missing_secret_file_never_reads_or_prints(self):
        path = self.write(GOOD % {"token": self.root / "absent"})
        findings = validate.validate(path, SCHEMA, {"OBS_OTLP_ENDPOINT": "x"}, True, use_jsonschema=False)
        self.assertEqual(len(findings), 1)
        self.assertIn("does not exist", findings[0]["message"])
        self.assertNotIn("not-printed", str(findings))

    def test_literal_header_secret_is_error(self):
        path = self.write('version: 1\ntracing:\n  exporter:\n    headers:\n      api-key: "abc123"\n')
        findings = validate.validate(path, SCHEMA, {}, False, use_jsonschema=False)
        self.assertTrue(any("literal header value" in f["message"] for f in findings))
        self.assertNotIn("abc123", str(findings))

    def test_unknown_key_and_bad_enum(self):
        path = self.write("version: 1\nprivacy: {}\nlogging:\n  output: [stdout, kafka]\n")
        for findings in self.run_both(path, {}):
            paths = " ".join(f["path"] for f in findings)
            self.assertTrue(findings)
            self.assertIn("$", paths)

    def test_version_default_is_warned_without_its_value(self):
        path = self.write('version: 1\nresource:\n  service:\n    version: "${OBS_SERVICE_VERSION:-9.9.9-canary}"\n')
        findings = validate.check_version_default(yaml.safe_load(path.read_text()))
        self.assertEqual(findings, [validate._finding(
            "warning", "$.resource.service.version", "has a default; the version must come from the build")])
        path = self.write('version: 1\nresource:\n  service:\n    version: "${OBS_SERVICE_VERSION}"\n')
        self.assertEqual(validate.check_version_default(yaml.safe_load(path.read_text())), [])

    def test_raw_pass_messages_never_echo_values(self):
        path = self.write('version: 1\ntracing:\n  exporter:\n    protocol: "canary-proto-77"\n')
        findings = validate.validate(path, SCHEMA, {}, False, use_jsonschema=True)
        self.assertTrue(findings)
        self.assertNotIn("canary-proto-77", str(findings))

    def test_invalid_yaml(self):
        path = self.write("version: [1\n")
        self.assertIn("not valid YAML", validate.validate(path, None, {}, False)[0]["message"])

    def test_raw_errors_are_not_repeated_after_resolution(self):
        path = self.write("version: 1\nprivacy: {}\ntracing:\n  exporter:\n    protocol: grpcc\n")
        for findings in self.run_both(path, {}):
            self.assertEqual(len(findings), 2)
            self.assertFalse(any("after resolution" in f["message"] for f in findings))

    def test_fallback_handles_not_and_dependent_required(self):
        self.assertEqual(validate._basic_schema_errors("x", {"not": {}}),
                         [validate._finding("error", "$", "value is not allowed")])
        schema = {"type": "object", "dependentRequired": {"cert_file": ["key_file"]}}
        self.assertEqual(validate._basic_schema_errors({"cert_file": "/c"}, schema),
                         [validate._finding("error", "$", "missing key 'key_file', which 'cert_file' requires")])
        self.assertEqual(validate._basic_schema_errors({"cert_file": "/c", "key_file": "/k"}, schema), [])


@unittest.skipUnless(LIBRARY_SCHEMA.exists(), "assets/observability.schema.json is missing")
class ResolvedPassTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.schema = json.loads(LIBRARY_SCHEMA.read_text(encoding="utf-8"))

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.token = self.root / "token"
        self.token.write_text("not-printed\n", encoding="utf-8")

    def tearDown(self):
        self.tmp.cleanup()

    def write(self, text: str) -> Path:
        path = self.root / "observability.yml"
        path.write_text(text, encoding="utf-8")
        return path

    def test_resolvable_file_passes_both_passes(self):
        path = self.write(RESOLVABLE % {"token": self.token})
        for arg in ("1.0", "1"):
            for use_jsonschema in (True, False):
                findings = validate.validate(path, self.schema, {**ENV, "OBS_SAMPLER_ARG": arg}, True, use_jsonschema)
                self.assertEqual(findings, [], (arg, use_jsonschema))

    def test_resolved_values_take_the_key_type(self):
        doc = yaml.safe_load(RESOLVABLE % {"token": self.token})
        for use_jsonschema in (True, False):
            resolved = validate.resolve(doc, {**ENV, "OBS_SAMPLER_ARG": "1"}, self.schema, use_jsonschema)
            self.assertIs(resolved["tracing"]["enabled"], True)
            self.assertIs(resolved["tracing"]["exporter"]["insecure"], False)
            self.assertIs(resolved["metrics"]["otlp"]["enabled"], False)
            port = resolved["metrics"]["prometheus"]["port"]
            self.assertEqual((port, type(port)), (9464, int))
            self.assertEqual(resolved["tracing"]["sampler"]["arg"], "1")
            self.assertEqual(resolved["tracing"]["exporter"]["timeout"], "10s")
            self.assertEqual(resolved["resource"]["service"], {"name": "shop", "version": "1.4.2"})
            self.assertEqual(resolved["resource"]["attributes"], {"team": "payments"})

    def test_unresolved_required_key_is_reported_after_resolution(self):
        path = self.write(RESOURCE.replace('version: "1.0.0"', 'version: "${UNSET_VAR}"'))
        self.assertEqual(validate.schema_errors(yaml.safe_load(path.read_text(encoding="utf-8")), self.schema), [])
        for require_env, level in ((False, "warning"), (True, "error")):
            expected = [
                validate._finding(level, "$.resource.service.version", "UNSET_VAR is not set and has no default"),
                validate._finding(level, "$.resource.service", "after resolution: missing required key 'version'"),
            ]
            self.assertEqual(validate.validate(path, self.schema, {}, require_env, use_jsonschema=True), expected)
            self.assertEqual(validate.validate(path, self.schema, {}, require_env, use_jsonschema=False), expected)

    def test_wrong_forms_are_reported_after_resolution(self):
        path = self.write(RESOURCE + 'tracing:\n  exporter:\n    protocol: "${PROTO:-grpcc}"\n'
                                     'metrics:\n  prometheus:\n    port: "${PORT}"\n')
        self.assertEqual(validate.schema_errors(yaml.safe_load(path.read_text(encoding="utf-8")), self.schema), [])
        for use_jsonschema in (True, False):
            findings = validate.validate(path, self.schema, {"PORT": "abc"}, False, use_jsonschema)
            self.assertEqual(sorted(f["path"] for f in findings), ["$.metrics.prometheus.port", "$.tracing.exporter.protocol"])
            for finding in findings:
                self.assertEqual(finding["level"], "warning")
                self.assertTrue(finding["message"].startswith("after resolution: "), finding)

    def test_secret_file_header_is_checked_but_never_read(self):
        secret = self.root / "secret-token"
        secret.write_text("never-read\n", encoding="utf-8")
        path = self.write(RESOURCE + f'tracing:\n  exporter:\n    headers:\n      x-token: "${{file:{secret}}}"\n')
        opened = []

        def spy(real):
            def wrapper(file, *args, **kwargs):
                opened.append(str(file))
                return real(file, *args, **kwargs)
            return wrapper

        secret.chmod(0)
        try:
            with mock.patch("builtins.open", spy(builtins.open)), mock.patch("io.open", spy(io.open)), \
                    mock.patch("os.open", spy(os.open)):
                for use_jsonschema in (True, False):
                    self.assertEqual(validate.validate(path, self.schema, {}, True, use_jsonschema), [])
                resolved = validate.resolve(yaml.safe_load(path.read_text(encoding="utf-8")), {}, self.schema)
        finally:
            secret.chmod(0o600)
        self.assertNotIn(str(secret), opened)
        self.assertEqual(resolved["tracing"]["exporter"]["headers"], {"x-token": validate.FILE_MARKER})

    def test_no_resolved_value_is_printed(self):
        canary = "CANARY-9f3b"
        path = self.write(RESOURCE + """
tracing:
  enabled: "${CANARY}"
  exporter:
    protocol: "${CANARY}"
    timeout: "${CANARY}"
    headers:
      x-token: "${CANARY}"
  sampler:
    name: traceidratio
    arg: "${CANARY}"
metrics:
  prometheus:
    port: "${CANARY}"
    exclude_paths: ["/a-${CANARY}", "/a-${CANARY_TWIN}"]
logging:
  output: ["${CANARY}"]
  application:
    level: "${CANARY}"
""")
        for ci in ([], ["--require-env"]):
            for output in (["--json"], []):
                out = io.StringIO()
                with mock.patch.dict(os.environ, {"CANARY": canary, "CANARY_TWIN": canary}), contextlib.redirect_stdout(out):
                    code = validate.main([str(path), "--schema", str(LIBRARY_SCHEMA), *output, *ci])
                self.assertNotIn(canary, out.getvalue())
                self.assertEqual(code, 1 if ci else 0)
                if output:
                    report = json.loads(out.getvalue())
                    self.assertEqual(set(report), {"file", "schema", "findings"})
                    paths = {f["path"] for f in report["findings"] if f["message"].startswith("after resolution: ")}
                    self.assertLessEqual({"$.tracing.enabled", "$.tracing.exporter.protocol", "$.tracing.exporter.timeout",
                                          "$.tracing.sampler.arg", "$.metrics.prometheus.port", "$.logging.output",
                                          "$.logging.application.level"}, paths)

    def test_rum_keys_required_when_on(self):
        quoted = RESOURCE + 'rum:\n  enabled: "true"\n  exporter:\n    endpoint: /telemetry\n'
        replay = RESOURCE + ('rum:\n  enabled: "${OBS_RUM}"\n  exporter:\n    endpoint: /telemetry\n  app_identifier: shop\n'
                             '  session_replay:\n    enabled: true\n    host: "${OBS_RUM_HOST}"\n')
        literal = RESOURCE + 'rum:\n  enabled: true\n  exporter:\n    endpoint: /telemetry\n'
        for use_jsonschema in (True, False):
            self.assertEqual(validate.validate(self.write(quoted), self.schema, {}, False, use_jsonschema),
                             [validate._finding("warning", "$.rum", "after resolution: missing required key 'app_identifier'")])
            self.assertEqual(validate.validate(self.write(replay), self.schema, {"OBS_RUM": "true"}, True, use_jsonschema), [
                validate._finding("error", "$.rum.session_replay.host", "OBS_RUM_HOST is not set and has no default"),
                validate._finding("error", "$.rum.session_replay", "after resolution: missing required key 'host'"),
            ])
            findings = validate.validate(self.write(literal), self.schema, {}, False, use_jsonschema)
            self.assertEqual([(f["level"], f["path"]) for f in findings], [("error", "$.rum")])
            self.assertIn("app_identifier", findings[0]["message"])

    def test_replay_headers_for_the_collector_route(self):
        rum = RESOURCE + ('rum:\n  enabled: true\n  exporter:\n    endpoint: http://collector:4318\n  app_identifier: shop\n'
                          '  session_replay:\n    enabled: true\n    host: https://oneuptime.example.com\n'
                          '    headers:\n      x-oneuptime-token: %s\n')
        for use_jsonschema in (True, False):
            self.assertEqual(validate.validate(self.write(rum % '"${env:REPLAY_TOKEN}"'), self.schema,
                                               {"REPLAY_TOKEN": "t"}, True, use_jsonschema), [])
            findings = validate.validate(self.write(rum % "plain-key"), self.schema, {}, True, use_jsonschema)
            self.assertEqual([(f["level"], f["path"]) for f in findings],
                             [("error", "$.rum.session_replay.headers.x-oneuptime-token")])
            self.assertNotIn("plain-key", json.dumps(findings))

    def test_rum_app_identifier_matches_service_name(self):
        rum = RESOURCE + 'rum:\n  enabled: true\n  exporter:\n    endpoint: /telemetry\n  app_identifier: %s\n'
        for use_jsonschema in (True, False):
            self.assertEqual(validate.validate(self.write(rum % "shop"), self.schema, {}, True, use_jsonschema), [])
            self.assertEqual(validate.validate(self.write(rum % '"${OBS_APP:-shop}"'), self.schema, {}, True, use_jsonschema), [])
            findings = validate.validate(self.write(rum % "shop-web"), self.schema, {}, True, use_jsonschema)
            self.assertEqual([(f["level"], f["path"]) for f in findings], [("warning", "$.rum.app_identifier")])
        off = RESOURCE + 'rum:\n  enabled: false\n  app_identifier: shop-web\n'
        self.assertEqual(validate.validate(self.write(off), self.schema, {}, True), [])

    def test_temporality_and_tls_keys(self):
        for value in ("cumulative", "delta", "lowmemory", '"${OBS_TEMPORALITY:-cumulative}"'):
            path = self.write(RESOURCE + f"metrics:\n  otlp:\n    temporality: {value}\n")
            self.assertEqual(validate.validate(path, self.schema, {}, True), [], value)
        path = self.write(RESOURCE + "metrics:\n  otlp:\n    temporality: sideways\n")
        self.assertEqual([f["path"] for f in validate.validate(path, self.schema, {}, True)], ["$.metrics.otlp.temporality"])

        tls = RESOURCE + "tracing:\n  exporter:\n    tls:\n      ca_file: /etc/tls/ca.pem\n%s"
        path = self.write(tls % "      cert_file: /etc/tls/client.pem\n      key_file: /etc/tls/client-key.pem\n")
        self.assertEqual(validate.validate(path, self.schema, {}, True), [])
        path = self.write(tls % "      cert_file: /etc/tls/client.pem\n")
        findings = validate.validate(path, self.schema, {}, True, use_jsonschema=True)
        self.assertEqual([(f["level"], f["path"]) for f in findings], [("error", "$.tracing.exporter.tls")])
        self.assertIn("key_file", findings[0]["message"])
        path = self.write(tls % '      cert_file: /etc/tls/client.pem\n      key_file: "${OBS_TLS_KEY:-}"\n')
        for use_jsonschema in (True, False):
            self.assertEqual(validate.validate(path, self.schema, {}, False, use_jsonschema), [validate._finding(
                "warning", "$.tracing.exporter.tls", "after resolution: missing key 'key_file', which 'cert_file' requires")])


GENAI = RESOURCE + """
genai:
  enabled: true
  content:
    capture: "${OBS_GENAI_CONTENT:-off}"
    sample_ratio: "${OBS_GENAI_CONTENT_RATIO:-1.0}"
    max_chars: 20000
    media: reference
  metrics:
    enabled: true
  langfuse:
    enabled: "${OBS_LANGFUSE:-true}"
    mode: "${OBS_LANGFUSE_MODE:-hybrid}"
    export: "${OBS_LANGFUSE_EXPORT:-app}"
    scope: ai_context
    base_url: "${OBS_LANGFUSE_URL}"
    public_key: "${file:%(public)s}"
    secret_key: "${file:%(secret)s}"
    timeout: 10s
    flush_interval: 5s
    scores: {enabled: "true"}
"""


@unittest.skipUnless(LIBRARY_SCHEMA.exists(), "assets/observability.schema.json is missing")
class GenAITests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.schema = json.loads(LIBRARY_SCHEMA.read_text(encoding="utf-8"))

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.files = {"public": self.root / "langfuse-public-key", "secret": self.root / "langfuse-secret-key"}
        for path in self.files.values():
            path.write_text("never-printed", encoding="utf-8")

    def tearDown(self):
        self.tmp.cleanup()

    def write(self, text: str) -> Path:
        path = self.root / "observability.yml"
        path.write_text(text, encoding="utf-8")
        return path

    def langfuse(self, lines: str = "", genai: str = "", environment: str = "dev", extra: str = "") -> Path:
        """A file with GenAI and Langfuse on, the given langfuse lines added (indented by the caller's text)."""
        text = RESOURCE.replace("environment: dev", f"environment: {environment}") + extra + (
            "genai:\n  enabled: true\n" + genai + "  langfuse:\n    enabled: true\n" + lines)
        return self.write(text)

    def keys(self) -> str:
        return (f'    public_key: "${{file:{self.files["public"]}}}"\n'
                f'    secret_key: "${{file:{self.files["secret"]}}}"\n')

    def found(self, path, env=None, require_env=True) -> list[list[tuple]]:
        return [[(f["level"], f["path"], f["message"]) for f in validate.validate(path, self.schema, env or {}, require_env, lib)]
                for lib in (True, False)]

    def assert_both(self, path, expected, env=None, require_env=True):
        for findings in self.found(path, env, require_env):
            self.assertEqual(findings, expected)

    def test_genai_section_passes_both_passes(self):
        path = self.write(GENAI % {k: v for k, v in self.files.items()})
        self.assert_both(path, [], {"OBS_LANGFUSE_URL": "https://cloud.langfuse.com"})
        doc = yaml.safe_load(path.read_text(encoding="utf-8"))
        for use_jsonschema in (True, False):
            resolved = validate.resolve(doc, {"OBS_LANGFUSE_URL": "https://cloud.langfuse.com"}, self.schema, use_jsonschema)
            self.assertIs(resolved["genai"]["langfuse"]["enabled"], True)
            self.assertEqual(resolved["genai"]["content"], {"capture": "off", "sample_ratio": 1.0, "max_chars": 20000,
                                                           "media": "reference"})
            self.assertEqual(resolved["genai"]["langfuse"]["secret_key"], validate.FILE_MARKER)

    def test_bad_genai_values_fail_the_schema(self):
        path = self.write(RESOURCE + "genai:\n  content:\n    capture: all\n    media: inline\n"
                                     "  langfuse:\n    mode: langchain\n    export: sidecar\n    scope: everything\n"
                                     "    base_url: cloud.langfuse.com\n    flush: 5s\n    prompts: {enabled: true}\n")
        for findings in self.found(path):
            self.assertEqual(sorted(p for _, p, _ in findings), [
                "$.genai.content.capture", "$.genai.content.media", "$.genai.langfuse.base_url", "$.genai.langfuse.export",
                "$.genai.langfuse.flush", "$.genai.langfuse.mode", "$.genai.langfuse.prompts", "$.genai.langfuse.scope"])

    def test_langfuse_keys_required_when_on(self):
        missing = [("error", "$.genai.langfuse", f"missing required key '{key}'") for key in ("base_url", "public_key", "secret_key")]
        self.assert_both(self.langfuse(), missing)
        self.assert_both(self.write(RESOURCE + 'genai:\n  enabled: "true"\n  langfuse:\n    enabled: "true"\n'), missing)
        self.assert_both(self.langfuse("    export: collector\n"), missing[:1])
        self.assert_both(self.langfuse("    export: collector\n    scores: {enabled: true}\n"), missing)
        interpolated = self.write(RESOURCE + 'genai:\n  enabled: true\n  langfuse:\n    enabled: "${OBS_LANGFUSE}"\n')
        for require_env, level in ((False, "warning"), (True, "error")):
            self.assert_both(interpolated, [(level, m[1], f"after resolution: {m[2]}") for m in missing],
                             {"OBS_LANGFUSE": "true"}, require_env)
        self.assert_both(interpolated, [], {"OBS_LANGFUSE": "false"})
        self.assert_both(self.write(RESOURCE + "genai:\n  enabled: false\n  langfuse:\n    enabled: false\n"), [])

    def test_literal_langfuse_keys(self):
        path = self.langfuse('    base_url: https://cloud.langfuse.com\n    public_key: "pk-lf-CANARY-1"\n'
                             '    secret_key: "sk-lf-CANARY-2"\n')
        for findings in self.found(path):
            self.assertEqual(findings, [
                ("warning", "$.genai.langfuse.public_key", "literal public key; use ${file:...} or ${env:...}"),
                ("error", "$.genai.langfuse.secret_key", "literal secret key; use ${file:...} or ${env:...}")])
            self.assertNotIn("CANARY", str(findings))

    def test_collector_route_rules(self):
        base = "    export: collector\n    base_url: https://cloud.langfuse.com\n"
        self.assert_both(self.langfuse(base), [])
        self.assert_both(self.langfuse(base + self.keys()), [])  # app code may use the module's client
        off = self.langfuse(base, extra="tracing:\n  enabled: false\n  exporter:\n    headers:\n"
                                        '      x-token: "${file:/absent/token}"\n')
        self.assert_both(off, [
            ("warning", "$.tracing.exporter.headers.x-token", "secret file /absent/token does not exist here"),
            ("error", "$.tracing.enabled", "off, but the collector route needs the spans exported to the collector")],
            require_env=False)
        headers = self.langfuse(base, extra="tracing:\n  exporter:\n    headers:\n"
                                            f'      x-token: "${{file:{self.files["secret"]}}}"\n')
        self.assert_both(headers, [("warning", "$.tracing.exporter.headers", (
            "set, which means direct export to a backend; on the collector route the spans must pass the collector "
            "that has the Langfuse branch"))])

    def test_mode_rules(self):
        base = "    base_url: https://cloud.langfuse.com\n" + self.keys()
        for mode in ("hybrid", "sdk", "otel"):
            self.assert_both(self.langfuse(base + f"    mode: {mode}\n"), [])
        sdk_collector = ("error", "$.genai.langfuse.export", "collector with mode sdk; the Langfuse SDK sends its spans "
                         "from the app, so the collector route goes with mode hybrid or otel")
        self.assert_both(self.langfuse(base + "    mode: sdk\n    export: collector\n"), [sdk_collector])
        self.assert_both(self.langfuse(base + "    mode: otel\n    export: collector\n"), [])
        self.assert_both(self.langfuse(base + "    mode: otel\n    scores: {enabled: true}\n"), [(
            "warning", "$.genai.langfuse.scores.enabled", "on with mode otel, which never calls Langfuse's API, so no "
            "score is sent; use mode hybrid for scores")])
        switched = self.langfuse(base + '    mode: "${OBS_LANGFUSE_MODE:-hybrid}"\n    export: collector\n')
        self.assert_both(switched, [], {"OBS_LANGFUSE_MODE": "otel"})
        self.assert_both(switched, [sdk_collector], {"OBS_LANGFUSE_MODE": "sdk"})

    def test_langfuse_environment_names(self):
        base = "    export: collector\n    base_url: https://cloud.langfuse.com\n"
        invalid = ("error", "$.resource.environment", "not a valid Langfuse environment: lowercase letters, digits, "
                   "- and _, at most 40 characters, not starting with 'langfuse'")
        capitals = ("warning", "$.resource.environment", "has capitals; Langfuse lowercases it, so the two backends "
                    "show different environment names")
        for environment, expected in (("prod-eu", []), ("staging_2", []), ("Prod_EU", [capitals]),
                                      ("langfuse-dev", [invalid]), ("e" * 41, [invalid]), ("prod.eu", [invalid])):
            self.assert_both(self.langfuse(base, environment=environment), expected)
        self.assert_both(self.write(RESOURCE.replace("environment: dev", "environment: prod.eu")
                                    + "genai:\n  enabled: true\n"), [])

    def test_content_capture_rules(self):
        ceiling = "  content:\n    capture: \"${OBS_GENAI_CONTENT:-off}\"\n"
        full = ("warning", "$.genai.content.capture", "full in a production environment; production takes off, or "
                "redacted for features with a redactor")
        for environment, value, expected in (("production", "full", [full]), ("prod-eu", "full", [full]),
                                             ("staging", "full", []), ("production", "redacted", []),
                                             ("production", "off", []), ("product-demo", "full", [])):
            path = self.write(RESOURCE.replace("environment: dev", f"environment: {environment}")
                              + "genai:\n  enabled: true\n" + ceiling)
            self.assert_both(path, expected, {"OBS_GENAI_CONTENT": value})
        media = self.write(RESOURCE + "genai:\n  enabled: true\n  content:\n    media: upload\n")
        self.assert_both(media, [("warning", "$.genai.content.media",
                                  "upload needs capture full; media is recorded as a reference otherwise")])
        self.assert_both(self.write(RESOURCE + "genai:\n  enabled: true\n  content:\n    capture: full\n    media: upload\n"), [])

    def test_access_log_content_fields(self):
        logging = ("logging:\n  access_log:\n    request:\n      body:\n        fields: [amount, prompt, order.messages]\n"
                   "    response:\n      body:\n        full: true\n")
        warning = "looks like AI content (prompts, messages, completions); content goes only to Langfuse"
        self.assert_both(self.write(RESOURCE + logging + "genai:\n  enabled: true\n"), [
            ("warning", "$.logging.access_log.request.body.fields[1]", warning),
            ("warning", "$.logging.access_log.request.body.fields[2]", warning),
            ("warning", "$.logging.access_log.response.body.full",
             "full bodies of AI routes hold prompts and completions; content goes only to Langfuse")])
        self.assert_both(self.write(RESOURCE + logging + "genai:\n  enabled: false\n"), [])
        self.assert_both(self.write(RESOURCE + logging.replace("  access_log:\n", "  access_log:\n    enabled: false\n")
                                    + "genai:\n  enabled: true\n"), [])

    def test_base_url_forms(self):
        path_warning = ("warning", "$.genai.langfuse.base_url",
                        "has a path; give the origin only, the module adds the OTLP and API paths")
        http_warning = ("warning", "$.genai.langfuse.base_url",
                        "plain http to a remote host; keys and content would cross the network in clear")
        for url, expected in (("https://cloud.langfuse.com", []), ("https://cloud.langfuse.com/", []),
                              ("https://cloud.langfuse.com/api/public/otel", [path_warning]),
                              ("http://langfuse.example.com", [http_warning]), ("http://localhost:3000", []),
                              ("http://langfuse-web.langfuse.svc:3000", []), ("http://langfuse-web:3000", [])):
            self.assert_both(self.langfuse(f"    base_url: {url}\n" + self.keys()), expected)

    def test_langfuse_without_genai_is_warned(self):
        path = self.write(RESOURCE + "genai:\n  enabled: false\n  langfuse:\n    enabled: true\n")
        self.assert_both(path, [("warning", "$.genai.langfuse.enabled",
                                 "on while genai.enabled is off, so Langfuse stays off")])

    def test_no_genai_value_is_printed(self):
        canary = "CANARY-77ab"
        path = self.write(RESOURCE.replace("environment: dev", 'environment: "${OBS_ENV}"') + """
genai:
  enabled: true
  content:
    capture: "${CANARY}"
  langfuse:
    enabled: true
    base_url: "${CANARY_URL}"
    public_key: "${CANARY}"
    secret_key: "${CANARY}"
    mode: "${CANARY}"
""")
        env = {"CANARY": canary, "CANARY_URL": f"http://{canary}.example.com/path", "OBS_ENV": f"Prod.{canary}"}
        for ci in ([], ["--require-env"]):
            for output in (["--json"], []):
                out = io.StringIO()
                with mock.patch.dict(os.environ, env), contextlib.redirect_stdout(out):
                    validate.main([str(path), "--schema", str(LIBRARY_SCHEMA), *output, *ci])
                self.assertNotIn(canary, out.getvalue())
                self.assertIn("$.resource.environment", out.getvalue())


if __name__ == "__main__":
    unittest.main()
