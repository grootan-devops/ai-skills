#!/usr/bin/env python3
"""Validate an observability.yml: raw and resolved schema passes plus an interpolation dry run.

Never prints resolved or secret values: findings carry paths, variable names and reasons only,
and ${file:...} placeholders are checked for existence without reading the file.

Usage: python3 validate.py <observability.yml> [--schema PATH] [--env-file PATH] [--require-env] [--json]

The schema defaults to the skill's own assets/observability.schema.json.
"""
from __future__ import annotations

import argparse
import copy
import json
import os
import re
import sys
from pathlib import Path
from urllib.parse import urlsplit

import yaml

BUNDLED_SCHEMA = Path(__file__).resolve().parents[1] / "assets" / "observability.schema.json"

PLACEHOLDER = re.compile(r"\$\{([^}]*)\}")
REFERENCE = re.compile(r"\$\$|\$\{([^}]*)\}")
NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
INTEGER = re.compile(r"[+-]?[0-9]+")
FLOAT = re.compile(r"[+-]?([0-9]+\.?[0-9]*|\.[0-9]+)([eE][+-]?[0-9]+)?")
FILE_MARKER = "<file>"
REASONS = {
    "required": "missing required key '{}'",
    "dependentRequired": "missing key '{}', which '{}' requires",
    "additionalProperties": "unknown key",
    "anyOf": "value matches none of the allowed forms",
    "oneOf": "value matches none of the allowed forms",
    "not": "value is not allowed",
    "type": "expected {}",
    "enum": "expected one of {}",
    "const": "expected {!r}",
    "pattern": "does not match {}",
    "minLength": "shorter than the minimum length {}",
    "minimum": "below the minimum {}",
    "maximum": "above the maximum {}",
    "minItems": "fewer than {} items",
    "maxItems": "more than {} items",
    "uniqueItems": "items are not unique",
}
LITERAL_KEYS = {"secret_key": "error", "public_key": "warning"}
PRODUCTION = re.compile(r"(^|[-_.])(prod|production|prd|live)([-_.]|$)", re.IGNORECASE)
LANGFUSE_ENVIRONMENT = re.compile(r"^(?!langfuse)[a-z0-9_-]{1,40}$")
CONTENT_FIELD = re.compile(
    r"(^|\.)(prompts?|messages?|contents?|completions?|questions?|answers?|input|output|query|text|instructions?|"
    r"system_prompt|documents?|context|history|transcript)$", re.IGNORECASE)
VALUE_CHECKS = {
    "enum": lambda value, allowed: value in allowed,
    "const": lambda value, const: value == const and isinstance(value, bool) == isinstance(const, bool),
    "pattern": lambda value, pattern: not isinstance(value, str) or re.search(pattern, value) is not None,
    "minLength": lambda value, length: not isinstance(value, str) or len(value) >= length,
    "minimum": lambda value, limit: not _type_ok(value, "number") or value >= limit,
    "maximum": lambda value, limit: not _type_ok(value, "number") or value <= limit,
}


def parse_placeholder(expr: str):
    """Return (scheme, target, default) for the text inside ${...}."""
    scheme = "env"
    if expr.startswith("file:"):
        return "file", expr[len("file:"):], None
    if expr.startswith("env:"):
        expr = expr[len("env:"):]
    name, sep, default = expr.partition(":-")
    return scheme, name, (default if sep else None)


def iter_strings(node, path="$"):
    if isinstance(node, dict):
        for key, value in node.items():
            yield from iter_strings(value, f"{path}.{key}")
    elif isinstance(node, list):
        for index, value in enumerate(node):
            yield from iter_strings(value, f"{path}[{index}]")
    elif isinstance(node, str):
        yield path, node


def check_interpolation(doc, env: dict, require_env: bool) -> list[dict]:
    findings = []
    for path, value in iter_strings(doc):
        for expr in PLACEHOLDER.findall(value.replace("$$", "")):
            scheme, target, default = parse_placeholder(expr)
            if scheme == "file":
                if not target.startswith("/"):
                    findings.append(_finding("error", path, f"${{file:...}} needs an absolute path, got '{target}'"))
                elif not Path(target).exists():
                    level = "error" if require_env else "warning"
                    findings.append(_finding(level, path, f"secret file {target} does not exist here"))
                continue
            if not NAME.match(target):
                findings.append(_finding("error", path, f"invalid placeholder '${{{expr}}}'"))
            elif target not in env and default is None:
                level = "error" if require_env else "warning"
                findings.append(_finding(level, path, f"{target} is not set and has no default"))
    return findings


