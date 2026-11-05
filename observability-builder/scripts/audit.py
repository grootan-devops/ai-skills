#!/usr/bin/env python3
"""Static telemetry-safety and layout audit for any language.

Flags source lines where instrumentation may capture secrets, bodies or unbounded values,
and SDK setup outside the observability module. A secret or a request body read into a
variable (or a framework-injected header parameter) is followed through its block, so logging
it on a later line is caught too, and a log call spread over several lines is read as one
statement. Values named like secrets that reach a log, a span or an error message, and secrets
sent in outbound query strings, are flagged for review. Application output written around the
app logger, scheduled jobs that start no job root span, and patches of observability SDK internals
are reported too. For GenAI: prompt and completion content recorded outside the module's content
policy or written to logs, capture switches turned on in code, Langfuse secret keys in source or in
the page, AI context in baggage, two instrumentations of one AI library, and a second sender of spans to
Langfuse's OTLP endpoint. For the data model (references/data-model.md): identity rows set from raw
client headers, `request.id` and `code.namespace` attributes, ID-like metric labels, personal data
logged without the masking helper, static profile labels that don't come from the resource, baggage
over its limits, and resource keys the standard never adds (a process or host detector whose output passes an
allow-list in the same file is accepted). For the telemetry's own cost and safety (references/overhead-and-security.md):
synchronous span or log processors, module OTLP exporters built without a timeout option (the option or an
OTEL_EXPORTER_OTLP_*TIMEOUT within 15 lines counts; options built elsewhere are not followed), plaintext export to a
remote host (accepted with a comment on the line or above naming a private hop: sidecar, in-cluster, NetworkPolicy,
mesh, mTLS), literal telemetry credentials in code or config, metrics or profiling servers bound to 0.0.0.0 without
a comment saying who reaches them, attribute value length limits above 8192 (accepted when equal to the yml's
genai.content.max_chars, with a comment on the line or above naming max_chars), module log formatters that copy every
LogRecord attribute, and module debug calls that format their message before the level check. The skill's own
scripts are skipped when they sit inside the scanned project. Heuristic by design:
the agent judges the rest (see references/audit.md). Add `obs-audit: ignore` to a line to accept it.

Usage: python3 audit.py <project> [--module PATH ...] [--include-tests] [--json] [--strict]
--module takes a path relative to the project, relative to the working directory, or absolute.
Exit codes: 0 no findings, 1 findings with --strict, 2 any P0.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from dataclasses import asdict, dataclass
from pathlib import Path

SKIP_DIRS = {
    ".git", "node_modules", "dist", "build", "target", "vendor", "__pycache__", ".venv",
    "venv", ".next", "coverage", ".turbo", ".gradle", ".svelte-kit", ".nuxt", ".output", ".angular",
}
SOURCE_SUFFIXES = {
    ".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs", ".mts", ".cts", ".vue", ".svelte", ".py", ".go",
    ".java", ".kt", ".kts", ".rb", ".php", ".cs", ".rs",
}
TEST_RE = re.compile(
    r"(^|/)(tests?|__tests__|spec|e2e|testdata)(/|$)|\.(test|spec|e2e-spec)\.[^/]+$|_test\.(go|py)$|(^|/)(test_[^/]+|conftest)\.py$"
    r"|(^|/)tests?\.rs$"
)
IGNORE_RE = re.compile(r"obs-audit:\s*ignore")
SKILL_SCRIPTS = Path(__file__).resolve().parent

# Telemetry sinks: span attributes and events, baggage, log calls.
ATTR_SINK = re.compile(r"\b(setAttributes?|set_attributes?|SetAttributes|addEvent|add_event|AddEvent|attribute\.(String|Int|Bool)\w*)\s*\(")
BAGGAGE_SINK = re.compile(r"\b(setBaggage|set_baggage|setEntry|baggage\.(New)?Member|propagation\.setBaggage)\b")
LOG_SINK = re.compile(r"(?<![\w$])_{0,2}(console|logger|log|logging|slog|Log|LOG|LOGGER|Logger)\s*\.\s*(log|info|warn|warning|error|debug|trace|fatal|critical|exception|Info|Warn|Error|Debug)\w*\s*\(")
# Plain output that ends up in container logs just the same.
PRINT_SINK = re.compile(
    r"(?<![\w.])print\s*\(|\bfmt\.Print\w*\s*\(|\bfmt\.Fprint\w*\s*\(\s*os\.Std(out|err)\b"
    r"|\blog\.(Print|Fatal|Panic)\w*\s*\(|\bSystem\.(out|err)\.(print|println|printf|format)\s*\("
    r"|\b(process|sys)\.(stdout|stderr)\.write\s*\("
)
# Error messages reach logs, exception.message on spans and error trackers.
ERROR_SINK = re.compile(
    r"\bthrow\s+(?:new\s+)?[\w.]+\s*\(|\braise\s+[\w.]+\s*\(|\b(?:errors\.New|fmt\.Errorf|panic)\s*\("
    r"|\bnew\s+[\w.]*(?:Error|Exception)\s*\("
)
METRIC_SINK = re.compile(r"\b\w*(counter|histogram|gauge|metric|meter|Counter|Histogram|Gauge|Metric)\w*\s*\.\s*(add|record|inc|dec|observe|labels|set|With|Add|Record)\s*\(|\.labels\s*\(")
PROFILE_SINK = re.compile(r"\b(wrapWithLabels|setLabels|tag_wrapper|add_thread_tag|TagWrapper|LabelsWrapper|LabelsSet|pyroscope\.Labels)\b")

SECRET_HEADER = r"authorization|cookie|set-cookie|x-api-key|proxy-authorization|x-[\w-]*token|x-[\w-]*secret|x-[\w-]*api-?key"
SECRET_READ = re.compile(
    rf"""headers?\s*(\[\s*['"]|\.\s*get\s*\(\s*['"]|\.)({SECRET_HEADER})\b"""
    rf"""|get(Header|HeaderString)?\s*\(\s*['"]({SECRET_HEADER})['"]"""
    rf"""|(?<![\w.])header\s*\(\s*['"]({SECRET_HEADER})['"]"""
    r"""|META\s*(\[\s*|\.\s*get\s*\(\s*)['"]HTTP_(AUTHORIZATION|COOKIE|X_API_KEY|X_[A-Z0-9_]*TOKEN)['"]"""
    r"""|\.(password|passwd|secret|apiKey|api_key|accessToken|access_token|refreshToken|refresh_token|clientSecret|client_secret)\b(?![.\w-])"""
    r"""|['"](authorization|cookie|password|secret|api[-_]?key|access[-_]?token|refresh[-_]?token|client[-_]?secret)['"]\s*[:,=)]"""
    r"""|\b(HttpHeaders|HttpHeaderNames|HeaderNames)\s*\.\s*(AUTHORIZATION|COOKIE|PROXY_AUTHORIZATION|SET_COOKIE|Authorization|Cookie)\b"""
    r"""|\bHeader(Authorization|Cookie|SetCookie|ProxyAuthorization|XAPIKey|XApiKey)\b""",
    re.IGNORECASE,
)
# Identifiers named like secrets: `token`, `apiKey`, `x_debug_token`, `clientSecret`, `sessionCookie`.
SECRET_IDENT = re.compile(r"(authorization|auth_?header|cookies?|api_?key|token|password|passwd|secret|credentials?|card_?(?:number|num|no)|cvv|cvc)$", re.IGNORECASE)
NOT_SECRET_IDENT = re.compile(r"(page|next|cancellation|continuation|resume|sync)_?token$|^(is|has|should|use|with|no|max|min|num|count)(_|[A-Z])", re.IGNORECASE)
# Assignments: `const x =`, `x :=`, `String x =`, `a, b :=`, `x =` (never ==, !=, <=, >=, =>).
ASSIGN = re.compile(r"\b([A-Za-z_]\w*)\s*(?::=|(?<![=!<>])=(?![=>]))\s*(.+)$")
MULTI_ASSIGN = re.compile(r"^\s*(?:var\s+)?([A-Za-z_]\w*(?:\s*,\s*[A-Za-z_]\w*)+)\s*(?::=|(?<![=!<>])=(?![=>]))\s*(.+)$")
DESTRUCTURED_HEADERS = re.compile(r"\{([^}]*)\}\s*=\s*(req|request|ctx\.request)\s*\.\s*headers\b")
# Framework-injected header parameters: FastAPI `Header()`, and annotations such as Spring
# `@RequestHeader`, JAX-RS `@HeaderParam`, NestJS `@Headers('authorization')`, `@CookieValue`.
HEADER_PARAM = re.compile(
    r"\b([A-Za-z_]\w*)\s*:\s*Annotated\s*\[[^\]]*?\bHeader\s*\("
    r"|\b([A-Za-z_]\w*)\s*:\s*[\w\[\]|. ,]*?=\s*Header\s*\("
)
HEADER_ANNOTATION = re.compile(r"@(RequestHeader|HeaderParam|Headers|CookieValue|CookieParam)\s*\(([^)]*)\)\s*(?:final\s+)?(?:[\w.<>\[\]]+\s+)?(\w+)")
MEMBER = r"(?!\s*(?:\?\.|!\.|\.|\[|\?\.\[))"
BODY_REF = re.compile(rf"\b(req|request|res|response|ctx\.request)\s*\.\s*(body|rawBody|data)\b{MEMBER}|JSON\.stringify\s*\(\s*(req|request|res|response)\b|json\.dumps\s*\(\s*(request|response)\b")
# Reading a raw request body, in the common frameworks.
BODY_READ = re.compile(
    rf"\b(?:req|request|ctx\.request|ctx|r|c\.Request)\s*\.\s*(?:body|rawBody|Body|data|POST)\b{MEMBER}"
    r"|\b(?:get_data|get_json|getContentAsByteArray|getContentAsString|getEntityStream|GetRawData|getInputStream|getReader)\s*\("
    r"|\bawait\s+(?:req|request)\s*\.\s*(?:body|json|text|formData)\s*\("
    r"|\b(?:req|request)\s*\.\s*clone\s*\(\s*\)\s*\.\s*(?:text|json|arrayBuffer|formData|body)\b"
    r"|\b(?:read|get|parse|capture)\w*Body\s*\(\s*(?:req|request|ctx|c|r)\b"
    r"|\bio(?:util)?\.ReadAll\s*\(\s*[\w.]*Body\b"
    r"|\b(?:getProperty|getAttribute|get_attribute)\s*\(\s*[\w.\"']*(?i:body)[\w.\"']*\s*\)"
)
HEADERS_WHOLE = re.compile(rf"\b(req|request|ctx\.request)\s*\.\s*headers\b{MEMBER}")
# A raw body passed on: an alias, a serialization, or an interpolation of the whole value.
SERIALIZE = r"(?:JSON\.stringify|String|str|bytes|string|repr|json\.dumps|Buffer\.from|new\s+String)\s*\(\s*"
SERIALIZE_METHOD = r"\s*(?:\??\.\s*(?:toString|decode|toJSON|text|json|string)\s*\()"
QUERY_KEYS = r"api[-_]?key|apikey|access[-_]?token|id[-_]?token(?:[-_]hint)?|refresh[-_]?token|token|secret|password|passwd|client[-_]?secret|sig|signature"
QUERY_SECRET = re.compile(
    rf"""[?&]({QUERY_KEYS})="""
    rf"""|\b\w*(?:[Pp]arams|[Qq]uery|[Qq]s|[Vv]alues|[Uu]rl|[Uu]ri|[Bb]uilder|\bq)\s*\.\s*(?:append|set|add|Set|Add)\s*\(\s*['"]({QUERY_KEYS})['"]"""
    rf"""|\.\s*(?:queryParam|QueryParam)\s*\(\s*['"]({QUERY_KEYS})['"]"""
    rf"""|\bparams\s*(?:\[\s*|=\s*\{{[^}}]*)['"]({QUERY_KEYS})['"]"""
    rf'|@(?:QueryParam|RequestParam|Query)\s*\(\s*(?:(?:value|name)\s*=\s*)?"({QUERY_KEYS})"',
    re.IGNORECASE,
)
ID_KEY = re.compile(
    r"""['"]?\b(user[._]?id|userId|session[._]?id|sessionId|tenant[._]?id|tenantId|order[._]?id|orderId|request[._]?id|requestId|trace[._]?id|traceId|correlation[._]?id|correlationId|email"""
    r"""|(?:org|organi[sz]ation|account|customer|conversation|span)[._]?id|(?:org|organi[sz]ation|account|customer|conversation|span)Id"""
    r"""|url\.full|url\.path|http\.target|http\.url|db\.query\.text|db\.statement)\b['"]?\s*[:=,)]""",
    re.IGNORECASE,
)
SDK_SETUP = re.compile(
    r"\bnew\s+(NodeSDK|NodeTracerProvider|WebTracerProvider|BasicTracerProvider|MeterProvider|LoggerProvider)\s*\("
    r"|\bsdktrace\.NewTracerProvider\s*\(|\bsdkmetric\.NewMeterProvider\s*\(|\bOpenTelemetrySdk\s*\.\s*builder\s*\("
    r"|^\s*\w*\s*=?\s*TracerProvider\s*\(|\bpyroscope\s*\.\s*(configure|init|Start)\s*\(|\bPyroscope\s*\.\s*init\s*\("
    r"|\bPyroscopeAgent\s*\.\s*start\s*\(|\bregisterInstrumentations\s*\("
    # Langfuse clients and processors, OpenLLMetry's init, GenAI instrumentors: the loader's switches must come first.
    r"|\bnew\s+Langfuse(?:SpanProcessor|Client|Exporter)?\s*\(|(?<![\w.])Langfuse\s*\(|\bTraceloop\s*\.\s*init\s*\("
    r"|\btraceloop\s*\.\s*initialize\s*\(|\b(?:OpenAI|Anthropic|LangChain|LlamaIndex|Bedrock|VertexAI|GoogleGenAi|"
    r"GoogleGenerativeAi|MistralAI|Cohere|Groq|Ollama|CrewAI|OpenAIAgents|DSPy|Haystack|LiteLLM|Instructor|PydanticAI|"
    r"SmolAgents)Instrumentor\s*\(\s*\)\s*\.\s*instrument\s*\("
)
OBS_ENV_PREFIX = r"(?:OTEL|LANGFUSE|TRACELOOP|OPENINFERENCE)_"
OTEL_ENV = re.compile(rf"""process\.env(\.|\[\s*['"]){OBS_ENV_PREFIX}|os\.environ[^\n]*['"]{OBS_ENV_PREFIX}"""
                      rf"""|os\.getenv\s*\(\s*['"]{OBS_ENV_PREFIX}|os\.Getenv\s*\(\s*"{OBS_ENV_PREFIX}|System\.getenv\s*\(\s*"{OBS_ENV_PREFIX}""")
XFF = re.compile(r"""x-forwarded-for""", re.IGNORECASE)
# Browser code: a telemetry backend key in code that runs in the page.
BROWSER_KEY = re.compile(r"""['"`]Api-Token\s""")  # Dynatrace's auth scheme; other keys show up as PUBLIC_KEY names
# A telemetry key in a build-time public variable or in public runtime config: bundlers ship these to the page.
# Only telemetry credentials count; other secrets in a bundle belong to another review, not this audit.
TELEMETRY_HINT = r"(?:OTEL|OTLP|TELEMETRY|PYROSCOPE|OBSERVABILITY|INGESTION)"
PUBLIC_KEY = re.compile(
    rf"""\b(?:(?:NEXT_|NUXT_)?PUBLIC_|VITE_|REACT_APP_|NG_APP_)\w*{TELEMETRY_HINT}\w*(?:TOKEN|SECRET|KEY)"""
    rf"""|\.public\.\w*{TELEMETRY_HINT}\w*(?:token|secret|key)""", re.IGNORECASE)

# Data model (references/data-model.md).
# Metric declarations whose label list names an ID, a URL, a path, SQL or an email.
METRIC_DECL = re.compile(r"\blabel_?[Nn]ames\b|\blabelnames\b|\bNew(?:Counter|Histogram|Gauge|Summary)Vec\s*\("
                         r"|\bnew\s+(?:[\w.]+\.)?(?:Counter|Histogram|Gauge|Summary)\s*\(|(?<![\w.])(?:Counter|Histogram|Gauge|Summary)\s*\(")
METRIC_ID_LABEL = re.compile(
    r"""['"](?:user|tenant|session|org|organi[sz]ation|account|customer|order|request|correlation|trace|span|conversation)"""
    r"""[_.-]?id['"]|['"](?:user|tenant|session|org|account|customer|order|request|correlation|trace|span)Id['"]"""
    r"""|['"](?:email|user_email|url|path|raw_path|uri|full_url|sql|query|statement|error_message)['"]""")
# Identity read from what the client sends: a header, cookie or query value.
IDENTITY_HEADER = r"x-(?:user|tenant|org|organi[sz]ation|account|customer|session)(?:[-_]?id)?|x-(?:user-)?roles?"
IDENTITY_READ = re.compile(
    rf"""headers?\s*(?:\[\s*['"]|\.\s*get\s*\(\s*['"]|\.)(?:{IDENTITY_HEADER})\b"""
    rf"""|get(?:Header|HeaderString)?\s*\(\s*['"](?:{IDENTITY_HEADER})['"]"""
    rf"""|(?<![\w.])header\s*\(\s*['"](?:{IDENTITY_HEADER})['"]"""
    r"""|META\s*(?:\[\s*|\.\s*get\s*\(\s*)['"]HTTP_X_(?:USER|TENANT|ORG|ORGANI[SZ]ATION|ACCOUNT|CUSTOMER|SESSION)(?:_ID)?['"]"""
    r"""|\bcookies?\s*(?:\[\s*|\.\s*get\s*\(\s*)['"](?:user|tenant|session|org|account)[-_]?id['"]"""
    r"""|\b(?:query|args|searchParams|query_params|queryParams|URL\.Query\(\))\s*(?:\.\s*[Gg]et\s*\(\s*['"]|\[\s*['"]|\.)"""
    r"""(?:user|tenant|org|account|customer)[-_]?[iI]d\b""",
    re.IGNORECASE,
)
IDENTITY_PARAM = re.compile(r"^x_?(?:user|tenant|org|organi[sz]ation|account|customer|session)_?id$|^x_?(?:user_?)?roles?$",
                            re.IGNORECASE)
IDENTITY_KEY = re.compile(
    r"""['"](?:user\.id|tenant\.id|session\.id|user\.roles|enduser\.id|app\.(?:organization|account|customer)\.id)['"]"""
    r"""|\b(?:ATTR_|SEMATTRS_)?(?:USER_ID|ENDUSER_ID|SESSION_ID|USER_ROLES)\b|\bsemconv\.(?:UserID|EnduserID|SessionID|UserRoles)\w*""")
# request.id: the attribute name anywhere, request_id / requestId as a field of a log, span or logger context.
REQUEST_ID_ATTR = re.compile(r"""['"](?:app\.)?request\.id['"]""")
REQUEST_ID_FIELD = re.compile(r"""['"](?:request_id|requestId|request-id)['"]\s*[:,)]|\brequest_id\s*=(?!=)""")
LOG_CONTEXT = re.compile(r"\bMDC\s*\.\s*put\s*\(|\bbind_contextvars\s*\(|\b(?:ThreadContext|LogContext)\s*\.\s*(?:put|push)\w*\s*\("
                         r"|\.\s*(?:bind|child|with)\s*\(|\bextra\s*=")
CODE_NAMESPACE = re.compile(r"""['"]code\.namespace['"]|\b(?:ATTR_|SEMATTRS_)?CODE_NAMESPACE\b|\bCodeNamespace(?:Key)?\b""")
# Personal data by name: the identifier lower-cased with `_` removed ends with one of these.
PII_SUFFIXES = ("email", "emails", "emailaddress", "phone", "phonenumber", "mobile", "mobilenumber", "msisdn", "firstname",
                "lastname", "fullname", "givenname", "familyname", "surname", "dateofbirth", "birthdate", "dob", "ssn",
                "socialsecuritynumber", "nationalid", "nationalinsurancenumber", "passport", "passportnumber", "taxid",
                "iban", "bankaccount", "bankaccountnumber", "accountnumber", "streetaddress", "postaladdress",
                "homeaddress", "billingaddress", "shippingaddress", "mailingaddress", "postcode", "zipcode")
NOT_PII = re.compile(r"^(?:is|has|should|valid|validate|send|verify|verified|use|with|no|max|min|num|count)(?:_|[A-Z])")
MASKED = re.compile(r"(?:mask|redact|sanitiz|sanitis|hash|scrub|anonymi|pseudonym|obfuscat|truncat)\w*\s*\(\s*[\w$.]*$",
                    re.IGNORECASE)
# Profiler start: static labels come from the module's resolved resource values.
PROFILE_START = re.compile(r"\bpyroscope\s*\.\s*(?:configure|Start|init)\s*\(|\bPyroscope\s*\.\s*init\s*\(|\bPyroscopeAgent\s*\.\s*start\s*\("
                           r"|\bnew\s+Config\s*\.\s*Builder\s*\(|\bpyroscope\.Config\s*\{")
PROFILE_LITERAL = re.compile(
    r"""['"]?\b(service_version|service_namespace|deployment_environment_name|application_?[Nn]ame|applicationName|ApplicationName|appName|app_name)\b['"]?"""
    r"""\s*(?:[:=]|,)\s*['"]([^'"$%{}]+)['"]""")
PROFILE_RO = re.compile(r"""['"]?\b(telemetry_\w+|process_runtime_\w+|process_\w+|os_type|os_\w+|host_arch|host_type|host_id"""
                        r"""|container_name|container_image_\w+|service_instance_id|k8s_pod_uid|vcs_\w+)\b['"]?\s*(?:[:=]|,)""")
# Baggage: an allow-list of at most 8 keys, a 512-byte cap, set only in the module.
BAGGAGE_LIST = re.compile(r"\b\w*(?i:baggage)\w*(?i:allow|keys|whitelist|allowed|permitted)\w*\s*(?::\s*[\w<>\[\], .]+)?\s*[:=]")
BAGGAGE_SET = re.compile(r"\b(?:setBaggage|set_baggage|setEntry|baggage\.NewMember|propagation\.setBaggage|Baggage\.builder|"
                         r"createBaggage)\b")
BAGGAGE_BAD_KEY = re.compile(r"authori[sz]ation|token|password|secret|cookie|api[-_.]?key|^langfuse\.|^gen_ai\.|prompt", re.IGNORECASE)
BAGGAGE_LIMIT = 8
# Resource keys the standard never adds, and detectors that add them.
EXCLUDED_RESOURCE = re.compile(
    r"\bk8s\.pod\.uid\b|\bK8S_POD_UID\b|\bk8s\.node\.uid\b|\bservice\.instance\.id\b|\bhost\.id\b|\bdeployment\.id\b"
    r"|\bprocess\.(?:pid|parent_pid|command|command_line|command_args|executable\.\w+)\b|\bvcs\.[a-z][\w.]*"
    r"|\bATTR_(?:SERVICE_INSTANCE_ID|HOST_ID|K8S_POD_UID|PROCESS_PID|PROCESS_COMMAND(?:_LINE|_ARGS)?)\b")
RESOURCE_DETECTOR = re.compile(
    r"\b(?:processDetector|hostDetector|ProcessResourceDetector|HostResourceDetector|ProcessResourceProvider"
    r"|HostIdResourceProvider|HostResourceProvider|ProcessResource|HostIdResource)\b|\bresource\.With(?:Process\w*|Host(?:ID)?)\s*\("
    r"|OTEL_NODE_RESOURCE_DETECTORS[^\n]*\b(?:process|host|all)\b|OTEL_PYTHON_RESOURCE_DETECTORS?[^\n]*\b(?:process|host)\b")
EXCLUSION_CONTEXT = re.compile(r"disabl|exclud|drop|remov|deny|denied|block|ban|filter|delete|strip|omit|forbid|prohibit|never|reject",
                               re.IGNORECASE)

# Performance and security of the telemetry itself (references/overhead-and-security.md).
# Synchronous export: one network call per span or record on the request path.
SYNC_EXPORTER = re.compile(r"(?<![\w.])(?:new\s+)?Simple(?:Span|LogRecord)Processor\s*(?:\(|<|\.\s*create\s*\()"
                           r"|\b(?:sdktrace\.)?WithSyncer\s*\(|\bsdklog\.NewSimpleProcessor\s*\(")
# OTLP exporters, and the options that bound how long one export may take.
OTLP_EXPORTER = re.compile(
    r"(?<![\w.])(?:new\s+)?OTLP(?:Span|Trace|Metric|Log|Logs)Exporter\s*\("
    r"|\botlp(?:trace|metric|log)(?:grpc|http)\s*\.\s*New\s*\("
    r"|\bOtlp(?:Grpc|Http)(?:Span|Metric|LogRecord)Exporter\s*\.\s*builder\s*\(")
EXPORT_TIMEOUT = re.compile(r"\btimeout\s*[=:]|\btimeoutMillis\b|\bWithTimeout\s*\(|\.\s*setTimeout\s*\("
                            r"|OTEL_EXPORTER_OTLP_\w*TIMEOUT|\bexport_?timeout", re.IGNORECASE)
# Plaintext export: an insecure switch, or an http:// telemetry endpoint off this host.
INSECURE = re.compile(r"""\binsecure['"]?\s*[:=]\s*['"]?(?:true|True|1)\b|\bWithInsecure\s*\(|\.\s*usePlaintext\s*\(""")
TELEMETRY_URL_CONTEXT = re.compile(r"endpoint|otlp|otel|pyroscope|profil|langfuse|collector|exporter|server_?address"
                                   r"|ingest|tempo|loki|jaeger|zipkin|alloy", re.IGNORECASE)
HTTP_URL = re.compile(r"""http://(\[[^\]]*\]|\$\{[^}]*\}|[^/:\s'"`,;)]+)""")
LOCAL_HOST = re.compile(r"^(?:localhost|127(?:\.\d+){3}|\[?::1\]?|0\.0\.0\.0)$", re.IGNORECASE)
# A comment on the line or the line above that says the plaintext hop is private.
PRIVATE_HOP = re.compile(r"loopback|localhost|sidecar|in[- ]cluster|cluster[- ]local|network[- ]?polic|\bmesh\b|mtls|mutual tls"
                         r"|private hop|same pod|same host", re.IGNORECASE)
# A credential written into code or config: a header value or a Langfuse key, not a ${file:}/${env:} reference.
CREDENTIAL_HEADER = r"authorization|x-api-key|api[-_]?key|x-honeycomb-team|dd-api-key|x-scope-orgid-token|signoz-ingestion-key"
CREDENTIAL_PAIRS = (
    # 'Authorization': 'Bearer …' in code, or a quoted yml value
    re.compile(rf"""['"]?\b(?P<name>{CREDENTIAL_HEADER})['"]?\s*[:=]\s*(?P<q>['"])(?P<value>(?:(?!(?P=q)).)*)(?P=q)""", re.IGNORECASE),
    # OTEL_EXPORTER_OTLP_HEADERS=authorization=Basic …,x-api-key=…
    re.compile(rf"""\b(?P<name>{CREDENTIAL_HEADER})=(?P<value>(?:(?:Bearer|Basic|Token)(?:\s+|%20))?[^,'"\s]+)""", re.IGNORECASE),
    # an unquoted yml value
    re.compile(rf"""^\s*-?\s*(?P<name>{CREDENTIAL_HEADER})\s*:\s*(?P<value>[^\s'"#{{$][^#]*?)\s*$""", re.IGNORECASE),
)
LANGFUSE_KEY_LITERAL = re.compile(r"\b[sp]k-lf-[A-Za-z0-9-]{6,}")
CREDENTIAL_SCHEME = re.compile(r"^(?:Bearer|Basic|Token|Api-Token|ApiKey|DD-API-KEY)\s+", re.IGNORECASE)
PLACEHOLDER_VALUE = re.compile(r"^(?:x+|\*+|\.+|changeme|change-me|redacted|none|null|example\w*|your[-_\w]*|<[^>]*>|\w*placeholder\w*)$",
                               re.IGNORECASE)
# A metrics or profiling server open on every interface.
PUBLIC_BIND = re.compile(r"""(?<![\d.])0\.0\.0\.0\b""")
METRICS_CONTEXT = re.compile(r"metric|prometheus|prom_|scrape|profil|pprof|pyroscope|start_http_server|9464|9090|6060", re.IGNORECASE)
BIND_REASON = re.compile(r"remote|scrape|network|pod|container|cluster|prometheus|collector", re.IGNORECASE)
YML_PUBLIC_HOST = re.compile(r"""^\s*(?:host|address|bind|listen)\w*\s*:\s*['"]?0\.0\.0\.0\b""")
# Attribute value length limits: the standard caps values at 8192 characters.
ATTRIBUTE_LIMIT = re.compile(
    r"""\b\w*(?:attribute_?value_?length_?limit|max_?(?:span_?)?attribute_?(?:value_?)?length)\w*['"]?\s*\]?\s*[:=,(]\s*['"]?"""
    r"""(-?\d[\d_]*)""", re.IGNORECASE)
ATTRIBUTE_LIMIT_MAX = 8192
# A raised limit is accepted only together with genai.content.max_chars: equal to it, with a comment naming it.
LIMIT_REASON = re.compile(r"max_chars", re.IGNORECASE)
YML_MAX_CHARS = re.compile(r"^\s*max_chars\s*:\s*['\"]?(\d[\d_]*)")
# A log formatter that copies every LogRecord attribute or extra key instead of an allow-list.
RECORD_DICT = re.compile(r"\b(?:record|rec|r|log_record)\s*\.\s*__dict__\b|\bvars\s*\(\s*(?:record|rec|r|log_record)\s*\)")
ALLOW_MEMBERSHIP = re.compile(r"\bif\s+[\w\[\]'\"]+\s+in\s+(?!\(?\s*['\"])[\w.]+")
# A debug-level log call whose message is built before the level check.
DEBUG_CALL = re.compile(r"(?<![\w$])(?:self\.|this\.)?_{0,2}(?:logger|log|logging|LOG|LOGGER|Logger|slog|console)\s*\.\s*"
                        r"(?:debug|trace|Debug|Trace|fine|finer|finest|Debugf|Tracef)\s*\(")
STRING_LITERAL = r"""(?:(['"])(?:\\.|(?!\1).)*\1)"""
EAGER_MESSAGE = re.compile(
    r"""[fF][rR]?['"]|`[^`]*\$\{|(?:fmt\.Sprint\w*|String\.format|string\.Format|\w+\.format)\s*\("""
    rf"""|{STRING_LITERAL}\s*(?:%(?!=)|\.\s*format\s*\(|\+)|[\w.]+(?:\(\))?\s*\+\s*['"]""")
LEVEL_GUARD = re.compile(r"(?:\bif\b|\bwhen\b|&&|\band\b).*(?:is\w*Enabled\w*|\bEnabled\s*\(|isEnabledFor|getEffectiveLevel"
                         r"|\b[Ll]evel\w*\s*(?:<=|>=|==|<|>)|\bLevel(?:Debug|Trace)\b|\bDEBUG\b|\b[Dd]ebug_?[Ee]nabled)")
# Resource detectors whose output is filtered through an allow-list of keys.
ALLOW_NAME = re.compile(r"\b(\w*(?:ALLOW|Allow|allow|KEEP|Keep|keep|PERMIT|Permit|permit|WHITELIST|Whitelist|whitelist)\w*)\b")
OBS_YML = re.compile(r"(?:^|/)observability(?:\.[\w-]+)?\.ya?ml$")

# GenAI (references/signals/genai.md). Content keys: prompts, completions, tool arguments and results,
# retrieved documents, in the GenAI conventions and in Langfuse, OpenLLMetry and OpenInference.
GENAI_CONTENT_KEY = re.compile(
    r"""['"`](?:gen_ai\.(?:input\.messages|output\.messages|system_instructions|tool\.definitions|tool\.call\.(?:arguments|result)"""
    r"""|retrieval\.(?:documents|query\.text)|memory\.(?:query\.text|records)|evaluation\.explanation|prompt|completion)"""
    r"""|gen_ai\.(?:prompt|completion)\.\d+\.\w+|langfuse\.(?:observation|trace)\.(?:input|output)|traceloop\.entity\.(?:input|output)"""
    r"""|input\.value|output\.value|llm\.(?:input|output)_messages\.[\w.]+)['"`]"""
    r"""|\b(?:ATTR_)?GEN_AI_(?:INPUT_MESSAGES|OUTPUT_MESSAGES|SYSTEM_INSTRUCTIONS|TOOL_DEFINITIONS|TOOL_CALL_ARGUMENTS|TOOL_CALL_RESULT"""
    r"""|RETRIEVAL_DOCUMENTS|RETRIEVAL_QUERY_TEXT)\b|\bGenAI(?:InputMessages|OutputMessages|SystemInstructions|ToolCallArguments|ToolCallResult)\w*"""
)
LANGFUSE_IMPORT = re.compile(r"""^\s*(?:from\s+langfuse\b|import\s+langfuse\b)|['"](?:@langfuse/[\w-]+|langfuse(?:-node|-langchain|-vercel)?)['"]"""
                             r"""|\bcom\.langfuse\b""", re.MULTILINE)
# Langfuse SDK calls that record an input or output.
LANGFUSE_CALLS = (r"\b(?:update|update_current_span|update_current_generation|update_current_trace|start_span|start_generation"
                  r"|start_observation|start_as_current_span|start_as_current_generation|start_as_current_observation|create_event"
                  r"|updateActiveObservation|updateActiveTrace|startObservation|startActiveObservation)\s*\(")
LANGFUSE_IO_CALL = re.compile(LANGFUSE_CALLS)
LANGFUSE_IO = re.compile(LANGFUSE_CALLS + r"[^;]*?(?:\b(?:input|output)\s*[=:]|[{,]\s*(?:input|output)\s*[,}])")
# Content capture switched on: environment switches, SDK and framework options.
GENAI_CAPTURE_ON = re.compile(
    r"""\b(?:TRACELOOP_TRACE_CONTENT|OTEL_INSTRUMENTATION_GENAI_CAPTURE_MESSAGE_CONTENT|LANGFUSE_OBSERVE_DECORATOR_IO_CAPTURE_ENABLED"""
    r"""|otel\.instrumentation\.genai\.capture-message-content)['"]?\s*\]?\s*[=:,]\s*['"]?(?:true|1|yes|span_only|event_only|span_and_event)\b"""
    r"""|\bOPENINFERENCE_HIDE_\w+['"]?\s*\]?\s*[=:,]\s*['"]?(?:false|0|no)\b"""
    r"""|\b(?:traceContent|recordInputs|recordOutputs|captureInput|captureOutput)\s*:\s*true\b"""
    r"""|\b(?:capture_input|capture_output|trace_content|capture_message_content)\s*=\s*True\b""",
    re.IGNORECASE,
)
SPRING_CONFIG = re.compile(r"(^|/)(?:application|bootstrap)[\w-]*\.(?:properties|ya?ml)$")
# Langfuse's OTLP endpoint outside the module: a second span sender, in code or in the files that configure the app.
LANGFUSE_OTLP = re.compile(r"/api/public/otel\b")
SENDER_CONFIG = re.compile(r"(?:^|/)(?:\.env[^/]*|[^/]+\.env|Dockerfile[^/]*|[^/]+\.(?:ya?ml|json|properties|toml|ini|conf|cfg))$")
SPRING_AI_CAPTURE = re.compile(r"\b(?:log|include)-(?:prompt|completion|content|query-response)\s*[:=]\s*['\"]?true\b", re.IGNORECASE)
AI_SDK_TELEMETRY = re.compile(r"\bexperimental_telemetry\s*:\s*\{[^}]*\bisEnabled\s*:\s*true")
LANGFUSE_OBSERVE_JS = re.compile(r"(?<![\w$.@])observe\s*\(")
GENAI_CONTEXT = re.compile(r"\b(?:propagate_attributes|propagateAttributes|update_current_trace|updateActiveTrace)\s*\(")
GENAI_BAGGAGE = re.compile(r"\bas_baggage\s*=\s*True\b|\basBaggage\s*:\s*true\b")
# Langfuse keys (experiment, trace context) handed to an OpenTelemetry baggage call, as Langfuse's own recipes do.
BAGGAGE_CALL = re.compile(r"\b[Bb]aggage\w*\s*(?:\.|\(|\{)|\b(?:set_baggage|setBaggage|createBaggage|NewMember)\s*\(")
LANGFUSE_KEY_STRING = re.compile(r"""['"`]langfuse\.""")
LANGFUSE_SECRET = re.compile(r"\bsk-lf-[0-9a-f]{8}")
LANGFUSE_BROWSER_SECRET = re.compile(r"\bLANGFUSE_SECRET_KEY\b|\b(?:secretKey|secret_key)\b")
LANGFUSE_WEB = re.compile(r"""['"]@langfuse/(?:browser|web)['"]|\bnew\s+Langfuse(?:Web|Browser)\s*\(|\bcreateScore\s*\(|\bscore\s*\.\s*create\s*\(""")
# Names that usually hold prompts, completions, tool arguments or retrieved documents.
GENAI_CONTENT_NAME = re.compile(
    r"\b(prompts?|messages|completions?|choices|output_text|system_prompt|user_prompt|llm_response|model_response|chat_history"
    r"|tool_args|tool_arguments|tool_result|retrieved_docs|retrieved_documents)\b")
DERIVED_SIZE = re.compile(r"\b(?:len|count|size|sizeof)\s*\(\s*(?:[\w$.]+\.)?$")
# A project uses GenAI when a manifest names a model provider, an AI framework or an LLM-observability tool.
AI_MANIFEST = re.compile(
    r"""(?<![\w@/-])(?:openai|anthropic|langchain[\w-]*|langgraph|llama[-_]?index[\w-]*|llamaindex|google-genai|google-generativeai"""
    r"""|mistralai|cohere(?:-ai)?|groq(?:-sdk)?|ollama|litellm|langfuse[\w-]*|traceloop-sdk|openinference[\w-]*|spring-ai[\w-]*"""
    r"""|langchain4j[\w-]*|semantic-kernel|pydantic-ai[\w-]*|crewai|dspy(?:-ai)?|haystack-ai|smolagents|openai-agents"""
    r"""|openai-java|anthropic-java)(?![\w-])|@(?:ai-sdk|langfuse|traceloop|arizeai|langchain|mistralai|openai|anthropic-ai)/|@google/genai"""
    r"""|"ai"\s*:\s*"|github\.com/(?:openai/openai-go|anthropics/anthropic-sdk-go|sashabaranov/go-openai|tmc/langchaingo|firebase/genkit)""")
# Instrumentations by the AI library they instrument; two families on one library record every call twice.
OPENLLMETRY_ALL = r"|traceloop-sdk|@traceloop/node-server-sdk"
GENAI_INSTRUMENTATION = {
    "openai": {
        "OpenTelemetry": r"opentelemetry-instrumentation-openai-v2|@opentelemetry/instrumentation-openai",
        "OpenLLMetry": r"opentelemetry-instrumentation-openai(?![\w-])|@traceloop/instrumentation-openai" + OPENLLMETRY_ALL,
        "OpenInference": r"openinference-instrumentation-openai(?![\w-])|@arizeai/openinference-instrumentation-openai",
        "the Langfuse OpenAI wrapper": r"^\s*from\s+langfuse\.openai\s+import|^\s*import\s+langfuse\.openai\b|\bobserveOpenAI\s*\(|['\"]@langfuse/openai['\"]",
    },
    "anthropic": {
        "OpenLLMetry": r"opentelemetry-instrumentation-anthropic|@traceloop/instrumentation-anthropic" + OPENLLMETRY_ALL,
        "OpenInference": r"openinference-instrumentation-anthropic|@arizeai/openinference-instrumentation-anthropic",
    },
    "langchain": {
        "OpenLLMetry": r"opentelemetry-instrumentation-langchain|@traceloop/instrumentation-langchain" + OPENLLMETRY_ALL,
        "OpenInference": r"openinference-instrumentation-langchain|@arizeai/openinference-instrumentation-langchain",
        "the Langfuse callback handler": r"^\s*from\s+langfuse\.(?:langchain|callback)\s+import|['\"]@langfuse/langchain['\"]|['\"]langfuse-langchain['\"]",
    },
    "llamaindex": {
        "OpenLLMetry": r"opentelemetry-instrumentation-llamaindex|@traceloop/instrumentation-llamaindex" + OPENLLMETRY_ALL,
        "OpenInference": r"openinference-instrumentation-llama[-_]index|@arizeai/openinference-instrumentation-llamaindex",
    },
}


@dataclass
class Finding:
    severity: str
    rule: str
    location: str
    message: str


ANGULAR_IMPORT = re.compile(r"""from\s+['"]@angular/""")


def is_browser_file(rel: str, lines: list[str]) -> bool:
    """Code that runs in the page: a `'use client'` module, `instrumentation-client`, a `.vue` or `.svelte`
    component, a `*.client.*` module (SvelteKit `hooks.client`, Nuxt client plugins), Angular app code or public/."""
    first = next((line.strip() for line in lines if line.strip()), "")
    parts = rel.split("/")
    name = parts[-1]
    if ".server." in name or name.startswith(("+server.", "server.")) or "server" in parts[:-1]:
        return False
    return (first.strip(";") in ("'use client'", '"use client"') or name.startswith("instrumentation-client.")
            or name.endswith((".vue", ".svelte")) or ".client." in name
            or any(ANGULAR_IMPORT.search(line) for line in lines[:40])
            or "public" in parts[:-1])


def browser_findings(line: str, loc: str) -> list[Finding]:
    found = []
    if BROWSER_KEY.search(line) or PUBLIC_KEY.search(line):
        found.append(Finding("P0", "browser-key", loc, "telemetry backend key or key header in code that runs in "
                             "the page; telemetry keys stay on the server"))
    return found


FARO_IMPORT = re.compile(r"""@grafana/faro-|\binitializeFaro\s*\(""")
FARO_PROPAGATE_ALL = re.compile(r"""propagateTraceHeaderCorsUrls\s*:\s*\[[^\]]*(/\.[*+]/|['"`]\*['"`]|RegExp\(\s*['"`]\.[*+]|/\^?https?:?\\?/?\\?/?\.[*+])""")
FARO_SET_USER = re.compile(r"""\bsetUser\s*\(|\buser\s*:\s*\{""")
FARO_USER_PII = re.compile(r"""\b(email|username|fullName|full_name|phone)\s*[:,}]""")
FARO_CONSOLE_ALL = re.compile(r"""disabledLevels\s*:\s*\[\s*\]""")
FARO_BUILD_ENV = re.compile(r"""\b(url|apiKey|endpoint|environment|logsURL|tracesURL)\s*:\s*(import\.meta\.env\.|process\.env\.(NEXT_PUBLIC_|REACT_APP_|VITE_|NUXT_PUBLIC_|PUBLIC_))""")
FARO_API_KEY = re.compile(r"""\bapiKey\s*:\s*['"`][^'"`]+['"`]""")
FARO_GEO = re.compile(r"""trackGeolocation\s*:\s*true""")
FARO_ALL_CHANGES = re.compile(r"""reportAllChanges\s*:\s*true""")
PUBLIC_SOURCEMAPS = re.compile(r"""\bsourcemap\s*:\s*(true|['"]inline['"])|\bdevtool\s*:\s*['"](inline-)?source-map['"]|productionBrowserSourceMaps\s*:\s*true""")
BUNDLER_CONFIG = re.compile(r"(^|/)(vite|webpack|next|rollup|rspack|astro|svelte|nuxt)\.config\.[cm]?[jt]s$|(^|/)angular\.json$")
RUM_PROXY = re.compile(r"^\s*transport:\s*['\"]?proxy\b")
FARO_PUSH_URL = re.compile(r"""\bpush(Event|Log|Measurement|Error)\s*\(.*\b(location\.href|document\.URL|window\.location(?!\.pathname))""")
NGINX_QUERY_LOG = re.compile(r"""^\s*access_log\s+\S+\s+combined\b|^\s*access_log\s+\S+\s*;|\$request_uri\b|\$request(?![_a-z])|\$args\b|\$query_string\b""")
NGINX_CONF = re.compile(r"(^|/)(nginx[^/]*\.conf|[^/]*\.conf\.template|default\.conf|conf\.d/[^/]+\.conf)$")


def faro_findings(rel: str, lines: list[str], proxy_mode: bool) -> list[Finding]:
    """Grafana Faro set-up rules (references/signals/rum.md); the file already imports or starts Faro."""
    found = []
    has_init, has_scrub = False, False
    for lineno, raw in enumerate(lines, 1):
        if IGNORE_RE.search(raw) or raw.lstrip().startswith(("//", "*")):
            continue
        loc, line = f"{rel}:{lineno}", strip_comment(raw)
        text = braced(lines, lineno - 1)
        has_init = has_init or "initializeFaro(" in line
        has_scrub = has_scrub or "beforeSend" in line
        if "propagateTraceHeaderCorsUrls" in line and FARO_PROPAGATE_ALL.search(text):
            found.append(Finding("P1", "faro-propagate-all", loc, "match-all propagateTraceHeaderCorsUrls sends traceparent "
                                 "and CORS preflights to every host the page calls; list the exact API origins"))
        if FARO_SET_USER.search(line) and FARO_USER_PII.search(blank_strings(text)):
            found.append(Finding("P1", "faro-user-pii", loc, "email, username, name or phone given to Faro's user meta; "
                                 "Faro copies it onto every record and span. Pass the opaque user id only"))
        if FARO_CONSOLE_ALL.search(line):
            found.append(Finding("P2", "faro-console-all", loc, "every console level captured, debug and log included; "
                                 "keep trace, debug and log disabled"))
        if FARO_BUILD_ENV.search(line):
            found.append(Finding("P2", "faro-build-env-config", loc, "Faro endpoint, key or environment from a build-time "
                                 "public variable; read them from the runtime config so one build serves every environment"))
        if FARO_API_KEY.search(line):
            if proxy_mode:
                found.append(Finding("P0", "faro-key-in-bundle", loc, "receiver key in page code while rum.transport is "
                                     "proxy; the proxy adds the key and the page holds none"))
            else:
                found.append(Finding("P2", "faro-key-literal", loc, "receiver key as a literal in page code; read the "
                                     "public key from the runtime config"))
        if FARO_GEO.search(line):
            found.append(Finding("P2", "faro-geolocation", loc, "geolocation tracking on; it is Grafana Cloud only and "
                                 "personal data, keep trackGeolocation false"))
        if FARO_PUSH_URL.search(line):
            found.append(Finding("P1", "faro-event-raw-url", loc, "the page URL (with its query and fragment) sent in a "
                                 "Faro event or log; send the route template, and let the scrubber handle page_url"))
        if FARO_ALL_CHANGES.search(line):
            found.append(Finding("P2", "faro-report-all-changes", loc, "reportAllChanges sends every web-vital change; "
                                 "a debugging setting, off in production"))
    if has_init and not has_scrub:
        first = next(i for i, raw in enumerate(lines, 1) if "initializeFaro(" in raw)
        found.append(Finding("P2", "faro-no-scrub", f"{rel}:{first}", "Faro starts without a beforeSend scrubber; URLs "
                             "with query values, emails and tokens in messages leave the page as they are"))
    return found


def _in_module(rel: str, modules: list[str]) -> bool:
    return any(rel == m or rel.startswith(m.rstrip("/") + "/") for m in modules)


def scan_line(line: str, rel: str, lineno: int, in_module: bool) -> list[Finding]:
    found = []
    loc = f"{rel}:{lineno}"
    attr = ATTR_SINK.search(line) is not None
    log = LOG_SINK.search(line) is not None or PRINT_SINK.search(line) is not None
    baggage = BAGGAGE_SINK.search(line) is not None
    sink = attr or log or baggage

    if sink and SECRET_READ.search(line):
        found.append(Finding("P0", "secret-capture", loc, "secret-like header or field passed to a span, log or baggage call"))
    body = whole_body(line)
    if attr and body:
        found.append(Finding("P1", "body-in-span", loc, "request or response body set on a span; keep body sizes and approved summaries only"))
    if log and body:
        found.append(Finding("P1", "body-logged", loc, "request or response body logged as-is; log allow-listed fields only"))
    elif log and HEADERS_WHOLE.search(line):
        found.append(Finding("P1", "raw-headers-logged", loc, "whole headers logged; log allow-listed headers only"))
    if METRIC_SINK.search(line) and ID_KEY.search(line):
        found.append(Finding("P1", "id-metric-label", loc, "ID-like value used as a metric attribute; keep IDs on spans and logs"))
    if PROFILE_SINK.search(line) and ID_KEY.search(line):
        found.append(Finding("P1", "id-profile-label", loc, "ID-like value used as a profiling label; use bounded labels such as controller"))
    if baggage and ID_KEY.search(line) is None and SECRET_READ.search(line) is None and re.search(r"email|password|token", line, re.IGNORECASE):
        found.append(Finding("P1", "sensitive-baggage", loc, "sensitive value written to baggage; baggage is allow-list only"))
    if not in_module and SDK_SETUP.search(line):
        found.append(Finding("P1", "sdk-outside-module", loc, "SDK, provider, profiler, Langfuse client or GenAI "
                             "instrumentation set up outside the observability module"))
    if not in_module and OTEL_ENV.search(line):
        found.append(Finding("P2", "otel-env-outside-loader", loc, "OTEL_*, LANGFUSE_*, TRACELOOP_* or OPENINFERENCE_* "
                             "setting read or written outside the observability module"))
    if (attr or log) and XFF.search(line):
        found.append(Finding("P2", "raw-xff", loc, "X-Forwarded-For captured; record the resolved client.address instead"))
    query = QUERY_SECRET.search(line)
    if query:
        key = next(g for g in query.groups() if g)
        found.append(Finding("P2", "secret-in-query", loc, f"secret sent in a URL query string ({key}): url.full, url.query and "
                             "access logs carry it unless the instrumentation redacts query values"))
    return found


def genai_findings(lines: list[str], index: int, loc: str, in_module: bool, browser: bool, js: bool, langfuse_file: bool,
                   ai_project: bool) -> list[Finding]:
    """GenAI rules for one line: content outside the content policy, capture switches, Langfuse keys. A call starts
    on this line and its arguments may continue below; a switch or a key is read from the line alone, so a statement
    spread over several lines is reported once."""
    found = []
    line = strip_comment(lines[index])
    own = blank_strings(line)
    calls = (ATTR_SINK, LOG_SINK, PRINT_SINK, GENAI_CONTEXT, LANGFUSE_OBSERVE_JS, LANGFUSE_IO_CALL)
    stmt = statement(lines, index) if any(p.search(own) for p in calls) else line
    code = blank_strings(stmt)
    if LANGFUSE_SECRET.search(line):
        found.append(Finding("P0", "langfuse-key", loc, "Langfuse secret key in source; load it from a mounted file "
                             "through observability.yml"))
    if browser and langfuse_file and LANGFUSE_BROWSER_SECRET.search(own):
        found.append(Finding("P0", "langfuse-key", loc, "Langfuse secret key in code that runs in the page; it reads and "
                             "writes the whole project, so keep it server-side"))
    if browser and LANGFUSE_WEB.search(line):
        found.append(Finding("P2", "genai-browser-score", loc, "scores sent from the browser with the public key can be "
                             "forged for any trace; post feedback to the app's API and score server-side"))
    if GENAI_BAGGAGE.search(own) or (BAGGAGE_CALL.search(own) and LANGFUSE_KEY_STRING.search(statement(lines, index))):
        found.append(Finding("P1", "genai-baggage", loc, "AI trace context put into baggage, which every outbound call "
                             "carries, model providers included; the module's stamping processor keeps it in the process"))
    if ai_project and (LOG_SINK.search(own) or PRINT_SINK.search(own)):
        name = content_value(code_refs(stmt))
        if name:
            found.append(Finding("P1", "genai-content-logged", loc, f"'{name}' looks like prompt or completion content and "
                                 "reaches a log or print call; log sizes, counts and ids, never the content"))
    if in_module:
        return found
    if ATTR_SINK.search(own) and GENAI_CONTENT_KEY.search(stmt):
        found.append(Finding("P1", "genai-content-outside-module", loc, "prompt or completion content set on a span outside "
                             "the observability module; record it through the module's content helper so the content "
                             "policy applies"))
    if langfuse_file and LANGFUSE_IO_CALL.search(own) and LANGFUSE_IO.search(code):
        found.append(Finding("P1", "genai-content-unguarded", loc, "Langfuse input or output set outside the observability "
                             "module; pass content through the module's content helper so mode, redaction, sampling and "
                             "the size cap apply"))
    telemetry = braced(lines, index) if "experimental_telemetry" in own else ""
    if GENAI_CAPTURE_ON.search(line):
        found.append(Finding("P1", "genai-capture-on", loc, "content capture switched on in code; the loader sets every "
                             "capture switch from genai.content.capture"))
    elif AI_SDK_TELEMETRY.search(telemetry) and not re.search(r"\brecord(?:Inputs|Outputs)\b", telemetry):
        found.append(Finding("P2", "genai-capture-default", loc, "AI SDK telemetry on with recordInputs and recordOutputs "
                             "left at their default (on); pass the content policy's values from the module"))
    elif js and langfuse_file and LANGFUSE_OBSERVE_JS.search(own) and not re.search(r"\bcapture(?:Input|Output)\b", code):
        found.append(Finding("P2", "genai-capture-default", loc, "Langfuse observe() records arguments and return values by "
                             "default; set captureInput and captureOutput from the content policy"))
    if langfuse_file and GENAI_CONTEXT.search(own):
        found.append(Finding("P2", "genai-context-outside-module", loc, "AI trace context set outside the observability "
                             "module; declare it once with the module's feature helper, which stamps the Langfuse keys "
                             "without writing session.id or baggage"))
    return found


def pii_named(refs: str) -> str | None:
    """The first value on the line named like personal data, used as a value and not through the masking helper."""
    for match in re.finditer(r"[A-Za-z_$][\w$]*(?:\s*\.\s*[A-Za-z_$][\w$]*)*", refs):
        chain = re.split(r"\s*\.\s*", match.group(0))
        last = chain[-1].lstrip("_$")
        if re.fullmatch(r"[A-Z0-9_]+", last) or NOT_PII.match(last):
            continue  # constants such as ErrorCode.INVALID_EMAIL, and tests such as isEmail
        if not last.lower().replace("_", "").endswith(PII_SUFFIXES):
            continue
        before, after = refs[:match.start()], refs[match.end():]
        if re.match(r"\s*\(", after) or MASKED.search(before) or DERIVED_BEFORE.search(before) \
                or DERIVED_SIZE.search(before) or DERIVED_AFTER.match(after) \
                or re.match(r"\s*(?:\?\.|\.)\s*(?:length|size|count)\b", after):
            continue
        return last
    return None


def profile_region(lines: list[str], index: int) -> str:
    """A profiler start call with its configuration: the statement and any object literal opened in it."""
    return statement(lines, index, limit=20) + " " + braced(lines, index, limit=20)


def data_model_findings(lines: list[str], index: int, loc: str, in_module: bool) -> list[Finding]:
    """Data-model rules for one line (references/data-model.md); multi-line calls are read as one statement."""
    found: list[Finding] = []
    line = strip_comment(lines[index])
    if not line.strip():
        return found
    own = blank_strings(line)
    sink = is_sink(own) or ERROR_SINK.search(own) is not None or LOG_CONTEXT.search(own) is not None
    stmt = statement(lines, index) if sink or METRIC_DECL.search(own) else line
    if IDENTITY_KEY.search(stmt) and IDENTITY_READ.search(stmt):
        found.append(Finding("P1", "identity-from-header", loc, "identity row set from a raw client header, cookie or "
                             "query value; take it from the trusted auth or tenant middleware after authentication"))
    if REQUEST_ID_ATTR.search(line) or (sink and REQUEST_ID_FIELD.search(stmt)):
        found.append(Finding("P2", "request-id-attribute", loc, "request.id attribute or log field; the standard uses "
                             "correlation.id only"))
    if CODE_NAMESPACE.search(line):
        found.append(Finding("P2", "code-namespace", loc, "code.namespace is deprecated; code.function.name carries the "
                             "fully qualified name"))
    if METRIC_DECL.search(own) and METRIC_ID_LABEL.search(stmt) and not METRIC_SINK.search(own):
        found.append(Finding("P1", "id-metric-label", loc, "metric declared with an ID, URL, path, SQL or email label; "
                             "keep labels bounded and IDs on spans and logs"))
    if PROFILE_START.search(own):
        region = profile_region(lines, index)
        literal = PROFILE_LITERAL.search(region)
        if literal:
            found.append(Finding("P2", "profile-label-mismatch", loc, f"profiler started with a literal {literal.group(1)}; "
                                 "static labels come from the module's resolved resource values"))
        ro = PROFILE_RO.search(region)
        if ro:
            found.append(Finding("P2", "profile-label-mismatch", loc, f"static profile label {ro.group(1)} is an R/O or "
                                 "excluded resource row; profiles carry the resource's M and C rows only"))
    if not in_module and BAGGAGE_SET.search(own):
        found.append(Finding("P2", "baggage-over-limit", loc, "baggage set outside the observability module; the module's "
                             "middleware writes the allow-listed keys"))
    if (EXCLUDED_RESOURCE.search(line) or RESOURCE_DETECTOR.search(line)) and not EXCLUSION_CONTEXT.search(line) \
            and not (RESOURCE_DETECTOR.search(line) and filtered_detector(lines, index)):
        found.append(Finding("P2", "resource-excluded-key", loc, "resource key the standard never adds (pod or node UID, "
                             "instance or host id, process id or command line, VCS data, release id), or a process or "
                             "host detector; build the resource from the yml and the platform rows only"))
    if not in_module and (LOG_SINK.search(own) or PRINT_SINK.search(own) or ATTR_SINK.search(own)
                          or ERROR_SINK.search(own)):
        name = pii_named(code_refs(stmt))
        if name:
            found.append(Finding("P1", "pii-unmasked", loc, f"'{name}' looks like personal data and reaches a log, print, "
                                 "span or error message without the masking helper"))
    return found


def yml_strip(line: str) -> str:
    """A config line without its `#` comment; `//` in a URL is not a comment here."""
    cut = re.search(r"(?:^|\s)#", blank_strings(line))
    return line[:cut.start()].rstrip() if cut else line


def comment_says(lines: list[str], index: int, pattern: re.Pattern, strip=None) -> bool:
    """A comment on the line, or on the line above, that matches: the reason a finding is accepted."""
    strip = strip or strip_comment
    for offset in (0, 1):
        if index - offset < 0:
            break
        raw = lines[index - offset]
        code = strip(raw)
        comment = raw if not code.strip() else raw[len(code):]
        if pattern.search(comment):
            return True
    return False


def credential_literal(line: str, skip_langfuse_secret: bool = False) -> str | None:
    """The header or key named by a literal credential on the line."""
    for match in (m for pattern in CREDENTIAL_PAIRS for m in pattern.finditer(line)):
        name = match.group("name")
        value = CREDENTIAL_SCHEME.sub("", re.sub(r"%20", " ", match.group("value")).strip())
        if not value or re.search(r"\$\{|\{|%[sd(]|\$[A-Za-z_]", value) or PLACEHOLDER_VALUE.match(value):
            continue
        if len(value) >= 8 and re.fullmatch(r"[A-Za-z0-9+/=_.:~-]+", value):
            return name
    for match in LANGFUSE_KEY_LITERAL.finditer(line):
        if not (skip_langfuse_secret and match.group(0).startswith("sk-")):
            return match.group(0)[:6]
    return None


def plaintext_endpoint(lines: list[str], index: int, code: str, strip=None) -> str | None:
    """An insecure switch or an http:// telemetry endpoint to another host, without a private-hop comment."""
    strip = strip or strip_comment
    if comment_says(lines, index, PRIVATE_HOP, strip):
        return None
    if INSECURE.search(code):
        window = " ".join(strip(line) for line in lines[max(0, index - 5):index + 6])
        hosts = [m.group(1) for m in HTTP_URL.finditer(window)] + re.findall(r"""['"]((?:localhost|127\.0\.0\.1)(?::\d+)?)['"]""", window)
        if not any(_local(h.split(":")[0] if not h.startswith(("[", "$")) else h) for h in hosts):
            return "insecure (plaintext) export switched on"
    if TELEMETRY_URL_CONTEXT.search(code):
        for match in HTTP_URL.finditer(code):
            host = match.group(1)
            if not _local(host):
                return f"plaintext http:// telemetry endpoint {host}"
    return None


def _local(host: str) -> bool:
    """Loopback, or a ${...} placeholder whose default is loopback; a placeholder without a default can't be judged."""
    if host.startswith("${"):
        default = re.search(r":-([^}]*)\}", host) or re.search(r"\$\{[^}]*?:([^}:]*)\}$", host)
        return default is None or LOCAL_HOST.match(default.group(1).split(":")[0].strip("[]") or "localhost") is not None
    return LOCAL_HOST.match(host.strip("[]")) is not None or LOCAL_HOST.match(host) is not None


def attribute_limit(line: str) -> int | None:
    for match in ATTRIBUTE_LIMIT.finditer(line):
        value = int(match.group(1).replace("_", ""))
        if value > ATTRIBUTE_LIMIT_MAX:
            return value
    return None


def limit_accepted(limit: int, max_chars: int | None, lines: list[str], index: int, strip=None) -> bool:
    """A raised limit equal to the yml's genai.content.max_chars, with a comment naming max_chars: the recorded exception."""
    return max_chars is not None and limit == max_chars and comment_says(lines, index, LIMIT_REASON, strip)


def content_max_chars(files: list[tuple[Path, str]]) -> int | None:
    """genai.content.max_chars from the project's observability yml, when one sets it."""
    for path, rel in files:
        if not OBS_YML.search(rel):
            continue
        try:
            for raw in path.read_text(encoding="utf-8", errors="ignore").splitlines():
                match = YML_MAX_CHARS.search(raw)
                if match:
                    return int(match.group(1).replace("_", ""))
        except OSError:
            continue
    return None


def eager_debug(lines: list[str], index: int, line: str) -> bool:
    call = DEBUG_CALL.search(blank_strings(line))
    if not call:
        return False
    first = statement(lines, index)[call.end():].lstrip() if call.end() >= len(line.rstrip()) else line[call.end():].lstrip()
    if not EAGER_MESSAGE.match(first):
        return False
    above = next((strip_comment(prev) for prev in reversed(lines[max(0, index - 3):index]) if strip_comment(prev).strip()), "")
    return not (LEVEL_GUARD.search(above) or LEVEL_GUARD.search(line[:call.start()]))


def safety_findings(lines: list[str], index: int, loc: str, rel: str, in_module: bool,
                    max_chars: int | None = None) -> list[Finding]:
    """Rules that keep the telemetry itself cheap and safe (references/overhead-and-security.md)."""
    found: list[Finding] = []
    line = strip_comment(lines[index])
    if not line.strip():
        return found
    own = blank_strings(line)
    if SYNC_EXPORTER.search(own) and not TEST_RE.search(rel):
        found.append(Finding("P1", "sync-exporter", loc, "synchronous span or log processor: every span or record is "
                             "exported on the request path; use the batch processor"))
    if in_module and OTLP_EXPORTER.search(own):
        window = "\n".join(strip_comment(l) for l in lines[max(0, index - 15):index + 16])
        if not EXPORT_TIMEOUT.search(window):
            found.append(Finding("P2", "exporter-no-timeout", loc, "OTLP exporter built without a timeout option; set it "
                                 "from the yml so a slow backend can't hold a flush or shutdown for the SDK default"))
    plain = plaintext_endpoint(lines, index, line)
    if plain:
        found.append(Finding("P1", "plaintext-remote-endpoint", loc, f"{plain}; telemetry carries identity rows and "
                             "credentials, so use TLS off this host, or say on the line why the hop is private "
                             "(sidecar, in-cluster with a NetworkPolicy, mesh mTLS)"))
    key = credential_literal(line, skip_langfuse_secret=LANGFUSE_SECRET.search(line) is not None)
    if key:
        found.append(Finding("P0", "telemetry-credential-literal", loc, f"literal {key} credential; reference a mounted "
                             "file or the environment (${file:...} or ${env:...}) instead"))
    if PUBLIC_BIND.search(line) and (in_module or METRICS_CONTEXT.search(line)) and not comment_says(lines, index, BIND_REASON):
        found.append(Finding("P2", "metrics-public-bind", loc, "metrics or profiling server bound to 0.0.0.0 with no "
                             "comment saying who scrapes it from the network; bind 127.0.0.1 unless it must be remote"))
    limit = attribute_limit(line)
    if limit and not limit_accepted(limit, max_chars, lines, index):
        found.append(Finding("P2", "attribute-limit-raised", loc, f"attribute value length limit {limit} is above "
                             f"{ATTRIBUTE_LIMIT_MAX}; long values cost memory, export size and can carry payloads. A "
                             "project that needs longer AI content sets it equal to genai.content.max_chars, with a "
                             "comment naming max_chars, and records the exception in the report"))
    if in_module and RECORD_DICT.search(own):
        stmt = statement(lines, index, limit=6)
        if not ALLOW_MEMBERSHIP.search(blank_strings(stmt)):
            found.append(Finding("P1", "log-extras-passthrough", loc, "log formatter copies every LogRecord attribute or "
                                 "extra key; copy an allow-list of fields so a stray extra can't leak a secret or body"))
    if in_module and eager_debug(lines, index, line):
        found.append(Finding("P2", "eager-debug-format", loc, "debug message formatted before the level check; pass "
                             "arguments to the logger or guard the call with an isEnabledFor/level check"))
    return found


def config_safety_findings(rel: str, lines: list[str], max_chars: int | None = None) -> list[Finding]:
    """The same rules for config files: credentials and attribute limits in any, endpoints and binds in observability.yml."""
    found: list[Finding] = []
    obs = OBS_YML.search(rel) is not None
    for index, raw in enumerate(lines):
        if IGNORE_RE.search(raw) or raw.lstrip().startswith(("#", "//", "!", "*")):
            continue
        line, loc = yml_strip(raw), f"{rel}:{index + 1}"
        key = credential_literal(line)
        if key:
            found.append(Finding("P0", "telemetry-credential-literal", loc, f"literal {key} credential; reference a "
                                 "mounted file or the environment (${file:...} or ${env:...}) instead"))
        limit = attribute_limit(line)
        if limit and not limit_accepted(limit, max_chars, lines, index, yml_strip):
            found.append(Finding("P2", "attribute-limit-raised", loc, f"attribute value length limit {limit} is above "
                                 f"{ATTRIBUTE_LIMIT_MAX} and not equal to genai.content.max_chars with a comment naming it"))
        if not obs:
            continue
        plain = plaintext_endpoint(lines, index, line, yml_strip)
        if plain:
            found.append(Finding("P1", "plaintext-remote-endpoint", loc, f"{plain}; use TLS off this host, or say on the "
                                 "line why the hop is private (sidecar, in-cluster with a NetworkPolicy, mesh mTLS)"))
        if YML_PUBLIC_HOST.search(line) and not comment_says(lines, index, BIND_REASON, yml_strip):
            found.append(Finding("P2", "metrics-public-bind", loc, "server bound to 0.0.0.0 with no comment saying who "
                                 "scrapes it from the network; bind 127.0.0.1 unless it must be remote"))
    return found


def filtered_detector(lines: list[str], index: int) -> bool:
    """A process or host detector whose output passes an allow-list of resource keys in the same file."""
    window = "\n".join(strip_comment(l) for l in lines[max(0, index - 40):index + 41])
    for name in set(ALLOW_NAME.findall(blank_strings(window))):
        if re.search(rf"\bin\s+(?:self\.|this\.)?{re.escape(name)}\b|\b{re.escape(name)}\s*\.\s*(?:includes|has|contains"
                     rf"|containsKey|get)\s*\(|\b{re.escape(name)}\s*\[|Contains\s*\(\s*{re.escape(name)}\b", window):
            return True
    return False


def baggage_list(lines: list[str], index: int, loc: str) -> list[Finding]:
    """A baggage allow-list: at most 8 keys, none a secret, authorisation or AI context, with a 512-byte cap nearby."""
    line = strip_comment(lines[index])
    if not BAGGAGE_LIST.search(blank_strings(line)):
        return []
    region = braced(lines, index, limit=30) if "{" in line else ""
    text = line
    depth = 0
    for offset in range(min(30, len(lines) - index)):
        code = blank_strings(strip_comment(lines[index + offset]))
        if offset:
            text += " " + strip_comment(lines[index + offset])
        depth += code.count("[") + code.count("(") - code.count("]") - code.count(")")
        if depth <= 0 and offset:
            break
        if depth <= 0 and not offset and "[" not in code and "(" not in code:
            break
    keys = sorted({m.group(2) for m in STRING.finditer(text + " " + region) if m.group(2)})
    found = []
    if len(keys) > BAGGAGE_LIMIT:
        found.append(Finding("P2", "baggage-over-limit", loc, f"baggage allow-list with {len(keys)} keys; at most "
                             f"{BAGGAGE_LIMIT}"))
    bad = [k for k in keys if BAGGAGE_BAD_KEY.search(k)]
    if bad:
        found.append(Finding("P2", "baggage-over-limit", loc, f"baggage allow-list names {', '.join(bad)}; never secrets, "
                             "authorisation or AI context"))
    window = "\n".join(lines[max(0, index - 30):index + 60])
    if keys and not re.search(r"\b512\b", window):
        found.append(Finding("P2", "baggage-over-limit", loc, "baggage allow-list without the 512-byte cap nearby"))
    return found


def braced(lines: list[str], index: int, limit: int = 8) -> str:
    """The line and the lines after it until its braces close: an object literal written over several lines."""
    text, depth = "", 0
    for offset in range(min(limit, len(lines) - index)):
        code = blank_strings(strip_comment(lines[index + offset]))
        text += " " + code.strip()
        depth += code.count("{") - code.count("}")
        if depth <= 0:
            break
    return text


def content_value(refs: str) -> str | None:
    """The first name on the line that usually holds AI content, used as a value (not its length or count)."""
    for match in GENAI_CONTENT_NAME.finditer(refs):
        before, after = refs[:match.start()], refs[match.end():]
        if DERIVED_BEFORE.search(before) or DERIVED_SIZE.search(before) \
                or re.match(r"\s*(?:\?\.|\.)\s*(?:length|size|count)\b|\s*_(?:count|len|length|size)\b", after):
            continue
        return match.group(1)
    return None


DERIVED_CALL = re.compile(r"\b(?:Object\.keys|len|size|byteLength|bool|Boolean|typeof|isEmpty)\s*\(?\s*$")


def whole_body(line: str) -> bool:
    """A body reference that passes the whole body on, not its keys, size or type."""
    return any(not DERIVED_CALL.search(line[:m.start()]) for m in BODY_REF.finditer(line))


def is_sink(line: str) -> bool:
    return any(p.search(line) for p in (ATTR_SINK, LOG_SINK, PRINT_SINK, BAGGAGE_SINK))


STRING = re.compile(r"""(['"`])((?:\\.|(?!\1).)*)\1""")
LEADING_CHAIN = re.compile(r"""^(?:[\w$.?\[\]]|\(\))*(?:\b(?:get|getHeader|getHeaderString|GetHeader|getFirst|header|Get)\s*\(\s*[\w.]*)?$""")
NOT_THE_VALUE = re.compile(r"\s\?\s|[=!]==?|\s(?:<|>|<=|>=)\s|\b(?:and|or|not|is|in)\b")
DERIVED_BEFORE = re.compile(r"(?:!!|!|\btypeof\s+|\b(?:bool|len|Boolean|isset|isEmpty|hasText)\s*\(\s*|\b(?:redact|mask|sanitiz|hash|scrub|fingerprint|truncat)\w*\s*\(\s*)$", re.IGNORECASE)
DERIVED_AFTER = re.compile(r"^\s*(?:!=|==|\bis\b|\?\.?(?:length|size)|\.\s*(?:length|size|isEmpty|isPresent)\b|\?\s|:(?!:)|=(?!=))")


def blank_strings(line: str) -> str:
    """The line with string contents blanked (same length) so words inside messages never match."""
    return STRING.sub(lambda m: m.group(1) + " " * len(m.group(2)) + m.group(1), line)


def code_refs(line: str) -> str:
    """Code on the line plus the expressions inside template and f-string interpolations."""
    interpolated = re.findall(r"\$\{([^}]*)\}", line)
    if re.search(r"\bf['\"]", line):
        interpolated += re.findall(r"(?<![$\w])\{([^{}:!]+)[^{}]*\}", line)
    # Values read by key, `body["email"]` or `data.get('card_number')`, are named by that key.
    keys = re.findall(r"(?:\[\s*|\.get\(\s*)['\"]([A-Za-z_]\w*)['\"]", line)
    return blank_strings(line) + " " + " ".join(interpolated + keys)


def strip_comment(line: str) -> str:
    """The line without its comment: whole-line `//`, `#`, `/*`, `*` comments and trailing ones."""
    stripped = line.lstrip()
    if stripped.startswith(("//", "#", "/*", "* ", "*/")) or stripped == "*":
        return ""
    cut = re.search(r"//|/\*|(?:^|\s)#(?![{!\[])", blank_strings(line))
    return line[:cut.start()].rstrip() if cut else line


def statement(lines: list[str], index: int, limit: int = 8) -> str:
    """The line plus its continuation lines while a call's parentheses stay open."""
    text, depth = strip_comment(lines[index]), 0
    for offset in range(limit):
        if index + offset >= len(lines):
            break
        if offset:
            text += " " + strip_comment(lines[index + offset]).strip()
        code = blank_strings(strip_comment(lines[index + offset]))
        depth += code.count("(") - code.count(")")
        if depth <= 0:
            break
    return text


def is_secret_value(rhs: str, tainted: dict) -> bool:
    """True when the right-hand side *is* the secret, not a call or condition that uses it."""
    core = re.sub(r"^\s*(?:await\s+|\(\s*)*", "", rhs)
    read = SECRET_READ.search(core)
    if read and LEADING_CHAIN.match(core[:read.start()]) and not NOT_THE_VALUE.search(core[read.end():]):
        return True
    first = re.match(r"\s*([A-Za-z_]\w*)", core)
    if first and first.group(1) in tainted and not NOT_THE_VALUE.search(core[first.end():]):
        return True
    return any(re.search(rf"\$\{{\s*{re.escape(name)}\b|\{{{re.escape(name)}\}}", rhs) for name in tainted)


def carries_body(rhs: str, body: dict) -> bool:
    """True when the right-hand side reads a raw body, or passes on the whole of one read earlier."""
    code = code_refs(rhs)
    if BODY_READ.search(code):
        return True
    core = re.sub(r"^\s*(?:await\s+|\(\s*|<[^>]*>\s*)*", "", rhs)
    for name in body:
        whole = rf"\b{re.escape(name)}\b(?:{SERIALIZE_METHOD}|{MEMBER})"
        if re.match(rf"{re.escape(name)}\b(?:{SERIALIZE_METHOD}|\s*(?:$|[;,)]|as\b|!|\?\?|\|\||or\b))", core) \
                or re.search(SERIALIZE + whole, rhs) \
                or re.search(rf"\$\{{\s*{whole}|(?<![$\w])\{{\s*{whole}", rhs):
            return True
    return False


def uses_whole(name: str, refs: str) -> bool:
    """True when the code uses the whole value or a serialization of it, not one field or its size."""
    for match in re.finditer(rf"\b{re.escape(name)}\b", refs):
        after, before = refs[match.end():], refs[:match.start()]
        if re.search(r"(?:\.|\b(?:len|size|bool|Boolean|byteLength)\s*\(\s*)$", before):
            continue
        if re.match(SERIALIZE_METHOD, after) or not re.match(r"\s*(?:\?\.|!\.|\.|\[)", after):
            return True
    return False


def uses_value(name: str, refs: str) -> bool:
    """True when the code uses the value itself, not a test of it (`header ? a : b`, `!!token`, `len(key)`)."""
    for match in re.finditer(rf"\b{re.escape(name)}\b", refs):
        before, after = refs[:match.start()], refs[match.end():]
        if re.search(r"\.\s*$", before) or DERIVED_BEFORE.search(before) or DERIVED_AFTER.match(after):
            continue
        return True
    return False


def secret_named(refs: str, error: bool = False) -> str | None:
    """The first value on the line whose name says it is a secret, used as a value (not a test of one)."""
    for match in re.finditer(r"[A-Za-z_$][\w$]*(?:\s*\.\s*[A-Za-z_$][\w$]*)*(?:\s*\(\s*\))?", refs):
        chain = re.split(r"\s*\.\s*", match.group(0).rstrip("() "))
        last = chain[-1].lstrip("_$")
        if not SECRET_IDENT.search(last) or NOT_SECRET_IDENT.search(last):
            continue
        # SCREAMING_CASE members and error arguments are codes such as ErrorCode.AUTH_INVALID_TOKEN.
        if re.fullmatch(r"[A-Z0-9_]+", last) and (len(chain) > 1 or error):
            continue
        if DERIVED_BEFORE.search(refs[:match.start()]) or DERIVED_AFTER.match(refs[match.end():]):
            continue
        return last
    return None


def new_taints(line: str, tainted: dict) -> tuple[list[str], list[str]]:
    """Names this line makes secret, and names it overwrites with something else."""
    added, cleared = [], []
    for match in HEADER_PARAM.finditer(line):
        name = match.group(1) or match.group(2)
        if SECRET_IDENT.search(name) and not NOT_SECRET_IDENT.search(name):
            added.append(name)
    for match in HEADER_ANNOTATION.finditer(line):
        kind, args, name = match.groups()
        if kind.startswith("Cookie") or re.search(r"authorization|cookie|token|api[-_]?key|secret|session", args + " " + name, re.IGNORECASE):
            added.append(name)
    for match in DESTRUCTURED_HEADERS.finditer(line):
        names = [part.split(":")[-1].strip() for part in match.group(1).split(",")]
        added += [n for n in names if n and SECRET_IDENT.search(n.replace("-", "_"))]
    match = None if DESTRUCTURED_HEADERS.search(line) else ASSIGN.search(blank_strings(line))
    if match:
        name, rhs = match.group(1), line[match.start(2):]
        if is_secret_value(rhs, tainted):
            added.append(name)
        elif name in tainted:
            cleared.append(name)
    return added, cleared


def identity_taints(line: str) -> list[str]:
    """Names this line fills with an identity the client sent: an assignment from a header, cookie or query read, or
    an injected header parameter (FastAPI `x_user_id: str = Header()`, `@RequestHeader("X-User-Id") String userId`)."""
    names = []
    for match in HEADER_PARAM.finditer(line):
        name = match.group(1) or match.group(2)
        if IDENTITY_PARAM.match(name):
            names.append(name)
    for match in HEADER_ANNOTATION.finditer(line):
        if re.search(rf"['\"](?:{IDENTITY_HEADER})['\"]", match.group(2), re.IGNORECASE):
            names.append(match.group(3))
    assign = ASSIGN.search(blank_strings(line))
    if assign and IDENTITY_READ.search(line[assign.start(2):]):
        names.append(assign.group(1))
    return names


def body_taints(line: str, body: dict) -> tuple[list[str], list[str]]:
    """Names this line fills with a raw request body, and names it overwrites."""
    blank = blank_strings(line)
    multi = MULTI_ASSIGN.search(blank)
    if multi:
        names, rhs = [n.strip() for n in multi.group(1).split(",")][:1], line[multi.start(2):]
    else:
        single = ASSIGN.search(blank)
        if not single or DESTRUCTURED_HEADERS.search(line) or re.search(r"\{[^}]*\}\s*=", blank):
            return [], []
        names, rhs = [single.group(1)], line[single.start(2):]
    names = [n for n in names if n != "_"]
    if carries_body(rhs, body):
        return names, []
    return [], [n for n in names if n in body]


def indent_of(line: str) -> int:
    return len(line) - len(line.lstrip(" \t"))


def _alive(names: dict, line: str) -> dict:
    """Drop names whose block has ended; a parameter's block starts on the first body line."""
    indent = indent_of(line)
    return {n: (read, indent if scope is None else scope) for n, (read, scope) in names.items()
            if scope is None or indent >= scope}


def _scope(line: str, injected: bool) -> int | None:
    # A parameter (a `def`/method line, or an injected header) lives in the body below it.
    if injected or re.search(r"\bdef\s|\)\s*(\{|->[^=]*:|:)\s*$", line):
        return None
    return indent_of(line)


# Logger bypass: output written around the app logger. Language families (.kts build scripts left out).
LOG_FAMILY = {
    ".ts": "js", ".tsx": "js", ".js": "js", ".jsx": "js", ".mjs": "js", ".cjs": "js", ".mts": "js", ".cts": "js",
    ".vue": "js", ".svelte": "js", ".py": "py", ".go": "go", ".java": "jvm", ".kt": "jvm", ".rs": "rust",
}
BYPASS_CALL = {
    "js": re.compile(r"(?<![\w$.])console\s*\.\s*(?:log|info|warn|error|debug)\s*\("),
    "py": re.compile(r"(?<![\w.])print\s*\("),
    "go": re.compile(r"\bfmt\s*\.\s*Print(?:f|ln)?\s*\(|\blog\s*\.\s*Print(?:f|ln)?\s*\("),
    "jvm": re.compile(r"\bSystem\s*\.\s*(?:out|err)\s*\.\s*print(?:ln|f)?\s*\("),
    "rust": re.compile(r"(?<![\w:])e?println!\s*\("),
}
KOTLIN_PRINT = re.compile(r"(?<![\w.])println\s*\(")
# Go's `log.Print*` bypasses only where `log` is the standard library: zerolog's `log` package and
# logrus imported as `log` are the app logger.
GO_STD_LOG = re.compile(r'^\s*(?:import\s+)?"log"\s*$', re.MULTILINE)
# Code that prints by design: scripts, CLIs, migrations, seeds, top-level examples, samples and docs,
# build scripts, tool configs, Python files that can't be imported (a hyphen in the name), and any
# file with a shebang (checked in scan_file). Not any `example` folder: Java packages such as
# com/example/… are application code.
PRINTS_BY_DESIGN = re.compile(
    r"(^|/)(scripts?|cli|bin|migrations?|alembic|seeds?|seeders)(/|$)|^(examples?|samples?|benches|docs?)/"
    r"|(^|/)management/commands/|(^|/)(?i:cli|migrate|seed)\.[^/]+$|(^|/)(manage|setup|noxfile|fabfile|__main__)\.py$"
    r"|(^|/)[^/]*-[^/]*\.py$|(^|/)build\.(rs|[cm]?[jt]s)$|\.config\.[cm]?[jt]s$"
)
BYPASS_COLLAPSE = 5   # more calls than this in one file: report the first and count the rest
BYPASS_FIX = "log through it so the record gets a level, the configured format and trace context"
NO_LOGGER_FIX = ("add the app logger (levels, the configured format, native trace ids; signals/app-logs.md) "
                 "and route this line through it")
# The project has an app logger when a source file is named like one, a manifest lists a logging
# library, or code uses the language's logging module. Evidence counts per language family, so a
# Python service's `logging` says nothing about the console calls of a JavaScript tool beside it.
LOGGER_STEM = re.compile(r"(?!request|http|access|audit)[a-z]*?(?:logger|logging|logs)")   # logs.py: logging.py shadows the stdlib
LOG_MANIFEST = {"package.json": "js", "pyproject.toml": "py", "Pipfile": "py", "setup.py": "py", "setup.cfg": "py",
                "go.mod": "go", "pom.xml": "jvm", "build.gradle": "jvm", "build.gradle.kts": "jvm", "Cargo.toml": "rust"}
LOG_LIBRARY = {
    "js": re.compile(r"""["'](pino|winston|bunyan|log4js|tslog|consola|loglevel|signale|roarr|@logtape/logtape|nestjs-pino|nest-winston)["']\s*:"""),
    "py": re.compile(r"""(?:^|[\s"'\[,])(structlog|loguru|logbook|picologging|python-json-logger)\b""", re.MULTILINE),
    "go": re.compile(r"\b(go\.uber\.org/zap|github\.com/rs/zerolog|github\.com/sirupsen/logrus|github\.com/go-kit/log"
                     r"|github\.com/hashicorp/go-hclog|github\.com/charmbracelet/log)\b"),
    "jvm": re.compile(r"\b(slf4j|logback|log4j|kotlin-logging|tinylog|jboss-logging|flogger)"),
    "rust": re.compile(r"^\s*(tracing|env_logger|log|slog|fern|log4rs|simplelog|flexi_logger|pretty_env_logger)\s*="
                       r"|^\[(?:[\w-]+\.)?dependencies\.(tracing|env_logger|log|slog|fern|log4rs)\]", re.MULTILINE),
}
LOG_USAGE = {
    "js": re.compile(r"""\b(new\s+Logger)\s*\(|\b(?:require\s*\(\s*|from\s+)['"](pino|winston|bunyan|log4js|tslog|consola|loglevel)['"]"""
                     r"|\b((?:request|req|reply|app|fastify|server)\.log)\.(?:info|warn|error|debug|trace|fatal)\s*\("),   # Fastify's logger
    "py": re.compile(r"^\s*(?:import|from)\s+(logging|structlog|loguru)\b", re.MULTILINE),
    "go": re.compile(r'"(log/slog|go\.uber\.org/zap|github\.com/rs/zerolog|github\.com/sirupsen/logrus)'),
    "jvm": re.compile(r"\b(LoggerFactory\.getLogger|LogManager\.getLogger|Logger\.getLogger|KotlinLogging|System\.getLogger"
                      r"|io\.quarkus\.logging\.Log)\b|@(Slf4j|Log4j2?|CommonsLog|JBossLog)\b"),
    "rust": re.compile(r"\b((?:tracing|log)::(?:\w+::)*(?:info|warn|error|debug|trace)!)|^\s*use\s+(tracing|log)::", re.MULTILINE),
}

# Scheduled jobs: each run needs its own root span. Callback-style registrations hold the job body in
# the same file, so that file must start the span. setInterval and similar timers are left out on
# purpose: too many of them are not jobs.
SCHEDULE_CALLBACK = {
    "js": re.compile(r"(?i:\b\w*cron\w*)\s*\.\s*schedule\s*\(|\.\s*scheduleJob\s*\(|\bnew\s+CronJob\s*\(|\bCronJob\s*\.\s*from\s*\(|@Cron\s*\("),
    "py": re.compile(r"\.\s*add_job\s*\(|@[\w.]*\bscheduled_job\s*\(|^\s*@(?:[\w.]+\.)?periodic_task\b|\bschedule\s*\.\s*every\s*\("),
    "go": re.compile(r"\.\s*Add(?:Func|Job)\s*\("),
    "jvm": re.compile(r"@Scheduled\b"),
    "rust": re.compile(r"\bJob\s*::\s*new\w*\s*\("),
}
# Scheduler set-up (robfig/cron, tokio-cron-scheduler): reported only when the file registers no job.
SCHEDULER_NEW = {"go": re.compile(r"\bcron\s*\.\s*New\s*\("), "rust": re.compile(r"\bJobScheduler\s*::\s*new\s*\(")}
# Go and Rust registrations count only in files that use a cron library.
SCHEDULER_LIB = {"go": re.compile(r'"[^"\n]*cron[^"\n]*"'), "rust": re.compile(r"\btokio_cron_scheduler\b|\bJobScheduler\b")}
# Queue-style registrations (agenda, bull and BullMQ repeatable jobs, celery beat) name a job whose
# processor usually lives in another file: a job span anywhere in the project counts for them.
SCHEDULE_QUEUE = {
    "js": re.compile(r"""\.\s*every\s*\(\s*['"`]|\brepeat\s*:\s*\{|\.\s*upsertJobScheduler\s*\("""),
    "py": re.compile(r"(?i:\b(?:celery_)?beat_schedule)\s*=|\.\s*add_periodic_task\s*\("),
}
# A job span: a job helper call (withJobRun, with_job_run, run_job, job_span …), or a span started
# in the same file together with a new-root marker (`root: true`, ROOT_CONTEXT, WithNewRoot,
# setNoParent, `context=Context()`, `parent: None`, a tracer started from context.Background()).
JOB_HELPER = re.compile(r"\b(?:with_?job\w*|run_?job|job_?(?:span|run)\w*|start_?job_?span)\s*[(<]", re.IGNORECASE)
SPAN_START = re.compile(
    r"\b(?:startActiveSpan|startSpan|start_as_current_span|start_span|spanBuilder|start_with_context)\s*\("
    r"|\b\w*[Tt]racer\w*(?:\([^)]*\))?\s*\.\s*Start\s*\(|\b(?:(?:info|debug|warn|error|trace)_)?span!\s*\("
)
ROOT_MARKER = re.compile(
    r"\broot\s*:\s*true\b|\bROOT_CONTEXT\b|\bWithNewRoot\s*\(|\bnew_root\b|\bsetNoParent\s*\(|\bContext\s*\.\s*root\s*\(\s*\)"
    r"|\bcontext\s*=\s*(?:[\w.]+\.)?Context\s*\(\s*\)|\bContext\s*::\s*new\s*\(\s*\)|\bparent\s*:\s*None\b"
    r"|[Tt]racer\w*(?:\([^)]*\))?\s*\.\s*Start\s*\(\s*context\s*\.\s*(?:Background|TODO)\s*\(\s*\)"
)
# Consumer instrumentation that gives every queued run its own span.
QUEUE_SPAN = re.compile(r"\b(?:CeleryInstrumentor|task_prerun|BullMQOtel|BullMQInstrumentation)\b")
SCHEDULE_FIX = "run each tick in the job helper: a new root span per run, never a child of start-up or the current span"

# SDK internals: assignments to the prototype or a private member of a name bound to an
# observability SDK (an import, or an object constructed from one), reflection into SDK classes and
# go:linkname into SDK packages.
SDK_MODULE = re.compile(r"^(?:@opentelemetry/|opentelemetry(?:\.|$)|go\.opentelemetry\.io/|io\.opentelemetry\.|prom-client$"
                        r"|@prometheus-io/client|prometheus_client(?:\.|$)|github\.com/prometheus/|io\.prometheus\.)")
JS_IMPORT = re.compile(r"""\bimport\s+((?:(?!\bfrom\b)[^;'"`])+?)\s*\bfrom\s*['"]([^'"]+)['"]""")
JS_REQUIRE = re.compile(r"""\b(?:const|let|var)\s+(\{[^}]*\}|[\w$]+)\s*=\s*require\s*\(\s*['"]([^'"]+)['"]\s*\)""")
PY_FROM_IMPORT = re.compile(r"^\s*from\s+([\w.]+)\s+import\s+(\([^)]*\)|[^\n#;]+)", re.MULTILINE)
PY_IMPORT = re.compile(r"^\s*import\s+([\w.]+)(?:\s+as\s+(\w+))?", re.MULTILINE)
JVM_IMPORT = re.compile(r"^\s*import\s+((?:io\.opentelemetry|io\.prometheus)(?:\.\w+)*)\.([A-Z]\w*)(?:\s+as\s+(\w+))?\s*;?\s*$", re.MULTILINE)
GO_LINKNAME = re.compile(r"^\s*//go:linkname\s+\w+\s+((?:go\.opentelemetry\.io|github\.com/prometheus)/\S+)")
JVM_FORNAME = re.compile(r"""\bClass\s*\.\s*forName\s*\(\s*"((?:io\.opentelemetry|io\.prometheus)\.[\w.$]+)""")
SDK_NEW = {
    "js": r"(?:\bthis\s*\.\s*)?\b([\w$]+)\s*(?::[^=;()]+)?=\s*new\s+({names})\s*[(<]",
    "py": r"(?:\bself\s*\.\s*)?\b(\w+)\s*(?::[^=\n]+)?=\s*({names})\s*\(",
}
MEMBERS = r"(?:\s*\.\s*[\w$]+|\s*\[[^\]]+\])*"
NAMESPACE = r"(?:\s*\.\s*[\w$]+)*?"   # members before the class: prom.Registry.prototype
SDK_PATCH = {
    "js": [
        rf"(?<![\w$.])(?:this\s*\.\s*)?({{names}}){NAMESPACE}\s*\.\s*prototype\s*(?:\.\s*[\w$]+|\[[^\]]+\]){MEMBERS}\s*=(?![=>])",
        rf"(?<![\w$.])(?:this\s*\.\s*)?({{names}}){NAMESPACE}\s*\.\s*_[\w$]*{MEMBERS}\s*=(?![=>])",
        rf"\(\s*(?:this\s*\.\s*)?({{names}}){NAMESPACE}\s+as\s+[^)]*\)\s*(?:\.\s*[\w$]+|\[[^\]]+\]){MEMBERS}\s*=(?![=>])",
        r"\b(?:Object\s*\.\s*(?:defineProperty|defineProperties|assign|setPrototypeOf)|(?:shimmer\s*\.\s*)?(?:wrap|massWrap))"
        rf"\s*\(\s*({{names}}){NAMESPACE}\s*\.\s*prototype\b",
    ],
    "py": [
        r"(?<![\w.])(?:self\s*\.\s*)?({names})(?:\s*\.\s*\w+)*?\s*\.\s*_\w*(?:\s*\.\s*\w+)*\s*=(?!=)",
        r"(?<![\w.])({classes})\s*\.\s*\w+\s*=(?!=)",
        r"\bsetattr\s*\(\s*({names})\b",
    ],
    "jvm": [r"\b({names})\s*(?:\.\s*class|::\s*class(?:\s*\.\s*java)?)\s*\.\s*getDeclared(?:Field|Method|Constructor)s?\s*\("],
}
SDK_FIX = "SDK internals change between versions without notice; use a documented option, a wrapper or a processor"


def logger_named(rel: str) -> bool:
    """A file named like an app logger: logger.ts, logging.py, logs.py, app-logger.js, AppLogger.kt."""
    stem = re.sub(r"[-_]", "", rel.rsplit("/", 1)[-1].split(".")[0]).lower()
    return LOGGER_STEM.fullmatch(stem) is not None


def logger_keys(rel: str, family: str) -> tuple[str, ...]:
    """How other files import a logger file: `utils/logger`, `core.logging`; Go imports the directory."""
    parts = rel.split("/")
    if family == "go":
        return ("/".join(parts[-3:-1]),) if len(parts) > 2 else ()
    if len(parts) < 2:
        return ()
    stem = parts[-1].split(".")[0]
    return (f"{parts[-2]}/{stem}", f"{parts[-2]}.{stem}")


def hidden_lines(lines: list[str], suffix: str) -> set[int]:
    """Indexes of lines inside a block comment or a string that spans lines: `/* … */`, Python
    docstrings, Java and Kotlin text blocks, JavaScript template literals and Go raw strings; in
    Rust, the `#[cfg(test)] mod tests` block at the end of a file."""
    quotes = ('"""', "'''") if suffix == ".py" else ('"""',) if suffix in (".java", ".kt") else ()
    backtick = LOG_FAMILY.get(suffix) == "js" or suffix == ".go"
    hidden, closer = set(), None
    for index, line in enumerate(lines):
        if suffix == ".rs" and re.match(r"\s*#\[cfg\(test\)\]", line):
            following = next((rest for rest in lines[index + 1:] if rest.strip()), "")
            if re.match(r"\s*(?:pub\s+)?mod\s+\w+\s*\{", following):
                return hidden | set(range(index, len(lines)))
        if closer:
            hidden.add(index)
            if (re.search(r"(?<!\\)`", line) if closer == "`" else closer in line):
                closer = None
            continue
        code = blank_strings(strip_comment(line))
        quote = next((q for q in quotes if code.count(q) % 2), None)
        start = -1 if suffix == ".py" else blank_strings(line).rfind("/*")
        if quote:
            closer = quote
            if suffix == ".py" and re.match(rf"\s*[rRbBuUfF]{{0,2}}{quote}", line):   # a docstring, not code opening a string
                hidden.add(index)
        elif start >= 0 and "*/" not in blank_strings(line)[start + 2:]:
            closer = "*/"
        elif backtick and code.count("`") % 2:
            closer = "`"
    return hidden


def bypass_calls(code: str, family: str, suffix: str, std_log: bool) -> list[str]:
    """Calls on the line that write output around the app logger: `console.error`, `print`, `fmt.Printf`."""
    calls = [re.sub(r"\s+|\($", "", m.group(0)) for m in BYPASS_CALL[family].finditer(code)]
    if family == "go" and not std_log:
        calls = [c for c in calls if not c.startswith("log.")]
    if suffix == ".kt":
        calls += ["println" for _ in KOTLIN_PRINT.finditer(code)]
    return calls


def js_bindings(clause: str) -> list[str]:
    """Local names an import or require clause binds: `X`, `* as X`, `{ A, B as C }`, `{ A, B: C }`."""
    names = re.findall(r"\*\s*as\s+([\w$]+)", clause)
    braces = re.search(r"\{([^}]*)\}", clause)
    for part in braces.group(1).split(",") if braces else []:
        part = re.sub(r"^\s*type\s+", "", part).strip()
        if part:
            names.append(re.split(r"\s+as\s+|\s*:\s*", part)[-1].strip())
    default = re.match(r"\s*(?:type\s+)?([\w$]+)\s*(?:,|$)", clause)
    if default:
        names.append(default.group(1))
    return [n for n in names if re.fullmatch(r"[\w$]+", n)]


def sdk_bindings(text: str, family: str | None) -> dict[str, str]:
    """Names in the file bound to an observability SDK module, and objects constructed from them."""
    bound: dict[str, str] = {}
    if family == "js":
        for clause, module in JS_IMPORT.findall(text) + JS_REQUIRE.findall(text):
            if SDK_MODULE.match(module):
                bound.update(dict.fromkeys(js_bindings(clause), module))
    elif family == "py":
        for module, names in PY_FROM_IMPORT.findall(text):
            if SDK_MODULE.match(module):
                for part in re.sub(r"#[^\n]*", "", names).strip("() \n").split(","):
                    name = re.split(r"\s+as\s+", part.strip())[-1].strip()
                    if re.fullmatch(r"\w+", name):
                        bound[name] = module
        for module, alias in PY_IMPORT.findall(text):
            if SDK_MODULE.match(module):
                bound[alias or module.split(".")[0]] = module
    elif family == "jvm":
        for package, name, alias in JVM_IMPORT.findall(text):
            bound[alias or name] = f"{package}.{name}"
    if bound and family in SDK_NEW:
        names = "|".join(map(re.escape, sorted(bound, key=len, reverse=True)))
        for var, cls in re.findall(SDK_NEW[family].format(names=names), text):
            bound.setdefault(var, bound[cls])
    return bound


def sdk_patterns(bound: dict[str, str], family: str | None) -> list[re.Pattern]:
    if not bound or family not in SDK_PATCH:
        return []
    names = "|".join(map(re.escape, sorted(bound, key=len, reverse=True)))
    classes = "|".join(re.escape(n) for n in sorted(bound, key=len, reverse=True) if n[0].isupper())
    return [re.compile(p.format(names=names, classes=classes)) for p in SDK_PATCH[family] if classes or "{classes}" not in p]


class ProjectScan:
    """Project-wide state for the rules that need more than one file: the app logger and job spans."""

    def __init__(self, files: list[tuple[Path, str]]):
        app = [(path, rel) for path, rel in files if not TEST_RE.search(rel) and not PRINTS_BY_DESIGN.search(rel)]
        self.log_files = {rel: LOG_FAMILY[path.suffix] for path, rel in app if path.suffix in LOG_FAMILY and logger_named(rel)}
        self.log_keys = {rel: logger_keys(rel, family) for rel, family in self.log_files.items()}
        self.log_refs = dict.fromkeys(self.log_files, 0)
        self.browser_loggers: set[str] = set()
        self.libraries: dict[str, str] = {}
        self.usage: dict[str, str] = {}
        for path, rel in files:
            family = LOG_MANIFEST.get(path.name) or ("py" if re.fullmatch(r"requirements[\w.-]*\.txt", path.name) else None)
            if family and family not in self.libraries and not TEST_RE.search(rel) and not re.match(r"(examples?|samples?|benches|docs?)/", rel):
                try:
                    match = LOG_LIBRARY[family].search(path.read_text(encoding="utf-8", errors="ignore"))
                except OSError:
                    continue
                if match:
                    self.libraries[family] = next(g for g in match.groups() if g)
        self.bypass: dict[str, dict[str, list[tuple[int, str]]]] = {}
        self.queued: list[tuple[str, int]] = []
        self.job_span = False
        self.ai_project = False
        self.instrumented: dict[str, dict[str, str]] = {}
        for path, rel in files:
            manifest = path.name in LOG_MANIFEST or re.fullmatch(r"requirements[\w.-]*\.txt", path.name)
            if not manifest or TEST_RE.search(rel) or re.match(r"(examples?|samples?|benches|docs?)/", rel):
                continue
            try:
                text = path.read_text(encoding="utf-8", errors="ignore")
            except OSError:
                continue
            self.ai_project = self.ai_project or AI_MANIFEST.search(text) is not None
            self.note_instrumentation(rel, text)

    def note_instrumentation(self, rel: str, text: str) -> None:
        """Where each instrumentation family of an AI library first shows up: a manifest line or an import."""
        for library, families in GENAI_INSTRUMENTATION.items():
            for family, pattern in families.items():
                if family in self.instrumented.get(library, {}):
                    continue
                match = re.search(pattern, text, re.MULTILINE)
                if match:
                    self.instrumented.setdefault(library, {})[family] = f"{rel}:{text.count(chr(10), 0, match.start()) + 1}"

    def scan_file(self, rel: str, suffix: str, text: str, lines: list[str], in_module: bool, browser: bool) -> list[Finding]:
        """Logger bypass, scheduler and SDK-patch checks for one application (non-test) file."""
        found: list[Finding] = []
        family = LOG_FAMILY.get(suffix)
        self.note_instrumentation(rel, "\n".join(strip_comment(line) for line in lines))  # a comment naming a package isn't a use
        designed = PRINTS_BY_DESIGN.search(rel) is not None or text.startswith("#!")
        if family and not designed and family not in self.usage:
            match = LOG_USAGE[family].search(text)
            if match:
                self.usage[family] = re.sub(r"\s+", " ", next(g for g in match.groups() if g))
        for logger, keys in self.log_keys.items():
            if logger != rel and self.log_files[logger] == family and any(key in text for key in keys):
                self.log_refs[logger] += 1
        if rel in self.log_files and browser:
            self.browser_loggers.add(rel)
        printing = family is not None and not (in_module or browser or designed or rel in self.log_files)
        std_log = suffix == ".go" and GO_STD_LOG.search(text) is not None
        library = family not in SCHEDULER_LIB or SCHEDULER_LIB[family].search(text) is not None
        bound = sdk_bindings(text, family)
        patches = sdk_patterns(bound, family)
        hidden = hidden_lines(lines, suffix)
        callbacks, queued, created = [], [], []
        helper = span = root = False
        for index, raw in enumerate(lines):
            if IGNORE_RE.search(raw):
                continue
            loc = f"{rel}:{index + 1}"
            link = GO_LINKNAME.match(raw) if suffix == ".go" else None
            if link:
                found.append(Finding("P2", "sdk-internal-patch", loc, f"go:linkname reaches into {link.group(1)}; {SDK_FIX}"))
            line = strip_comment(raw)
            if index in hidden or not line.strip():
                continue
            code = blank_strings(line)
            calls = bypass_calls(code, family, suffix, std_log) if printing else []
            if calls:
                self.bypass.setdefault(family, {}).setdefault(rel, []).append((index + 1, calls[0]))
            if family in SCHEDULE_CALLBACK and library and SCHEDULE_CALLBACK[family].search(code):
                callbacks.append(index + 1)
            elif family in SCHEDULE_QUEUE and SCHEDULE_QUEUE[family].search(code):
                queued.append(index + 1)
            elif family in SCHEDULER_NEW and library and SCHEDULER_NEW[family].search(code):
                created.append(index + 1)
            helper = helper or JOB_HELPER.search(code) is not None
            span = span or SPAN_START.search(code) is not None
            root = root or ROOT_MARKER.search(code) is not None
            hit = next((m for m in (p.search(line) for p in patches) if m), None)
            if hit:
                name = hit.group(1)
                what = "reached by reflection" if family == "jvm" else "patched through its prototype or a private member"
                found.append(Finding("P2", "sdk-internal-patch", loc, f"'{name}' ({bound[name]}) is {what}; {SDK_FIX}"))
            forname = JVM_FORNAME.search(line) if family == "jvm" else None
            if forname and not hit:
                found.append(Finding("P2", "sdk-internal-patch", loc, f"reflection into {forname.group(1)}; {SDK_FIX}"))
        has_span = helper or (span and root)
        if (has_span and not in_module) or QUEUE_SPAN.search(text):
            self.job_span = True
        if not has_span and not in_module:
            found += [Finding("P2", "scheduler-no-root-span", f"{rel}:{lineno}", "scheduled job registered with no job span "
                              f"in this file; {SCHEDULE_FIX}") for lineno in callbacks or created]
            self.queued += [(rel, lineno) for lineno in queued]
        return found

    def logger_hint(self, family: str) -> str | None:
        """The app logger to name in findings: the logger file most others import, a library, or the module."""
        files = [rel for rel, fam in self.log_files.items() if fam == family and rel not in self.browser_loggers]
        if files:
            return sorted(files, key=lambda rel: (-self.log_refs[rel], rel.count("/"), rel))[0]
        return self.libraries.get(family) or self.usage.get(family)

    def finish(self) -> list[Finding]:
        found: list[Finding] = []
        for library, families in sorted(self.instrumented.items()):
            if len(families) > 1:
                ordered = sorted(families.items(), key=lambda item: _location_key(item[1]))
                names = [name for name, _ in ordered]
                found.append(Finding("P2", "genai-duplicate-instrumentation", ordered[1][1], f"{library} is instrumented by "
                                     f"{', '.join(names[:-1])} and {names[-1]}; keep one, or every call is recorded twice"))
        if not self.job_span:
            found += [Finding("P2", "scheduler-no-root-span", f"{rel}:{lineno}", "repeating job registered and no job span "
                              "found in the project; start each run's root span in its processor with the job helper")
                      for rel, lineno in self.queued]
        for family, per_file in self.bypass.items():
            hint = self.logger_hint(family)
            # Without an app logger every line is a bypass: the records carry no level, format or trace context.
            where = f"around the app logger ({hint})" if hint else "with no app logger in the project"
            fix = BYPASS_FIX if hint else NO_LOGGER_FIX
            for rel, calls in per_file.items():
                if len(calls) > BYPASS_COLLAPSE:
                    lineno, call = calls[0]
                    found.append(Finding("P2", "logger-bypass", f"{rel}:{lineno}", f"{call} writes {where}, "
                                         f"and {len(calls) - 1} more in this file; {fix}"))
                    continue
                found += [Finding("P2", "logger-bypass", f"{rel}:{lineno}", f"{call} writes {where}; {fix}")
                          for lineno, call in calls]
        return found


def audit(root: Path, modules: list[str], include_tests: bool = False) -> list[Finding]:
    findings: list[Finding] = []
    files = []
    own = SKILL_SCRIPTS if root in SKILL_SCRIPTS.parents else None   # the skill copied or installed inside the project
    for path in sorted(root.rglob("*")):
        rel_parts = path.relative_to(root).parts
        if own is not None and own in path.parents:
            continue   # this skill's own scripts name the rules they check
        if not any(part in SKIP_DIRS for part in rel_parts) and path.is_file():
            files.append((path, "/".join(rel_parts)))
    project = ProjectScan(files)
    max_chars = content_max_chars(files)
    baggage_keys: dict[str, str] = {}   # literal key set as baggage -> first location
    for path, rel in files:
        if path.suffix not in SOURCE_SUFFIXES or (not include_tests and TEST_RE.search(rel)):
            continue
        in_module = _in_module(rel, modules)
        try:
            source = path.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        lines = source.splitlines()
        browser = is_browser_file(rel, lines)
        js = LOG_FAMILY.get(path.suffix) == "js"
        langfuse_file = LANGFUSE_IMPORT.search(source) is not None
        if not TEST_RE.search(rel):
            # Logger bypass, schedulers and SDK patches are about application code: tests never count.
            findings.extend(project.scan_file(rel, path.suffix, source, lines, in_module, browser))
        tainted: dict[str, tuple[int, int | None]] = {}   # name -> (line read, block indent it lives in)
        identity: dict[str, tuple[int, int | None]] = {}   # names holding a client-sent identity
        body: dict[str, tuple[int, int | None]] = {}
        opened = False   # the previous code line opened a block: pending parameter scopes start here
        for lineno, raw in enumerate(lines, 1):
            if IGNORE_RE.search(raw):
                continue
            line = strip_comment(raw)
            if line.strip():
                if opened:
                    tainted, body, identity = _alive(tainted, line), _alive(body, line), _alive(identity, line)
                else:
                    tainted = {n: v for n, v in tainted.items() if v[1] is None or indent_of(line) >= v[1]}
                    body = {n: v for n, v in body.items() if v[1] is None or indent_of(line) >= v[1]}
                    identity = {n: v for n, v in identity.items() if v[1] is None or indent_of(line) >= v[1]}
                opened = bool(re.search(r"[{:]\s*$", line))
            loc = f"{rel}:{lineno}"
            sink, error_sink = is_sink(line), ERROR_SINK.search(line) is not None
            text = statement(lines, lineno - 1) if sink or error_sink else line
            direct = scan_line(text, rel, lineno, in_module)
            genai = genai_findings(lines, lineno - 1, loc, in_module, browser, js, langfuse_file, project.ai_project)
            if any(f.rule == "genai-capture-on" for f in genai):
                direct = [f for f in direct if f.rule != "otel-env-outside-loader"]
            direct += genai
            direct += data_model_findings(lines, lineno - 1, loc, in_module)
            direct += baggage_list(lines, lineno - 1, loc)
            direct += safety_findings(lines, lineno - 1, loc, rel, in_module, max_chars)
            for match in BAGGAGE_SET.finditer(blank_strings(line)):
                first = STRING.search(line[match.end():])
                if first and first.group(2):
                    baggage_keys.setdefault(first.group(2), loc)
            if browser:
                direct += browser_findings(text, loc)
            elif PUBLIC_KEY.search(text):
                direct.append(Finding("P1", "public-key-var", loc, "telemetry key named as a public build variable or "
                                      "public runtime config; bundlers ship these to the page, so it stays a server-only "
                                      "variable"))
            findings.extend(direct)
            refs = code_refs(text)
            reported = any(f.rule == "secret-capture" for f in direct)
            if tainted and sink and not reported:
                hit = next((n for n in tainted if uses_value(n, refs)), None)
                if hit:
                    reported = True
                    findings.append(Finding("P0", "secret-capture", loc,
                                            f"'{hit}' (a secret read on line {tainted[hit][0]}) reaches a log, print, span or baggage call"))
            if body and sink and not any(f.rule in ("body-logged", "body-in-span") for f in direct):
                hit = next((n for n in body if uses_whole(n, refs)), None)
                if hit:
                    rule = "body-in-span" if ATTR_SINK.search(text) else "body-logged"
                    findings.append(Finding("P1", rule, loc, f"'{hit}' (a raw request body read on line {body[hit][0]}) "
                                            "reaches a log, print or span call; capture allow-listed fields only"))
            if (sink or error_sink) and not reported:
                named = secret_named(refs, error=error_sink and not sink)
                if named and error_sink and not sink:
                    findings.append(Finding("P1", "secret-in-error", loc, f"'{named}' goes into an error message; messages reach "
                                            "logs and exception.message, so keep secrets out of them"))
                elif named:
                    findings.append(Finding("P1", "secret-named-value", loc, f"'{named}' is named like a secret and reaches a log, "
                                            "print or span call; check it is not one, or log allow-listed values only"))
            if identity and IDENTITY_KEY.search(text) and not any(f.rule == "identity-from-header" for f in direct):
                hit = next((n for n in identity if uses_value(n, refs)), None)
                if hit:
                    findings.append(Finding("P1", "identity-from-header", loc, f"'{hit}' (read from a client header, "
                                            f"cookie or query on line {identity[hit][0]}) is set as an identity row; take "
                                            "it from the trusted auth or tenant middleware"))
            for name in identity_taints(line):
                identity.setdefault(name, (lineno, _scope(line, bool(HEADER_PARAM.search(line) or HEADER_ANNOTATION.search(line)))))
            added, cleared = new_taints(line, tainted)
            for name in cleared:
                tainted.pop(name, None)
            injected = bool(HEADER_PARAM.search(line) or HEADER_ANNOTATION.search(line))
            for name in added:
                tainted.setdefault(name, (lineno, _scope(line, injected)))
            grown, dropped = body_taints(line, body)
            for name in dropped:
                body.pop(name, None)
            for name in grown:
                body.setdefault(name, (lineno, _scope(line, False)))
    for path, rel in files:
        if not SPRING_CONFIG.search(rel) or (not include_tests and TEST_RE.search(rel)):
            continue
        try:
            config = path.read_text(encoding="utf-8", errors="ignore").splitlines()
        except OSError:
            continue
        findings += [Finding("P1", "genai-capture-on", f"{rel}:{lineno}", "prompt or completion logging switched on in the "
                             "Spring AI configuration; the loader sets every capture switch from genai.content.capture")
                     for lineno, raw in enumerate(config, 1)
                     if SPRING_AI_CAPTURE.search(raw) and not IGNORE_RE.search(raw) and not raw.lstrip().startswith(("#", "!"))]
    for path, rel in files:
        # The collector's own configuration (.alloy) is the collector route's sender and isn't read here.
        if _in_module(rel, modules) or (not include_tests and TEST_RE.search(rel)):
            continue
        if path.suffix not in SOURCE_SUFFIXES and not SENDER_CONFIG.search(rel):
            continue
        try:
            text = path.read_text(encoding="utf-8", errors="ignore").splitlines()
        except OSError:
            continue
        findings += [Finding("P2", "genai-langfuse-two-senders", f"{rel}:{lineno}", "Langfuse's OTLP endpoint configured "
                             "outside the observability module; the module already sends each span to Langfuse once, so "
                             "a second exporter sends every observation twice")
                     for lineno, raw in enumerate(text, 1)
                     if LANGFUSE_OTLP.search(raw) and not IGNORE_RE.search(raw)
                     and not raw.lstrip().startswith(("#", "//", "!", "*"))]
    proxy_mode = False
    faro_project = False
    for path, rel in files:
        if path.suffix in (".yml", ".yaml") and "observability" in path.name:
            try:
                proxy_mode = proxy_mode or any(RUM_PROXY.search(raw) for raw in path.read_text(encoding="utf-8", errors="ignore").splitlines())
            except OSError:
                pass
    for path, rel in files:
        if path.suffix not in SOURCE_SUFFIXES or (not include_tests and TEST_RE.search(rel)):
            continue
        try:
            text = path.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        if FARO_IMPORT.search(text):
            faro_project = True
            findings += faro_findings(rel, text.splitlines(), proxy_mode)
    if faro_project:
        sources = {rel: path.read_text(encoding="utf-8", errors="ignore") for path, rel in files
                   if path.suffix in SOURCE_SUFFIXES and not TEST_RE.search(rel)}
        if not any("resetUser(" in text for text in sources.values()):
            for rel, text in sources.items():
                for lineno, raw in enumerate(text.splitlines(), 1):
                    if FARO_IMPORT.search(text) and re.search(r"\bsetUser\s*\(", raw) and not IGNORE_RE.search(raw):
                        findings.append(Finding("P2", "faro-user-not-reset", f"{rel}:{lineno}", "Faro user set but "
                                                "never reset; call faro.api.resetUser() on sign-out, or the next "
                                                "person's activity is recorded as the previous user"))
        for path, rel in files:
            if not NGINX_CONF.search(rel):
                continue
            try:
                conf = path.read_text(encoding="utf-8", errors="ignore").splitlines()
            except OSError:
                continue
            findings += [Finding("P2", "rum-access-log-query", f"{rel}:{lineno}", "the page server's access log "
                                 "records the query string (combined format, $request, $request_uri or $args): "
                                 "OAuth codes and search text from page URLs reach the logs; log the path only")
                         for lineno, raw in enumerate(conf, 1)
                         if NGINX_QUERY_LOG.search(raw.split("#")[0]) and ("access_log" in raw or "log_format" in raw
                                                                            or raw.strip().startswith("'"))]
        # A Faro key is reported by the faro-key-* rules; the generic credential rule would repeat it.
        faro_keys = {f.location for f in findings if f.rule in ("faro-key-literal", "faro-key-in-bundle")}
        findings = [f for f in findings if not (f.rule == "telemetry-credential-literal" and f.location in faro_keys)]
    for path, rel in files if faro_project else []:
        if not BUNDLER_CONFIG.search(rel):
            continue
        try:
            text = path.read_text(encoding="utf-8", errors="ignore").splitlines()
        except OSError:
            continue
        findings += [Finding("P2", "faro-public-sourcemaps", f"{rel}:{lineno}", "source maps emitted into the served "
                             "build; build them hidden and give them to the receiver, never to browsers")
                     for lineno, raw in enumerate(text, 1)
                     if PUBLIC_SOURCEMAPS.search(raw) and not IGNORE_RE.search(raw) and not raw.lstrip().startswith("//")]
    if len(baggage_keys) > BAGGAGE_LIMIT:
        keys = sorted(baggage_keys, key=lambda k: _location_key(baggage_keys[k]))
        findings.append(Finding("P2", "baggage-over-limit", baggage_keys[keys[BAGGAGE_LIMIT]], f"{len(keys)} distinct "
                                f"baggage keys set in the project; at most {BAGGAGE_LIMIT}"))
    for path, rel in files:
        # The yml, charts, env and compose files: resource keys the standard never adds.
        if path.suffix in SOURCE_SUFFIXES or not SENDER_CONFIG.search(rel) or (not include_tests and TEST_RE.search(rel)):
            continue
        if path.name in ("package.json", "package-lock.json", "composer.json", "tsconfig.json") or path.suffix == ".lock":
            continue
        try:
            text = path.read_text(encoding="utf-8", errors="ignore").splitlines()
        except OSError:
            continue
        findings += [Finding("P2", "resource-excluded-key", f"{rel}:{lineno}", "resource key the standard never adds "
                             "(pod or node UID, instance or host id, process id or command line, VCS data, release id) "
                             "or a process or host detector; build the resource from the yml and the platform rows only")
                     for lineno, raw in enumerate(text, 1)
                     if (EXCLUDED_RESOURCE.search(raw) or RESOURCE_DETECTOR.search(raw)) and not IGNORE_RE.search(raw)
                     and not EXCLUSION_CONTEXT.search(raw) and not raw.lstrip().startswith(("#", "//", "!", "*"))]
    for path, rel in files:
        if path.suffix in SOURCE_SUFFIXES or not SENDER_CONFIG.search(rel) or (not include_tests and TEST_RE.search(rel)):
            continue
        if path.name in ("package.json", "package-lock.json", "composer.json", "tsconfig.json") or path.suffix == ".lock":
            continue
        try:
            findings += config_safety_findings(rel, path.read_text(encoding="utf-8", errors="ignore").splitlines(),
                                               max_chars)
        except OSError:
            continue
    findings.extend(project.finish())
    if not modules:
        findings.append(Finding("P2", "no-module", ".", "no --module given; SDK setup location was not checked against a module"))
    order = {"P0": 0, "P1": 1, "P2": 2}
    return sorted(findings, key=lambda f: (order[f.severity], *_location_key(f.location)))


def _location_key(location: str) -> tuple[str, int]:
    """`src/a.ts:9` sorts before `src/a.ts:10`."""
    path, _, line = location.rpartition(":")
    return (path, int(line)) if line.isdigit() else (location, 0)


def module_path(root: Path, module: str) -> str | None:
    """The module relative to the project: given relative to it, relative to the working directory, or absolute."""
    given = Path(module)
    candidates = [given] if given.is_absolute() else [root / given, given.absolute()]
    # An existing directory wins; otherwise the path as written, relative to the project.
    for candidate in sorted(candidates, key=lambda c: not c.exists()):
        resolved = candidate.resolve() if candidate.exists() else Path(os.path.normpath(candidate))
        try:
            rel = resolved.relative_to(root).as_posix()
        except ValueError:
            continue
        return "" if rel == "." else rel
    return None


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("project", type=Path)
    parser.add_argument("--module", action="append", default=[], help="observability module path: relative to the project or the working directory, or absolute (repeatable)")
    parser.add_argument("--include-tests", action="store_true")
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--strict", action="store_true", help="exit 1 when any finding remains")
    args = parser.parse_args(argv)
    if not args.project.is_dir():
        print(f"not a directory: {args.project}", file=sys.stderr)
        return 1

    root = args.project.resolve()
    modules = []
    for module in args.module:
        rel = module_path(root, module)
        if rel is None:
            print(f"--module {module} is not inside {args.project}", file=sys.stderr)
            return 1
        modules.append(rel)
    findings = audit(root, modules, args.include_tests)
    if args.json:
        print(json.dumps([asdict(f) for f in findings], indent=2))
    else:
        for f in findings:
            print(f"[{f.severity}] [{f.rule}] {f.location} {f.message}")
        if not findings:
            print("ok: no findings")
    if any(f.severity == "P0" for f in findings):
        return 2
    real = [f for f in findings if f.rule != "no-module"]
    return 1 if args.strict and real else 0


if __name__ == "__main__":
    sys.exit(main())