def check_literal_secrets(doc) -> list[dict]:
    """Header values and Langfuse keys are credentials; they must come from interpolation, never literals."""
    findings = []

    def walk(node, path):
        if isinstance(node, dict):
            for key, value in node.items():
                child = f"{path}.{key}"
                if key == "headers" and isinstance(value, dict):
                    for header, header_value in value.items():
                        if isinstance(header_value, str) and header_value and "${" not in header_value:
                            findings.append(_finding("error", f"{child}.{header}",
                                                     "literal header value; use ${file:...} or ${env:...}"))
                if key in LITERAL_KEYS and isinstance(value, str) and value and "${" not in value:
                    findings.append(_finding(LITERAL_KEYS[key], child,
                                             f"literal {key.replace('_', ' ')}; use ${{file:...}} or ${{env:...}}"))
                walk(value, child)
        elif isinstance(node, list):
            for index, value in enumerate(node):
                walk(value, f"{path}[{index}]")

    walk(doc, "$")
    return findings


def resolve_string(value: str, env: dict) -> tuple[str, bool]:
    """Resolve $$ and ${...} as the loader does, never reading a file; also says if a reference was used."""
    references = []

    def substitute(match):
        if match.group(0) == "$$":
            return "$"
        references.append(match.group(1))
        return _reference_value(match.group(1), env)

    return REFERENCE.sub(substitute, value), bool(references)


def _reference_value(expr: str, env: dict) -> str:
    """One reference: unset or empty takes the default; an existing file is a fixed marker, never opened."""
    scheme, target, default = parse_placeholder(expr)
    if scheme == "file":
        return FILE_MARKER if target.startswith("/") and Path(target).exists() else ""
    return (env.get(target) if NAME.match(target) else None) or default or ""


def resolve(doc, env: dict, schema: dict, use_jsonschema: bool = True):
    """The document as the loader sees it: references resolved, typed by the schema, empty strings removed."""
    return _resolve_node(doc, env, _resolve_refs(resolved_schema(schema)), use_jsonschema)


def _resolve_node(node, env: dict, schema, use_jsonschema: bool):
    if isinstance(node, dict):
        resolved = {key: _resolve_node(value, env, _key_schema(schema, key), use_jsonschema) for key, value in node.items()}
        return {key: value for key, value in resolved.items() if value != ""}
    if isinstance(node, list):
        items = [_resolve_node(value, env, _item_schema(schema), use_jsonschema) for value in node]
        return [item for item in items if item != ""]
    if isinstance(node, str):
        text, referenced = resolve_string(node, env)
        return _typed(text, schema, use_jsonschema) if referenced and text and schema is not None else text
    return node


def _typed(text: str, schema: dict, use_jsonschema: bool):
    """The first reading of text the key's schema accepts, so the key's type wins; else the string."""
    return next((value for value in _readings(text) if not schema_errors(value, schema, use_jsonschema)), text)


def _readings(text: str) -> list:
    """A resolved string read as a boolean, an integer and a float where it parses, then as itself."""
    readings = [text.lower() == "true"] if text.lower() in ("true", "false") else []
    readings += [int(text)] if INTEGER.fullmatch(text) else []
    readings += [float(text)] if FLOAT.fullmatch(text) else []
    return readings + [text]


def _key_schema(schema, key):
    """The sub-schema for a mapping key; None when the schema doesn't describe it."""
    return _subschema(schema, lambda node: node.get("properties", {}).get(key, node.get("additionalProperties")))


def _item_schema(schema):
    """The sub-schema for list items; None when the schema doesn't describe them."""
    return _subschema(schema, lambda node: node.get("items"))


def _subschema(schema, pick):
    """What pick finds in schema itself or, failing that, in its oneOf/anyOf branches."""
    if not isinstance(schema, dict):
        return None
    found = pick(schema)
    if isinstance(found, dict):
        return found
    branches = (_subschema(branch, pick) for branch in schema.get("oneOf", []) + schema.get("anyOf", []))
    return next((branch for branch in branches if branch is not None), None)


def resolved_schema(schema: dict) -> dict:
    """A copy of schema whose interpolated alternative matches nothing, so only real values pass."""
    strict = copy.deepcopy(schema)
    if "interpolated" in strict.get("$defs", {}):
        strict["$defs"]["interpolated"] = {"not": {}}
    return strict


def check_resolved(doc, schema: dict, env: dict, require_env: bool, use_jsonschema: bool = True) -> list[dict]:
    """Pass 2: the resolved document against the schema again, reporting only what the raw pass didn't."""
    resolved = resolve(doc, env, schema, use_jsonschema)
    found = (schema_errors(resolved, resolved_schema(schema), use_jsonschema, safe=True)
             + check_rum_required(resolved) + check_genai_required(resolved))
    raw = {(item["path"], item["message"])
           for item in schema_errors(doc, schema, use_jsonschema, safe=True) + check_genai_required(doc)}
    level = "error" if require_env else "warning"
    return [_finding(level, item["path"], f"after resolution: {item['message']}")
            for item in _unique(found) if (item["path"], item["message"]) not in raw]


def check_rum_required(doc) -> list[dict]:
    """Keys RUM needs when on; the schema's if/then fires only for a boolean true, not a quoted "true"."""
    rum = doc.get("rum")
    if not isinstance(rum, dict) or not _is_on(rum.get("enabled")):
        return []
    missing = [("$.rum", key) for key in ("exporter", "app_identifier") if key not in rum]
    exporter, replay = rum.get("exporter"), rum.get("session_replay")
    if isinstance(exporter, dict) and "endpoint" not in exporter:
        missing.append(("$.rum.exporter", "endpoint"))
    if isinstance(replay, dict) and _is_on(replay.get("enabled")) and "host" not in replay:
        missing.append(("$.rum.session_replay", "host"))
    return [_finding("error", path, REASONS["required"].format(key)) for path, key in missing]


def check_genai_required(doc) -> list[dict]:
    """Keys Langfuse needs when on: base_url, and both keys when the app sends the spans or the scores. Like RUM's,
    the check counts a quoted "true" as on, which the schema can't express."""
    genai = _mapping(doc, "genai")
    langfuse = _mapping(genai, "langfuse")
    if not (_is_on(genai.get("enabled")) and _is_on(langfuse.get("enabled"))):
        return []
    needed = ["base_url"]
    if langfuse.get("export", "app") == "app" or _section_on(langfuse, "scores"):
        needed += ["public_key", "secret_key"]
    return [_finding("error", "$.genai.langfuse", REASONS["required"].format(key)) for key in needed if key not in langfuse]


def check_genai(doc, schema: dict | None, env: dict, use_jsonschema: bool = True) -> list[dict]:
    """GenAI settings that pass the schema but contradict each other or the standard; never prints a value."""
    resolved = resolve(doc, env, schema, use_jsonschema) if schema is not None else doc
    genai = _mapping(resolved, "genai")
    langfuse = _mapping(genai, "langfuse")
    if not genai:
        return []
    if not _is_on(genai.get("enabled")):
        return [_finding("warning", "$.genai.langfuse.enabled", "on while genai.enabled is off, so Langfuse stays off")] \
            if _is_on(langfuse.get("enabled")) else []
    findings = _genai_content(resolved, _mapping(genai, "content"))
    if _is_on(langfuse.get("enabled")):
        findings += _langfuse_mode(langfuse)
        findings += _langfuse_route(resolved, langfuse)
        findings += _langfuse_target(resolved, langfuse)
    return findings


def _langfuse_mode(langfuse: dict) -> list[dict]:
    """The mode against the route and the scores: the sdk mode exports from the app, the otel mode has no scores."""
    findings, mode = [], langfuse.get("mode", "hybrid")
    if mode == "sdk" and langfuse.get("export", "app") == "collector":
        findings.append(_finding("error", "$.genai.langfuse.export",
                                 "collector with mode sdk; the Langfuse SDK sends its spans from the app, so the "
                                 "collector route goes with mode hybrid or otel"))
    if mode == "otel" and _section_on(langfuse, "scores"):
        findings.append(_finding("warning", "$.genai.langfuse.scores.enabled",
                                 "on with mode otel, which never calls Langfuse's API, so no score is sent; use mode "
                                 "hybrid for scores"))
    return findings


def _genai_content(doc: dict, content: dict) -> list[dict]:
    """The content ceiling against the environment, and AI content in the access log."""
    findings = []
    capture = content.get("capture", "off")
    environment = _mapping(doc, "resource").get("environment")
    if capture == "full" and isinstance(environment, str) and PRODUCTION.search(environment):
        findings.append(_finding("warning", "$.genai.content.capture",
                                 "full in a production environment; production takes off, or redacted for features "
                                 "with a redactor"))
    if content.get("media") == "upload" and capture != "full":
        findings.append(_finding("warning", "$.genai.content.media",
                                 "upload needs capture full; media is recorded as a reference otherwise"))
    access = _mapping(_mapping(doc, "logging"), "access_log")
    if not _is_on(access.get("enabled", True)):
        return findings
    for side in ("request", "response"):
        body, base = _mapping(_mapping(access, side), "body"), f"$.logging.access_log.{side}.body"
        if _is_on(body.get("full")):
            findings.append(_finding("warning", f"{base}.full",
                                     "full bodies of AI routes hold prompts and completions; content goes only to Langfuse"))
        fields = body.get("fields") if isinstance(body.get("fields"), list) else []
        findings += [_finding("warning", f"{base}.fields[{index}]",
                              "looks like AI content (prompts, messages, completions); content goes only to Langfuse")
                     for index, field in enumerate(fields) if isinstance(field, str) and CONTENT_FIELD.search(field)]
    return findings


def _langfuse_route(doc: dict, langfuse: dict) -> list[dict]:
    """The collector route: the spans must reach the collector that has the Langfuse branch."""
    if langfuse.get("export", "app") != "collector":
        return []
    findings = []
    tracing = _mapping(doc, "tracing")
    if "enabled" in tracing and not _is_on(tracing["enabled"]):
        findings.append(_finding("error", "$.tracing.enabled",
                                 "off, but the collector route needs the spans exported to the collector"))
    if _mapping(_mapping(tracing, "exporter"), "headers"):
        findings.append(_finding("warning", "$.tracing.exporter.headers",
                                 "set, which means direct export to a backend; on the collector route the spans must "
                                 "pass the collector that has the Langfuse branch"))
    return findings


def _langfuse_target(doc: dict, langfuse: dict) -> list[dict]:
    """The Langfuse origin, and the environment name Langfuse files the traces under."""
    findings = []
    url = langfuse.get("base_url")
    if isinstance(url, str) and url != FILE_MARKER:
        parts = urlsplit(url)
        if parts.path not in ("", "/"):
            findings.append(_finding("warning", "$.genai.langfuse.base_url",
                                     "has a path; give the origin only, the module adds the OTLP and API paths"))
        if parts.scheme == "http" and not _local_host(parts.hostname or ""):
            findings.append(_finding("warning", "$.genai.langfuse.base_url",
                                     "plain http to a remote host; keys and content would cross the network in clear"))
    environment = _mapping(doc, "resource").get("environment")
    if isinstance(environment, str) and environment:
        if not LANGFUSE_ENVIRONMENT.match(environment.lower()):
            findings.append(_finding("error", "$.resource.environment",
                                     "not a valid Langfuse environment: lowercase letters, digits, - and _, at most 40 "
                                     "characters, not starting with 'langfuse'"))
        elif environment != environment.lower():
            findings.append(_finding("warning", "$.resource.environment",
                                     "has capitals; Langfuse lowercases it, so the two backends show different "
                                     "environment names"))
    return findings


def _local_host(host: str) -> bool:
    """Loopback or a cluster-internal name, where plain http stays on the node or in the cluster."""
    host = host.lower()
    return (host in ("localhost", "127.0.0.1", "::1") or "." not in host
            or host.endswith((".localhost", ".local", ".internal", ".svc", ".cluster.local")))


def _mapping(parent, key) -> dict:
    value = parent.get(key) if isinstance(parent, dict) else None
    return value if isinstance(value, dict) else {}


def _section_on(parent: dict, key: str) -> bool:
    return _is_on(_mapping(parent, key).get("enabled"))


def _is_on(value) -> bool:
    return value is True or value == "true"


def _type_ok(value, expected) -> bool:
    checks = {
        "object": lambda v: isinstance(v, dict),
        "array": lambda v: isinstance(v, list),
        "string": lambda v: isinstance(v, str),
        "boolean": lambda v: isinstance(v, bool),
        "integer": lambda v: isinstance(v, int) and not isinstance(v, bool),
        "number": lambda v: isinstance(v, (int, float)) and not isinstance(v, bool),
        "null": lambda v: v is None,
    }
    expected = expected if isinstance(expected, list) else [expected]
    return any(checks.get(item, lambda v: True)(value) for item in expected)


def _basic_schema_errors(node, schema, path="$") -> list[dict]:
    """Small fallback for when jsonschema isn't installed: types, values, required and unknown keys, if/then."""
    errors = []
    if "not" in schema and not _basic_schema_errors(node, schema["not"], path):
        errors.append(_finding("error", path, REASONS["not"]))
    variants = schema.get("oneOf") or schema.get("anyOf")
    if variants:
        if not any(not _basic_schema_errors(node, variant, path) for variant in variants):
            errors.append(_finding("error", path, REASONS["anyOf"]))
        return errors
    if "type" in schema and not _type_ok(node, schema["type"]):
        return errors + [_finding("error", path, REASONS["type"].format(schema["type"]))]
    errors += [_finding("error", path, REASONS[keyword].format(expected)) for keyword, expected in schema.items()
               if keyword in VALUE_CHECKS and not VALUE_CHECKS[keyword](node, expected)]
    if isinstance(node, dict):
        properties = schema.get("properties", {})
        for key, value in node.items():
            if key in properties:
                errors += _basic_schema_errors(value, properties[key], f"{path}.{key}")
            elif schema.get("additionalProperties") is False:
                errors.append(_finding("error", f"{path}.{key}", REASONS["additionalProperties"]))
            elif isinstance(schema.get("additionalProperties"), dict):
                errors += _basic_schema_errors(value, schema["additionalProperties"], f"{path}.{key}")
        errors += [_finding("error", path, REASONS["required"].format(key))
                   for key in schema.get("required", []) if key not in node]
        errors += [_finding("error", path, REASONS["dependentRequired"].format(need, key))
                   for key, needs in schema.get("dependentRequired", {}).items() if key in node
                   for need in needs if need not in node]
    if isinstance(node, list) and isinstance(schema.get("items"), dict):
        for index, value in enumerate(node):
            errors += _basic_schema_errors(value, schema["items"], f"{path}[{index}]")
    if "if" in schema:
        branch = "else" if _basic_schema_errors(node, schema["if"], path) else "then"
        errors += _basic_schema_errors(node, schema.get(branch, {}), path)
    return errors


def _resolve_refs(schema, root=None):
    """Inline local $ref / $defs so the fallback validator can follow them."""
    root = root or schema
    if isinstance(schema, dict):
        if "$ref" in schema and schema["$ref"].startswith("#/"):
            target = root
            for part in schema["$ref"][2:].split("/"):
                target = target[part]
            merged = {**_resolve_refs(target, root), **{k: v for k, v in schema.items() if k != "$ref"}}
            return merged
        return {key: _resolve_refs(value, root) for key, value in schema.items()}
    if isinstance(schema, list):
        return [_resolve_refs(item, root) for item in schema]
    return schema


def schema_errors(doc, schema, use_jsonschema: bool = True, safe: bool = False) -> list[dict]:
    """Findings for doc against schema; safe=True words them from the schema alone, never from a value."""
    if use_jsonschema:
        try:
            import jsonschema  # optional
        except ImportError:
            use_jsonschema = False
    if not use_jsonschema:
        return _basic_schema_errors(doc, _resolve_refs(schema))
    validator = jsonschema.Draft202012Validator(schema)
    errors = sorted(validator.iter_errors(doc), key=lambda e: list(map(str, e.absolute_path)))
    if safe:
        return _unique([_finding("error", path, reason) for error in errors for path, reason in _safe_findings(error)])
    return [_finding("error", _json_path(error.absolute_path), error.message) for error in errors]


def _safe_findings(error) -> list[tuple[str, str]]:
    """(path, reason) for one jsonschema error, worded from its keyword and schema, never its value."""
    path, keyword, expected, instance = _json_path(error.absolute_path), error.validator, error.validator_value, error.instance
    if keyword == "required":
        return [(path, REASONS[keyword].format(key)) for key in expected if key not in instance]
    if keyword == "dependentRequired":
        return [(path, REASONS[keyword].format(need, key))
                for key, needs in expected.items() if key in instance for need in needs if need not in instance]
    if keyword == "additionalProperties":
        return [(f"{path}.{key}", REASONS[keyword]) for key in instance if key not in error.schema.get("properties", {})]
    if keyword == "oneOf" and not error.context:
        return [(path, "value matches more than one allowed form")]
    return [(path, REASONS.get(keyword, f"fails the '{keyword}' rule").format(expected))]


def _json_path(parts) -> str:
    return "$" + "".join(f"[{part}]" if isinstance(part, int) else f".{part}" for part in parts)


def _unique(findings: list[dict]) -> list[dict]:
    return list({(item["path"], item["message"]): item for item in findings}.values())


def load_env_file(path: Path) -> dict:
    env = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            key, _, value = line.partition("=")
            env[key.strip()] = value.strip()
    return env


def _finding(level, path, message):
    return {"level": level, "path": path, "message": message}


def check_version_default(doc) -> list[dict]:
    """resource.service.version comes from the build, so its reference has no default."""
    value = ((doc.get("resource") or {}).get("service") or {}).get("version")
    if isinstance(value, str) and any(parse_placeholder(expr)[2] for expr in PLACEHOLDER.findall(value.replace("$$", ""))):
        return [_finding("warning", "$.resource.service.version", "has a default; the version must come from the build")]
    return []


def check_rum_identity(doc, schema: dict | None, env: dict, use_jsonschema: bool = True) -> list[dict]:
    """rum.app_identifier names the RUM application; OneUptime keys those by the browser's service.name."""
    resolved = resolve(doc, env, schema, use_jsonschema) if schema is not None else doc
    rum = resolved.get("rum") if isinstance(resolved, dict) else None
    if not isinstance(rum, dict) or not _is_on(rum.get("enabled")):
        return []
    app = rum.get("app_identifier")
    service = ((resolved.get("resource") or {}).get("service") or {}).get("name")
    if isinstance(app, str) and isinstance(service, str) and app and service and app != service:
        return [_finding("warning", "$.rum.app_identifier",
                         "differs from resource.service.name; OneUptime keys a RUM application by the browser's "
                         "service.name, so session replays would land in a second application")]
    return []


def validate(file: Path, schema: dict | None, env: dict, require_env: bool, use_jsonschema: bool = True) -> list[dict]:
    try:
        doc = yaml.safe_load(file.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        return [_finding("error", "$", f"not valid YAML: {exc}")]
    if not isinstance(doc, dict):
        return [_finding("error", "$", "top level must be a mapping")]
    findings = []
    if schema is not None:
        findings += schema_errors(doc, schema, use_jsonschema, safe=True)
    findings += check_literal_secrets(doc)
    findings += check_version_default(doc)
    findings += check_genai_required(doc)
    findings += check_interpolation(doc, env, require_env)
    if schema is not None:
        findings += check_resolved(doc, schema, env, require_env, use_jsonschema)
    findings += check_rum_identity(doc, schema, env, use_jsonschema)
    findings += check_genai(doc, schema, env, use_jsonschema)
    return findings


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("file", type=Path)
    parser.add_argument("--schema", type=Path, default=BUNDLED_SCHEMA,
                        help="JSON Schema path (default: the skill's assets/observability.schema.json)")
    parser.add_argument("--env-file", type=Path, help="KEY=VALUE file to resolve placeholders (values are never printed)")
    parser.add_argument("--require-env", action="store_true",
                        help="CI mode: unresolved placeholders, missing secret files and resolved-pass findings are errors")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)

    schema_path = args.schema if args.schema and args.schema.is_file() else None
    schema = json.loads(schema_path.read_text(encoding="utf-8")) if schema_path else None
    env = dict(os.environ)
    if args.env_file:
        env.update(load_env_file(args.env_file))

    findings = validate(args.file, schema, env, args.require_env)
    if args.json:
        print(json.dumps({"file": str(args.file), "schema": str(schema_path) if schema_path else None, "findings": findings}, indent=2))
    else:
        if schema_path is None:
            print("note: schema not found; only interpolation and secret checks ran")
        for item in findings:
            print(f"[{item['level']}] {item['path']}: {item['message']}")
        if not findings:
            print(f"ok: {args.file}")
    return 1 if any(item["level"] == "error" for item in findings) else 0


if __name__ == "__main__":
    sys.exit(main())
