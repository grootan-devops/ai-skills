# Safe capture

Telemetry is safe by construction: the code that records a value decides, at that point, whether
and how it may be recorded. This page gives the rules per signal, the shared masking helper for
personal data, what is deliberately visible, how third-party instrumentation is configured, how
the audit checks all of it, and what happens when sanitising fails.

## Principle

**Masking and safe capture live in the instrumentation code, and the telemetry-safety audit checks
that code.** Developers and AI agents write telemetry that is safe by construction; nothing captures
everything and relies on a redactor or configuration to clean it up later. So:

- secrets are never captured, not even masked;
- personal data is written only through the module's one shared
  [masking helper](#pii-masking), called by the code that records the value;
- there is no privacy section in `observability.yml` and no redaction processor in the SDK.

A downstream redactor has to recognise values by pattern after they have been captured, and every
pattern it misses is a leak that has already left the process. The code that sets an attribute
knows what the value is, so the decision belongs there, where a static scan can also check it. A
collector redaction rule may stay as a backstop, never as the control. The access-log allow-lists
in `observability.yml` say what to capture; the masking helper says how it is written.

## Rules per signal

| Signal | Rule the instrumentation code follows |
| --- | --- |
| Traces | only approved semantic and business attributes ([operations](data-model.md#operations), [enrichment](enrichment.md)) plus body sizes and summaries; never passwords, tokens, API keys, `Authorization` or `Cookie` values, full request or response bodies, or other secrets. Function wrappers included: no raw arguments or payloads as attributes. Personal values only masked ([masking helper](#pii-masking)). AI content only on the Langfuse path (row below). |
| Logs | explicit allow-lists for headers, cookies and body fields (`logging.access_log` in `observability.yml`), each value written through the masking helper; full-body logging is off by default and, when switched on, masked, bounded, temporary and logs-only. Messages are constant strings and values go in attributes, because a log sanitiser never sanitises message text: emails, file paths or object keys, original file names, cache keys, search text and upstream response text are never interpolated into a message. Audit records carry ids, the outcome and the target's type, never the changed data |
| Metrics | short, bounded labels only; never user, tenant, org, account, customer, session, request, correlation, trace or span IDs, raw URLs or paths, SQL, error messages, emails, free text, secrets or other unbounded values ([metrics](data-model.md#metrics)) |
| Baggage | allow-listed keys only, at most 8 keys and 512 bytes; never secrets, authorisation values, request content or the AI trace context |
| AI content | prompts, completions, system instructions, tool arguments and results, retrieved documents: recorded only through the module's content helper, which applies the feature's mode (the lower of the environment's `genai.content.capture` and the feature's own), the content sample, redaction where the attribute is created and the size cap; sent only to Langfuse, never to the ops path, logs, metrics, span events or baggage; on the ops path AI spans keep an allow-list of keys ([genai](signals/genai.md#content)) |
| Browser RUM (Faro) | the opaque `user.id` only, never email, username or name; no input values, element text or DOM content; user-action names from `data-faro-user-action-name`; every item through the `beforeSend` scrubber, which fails closed ([browser RUM](#browser-rum)) |
| Profiles | static identity labels and bounded operational labels only (`controller`, `operation`); never IDs or secrets ([profile labels](data-model.md#profile-labels)) |
| Request paths | a path segment that holds personal data goes through the masking helper (an email becomes `j***@example.com`): in `url.path` on SERVER spans, in the access log and in error-log context, and in `url.full` and `url.path` on CLIENT spans. Internal ids stay visible |
| Exceptions, URLs, database queries | sanitised before recording: exception messages as templates without payloads or credentials, any personal value in them masked; `url.query` and `url.full` keep only allow-listed parameter values, personal data in paths is masked (row above) and other content in paths, such as storage object keys, is templated; `db.query.text` is parameterised (no literals), or `db.query.summary` is used instead |

Bodies are captured only in logs, never in span attributes. Selected fields come first, a small
masked capture next, and the full body only as a last resort: bounded, temporary and explicitly
switched on.

## PII masking

Personal data may appear in logs, span attributes and exception messages only in masked form,
written through the shared masking helper in the service's observability module. Every call site
that records a personal value uses it; nothing hand-rolls its own masking. The rules come from the
[data model](data-model.md#pii-masking):

| Data | The helper writes |
| --- | --- |
| Email | the first character, `***` and the domain: `j***@example.com` |
| Phone number | the last 4 digits: `***0123` |
| Person name | the first character of each part: `J*** D***` |
| Postal address | city and country only |
| Government ID, passport, tax ID | the last 4 characters |
| Bank account, IBAN | the last 4 characters |
| Date of birth | the year only |
| IP address (`client.address`) | as it is in access and audit logs and on the entry span; the last octet masked (`203.0.113.0`) everywhere else |
| Internal opaque IDs (`user.id`, `tenant.id`, order IDs) | as they are: they are pseudonymous |
| Free text, request and response bodies | nothing: only allow-listed fields, each masked by the rules above |
| Card numbers, CVV, passwords, tokens, keys, session cookies | never: these are secrets, prohibited even masked |

The helper fails closed: if a value can't be classified or masked, it returns nothing and the
field is dropped, never written raw. It is a plain function with one entry per data kind, unit
tested with the examples above, and it never logs the value it was given.

## Browser RUM

The page is the least trusted place telemetry starts: what reaches it can be read and changed by
the user. The rules ([signals/rum.md](signals/rum.md#safety)):

- **Scrub everything before send.** The `beforeSend` scrubber walks the meta and the payload,
  span attributes included. It removes query values not on `rum.allowed_query_params`,
  fragments and user info from every URL, masks emails, and redacts credentials, JWTs,
  token-like strings, Luhn-valid card numbers and secret-named fields. It drops personal user
  fields and keeps identifiers, timestamps and sizes. An item it can't scrub is dropped. OAuth
  callback pages (`?code=…&state=…`) are the usual leak: their query never leaves the page.
- **Nothing personal is set.** `setUser({ id })` with the opaque id only. Event attributes and
  measurement contexts hold bounded values; error messages are written without personal data.
- **No input capture.** Faro records no input values or element text. User-action names come
  from `data-faro-user-action-name`, never from labels or button text.
- **Keys.** The Faro key in transport `faro` is public; a backend credential is never used as it.
- **Source maps** are hidden from browsers and resolved by the receiver from files.

## Visible by design

Some values are recorded as they are, because operators need them to find and debug a specific
request:

- the client IP as `client.address`, from the framework's trusted-proxy resolver, in the access
  and audit logs and on the entry span (masked elsewhere); the raw `X-Forwarded-For` header can
  be forged by any client and is not logged;
- user, tenant, session and order IDs on spans and logs.

They are never metric labels, profile labels or default baggage.

## Third-party and auto-instrumentation

Libraries that instrument for you are configured in code by the same rules, and the audit checks
their configuration too:

- header capture off, or limited to the access-log allow-list;
- URL and query sanitising hooks that keep only allow-listed parameter values;
- SQL literal removal: parameterised statement text, or the query summary;
- request, response and message body capture off;
- GenAI content capture set from `genai.content.capture` by the loader, never left at a
  library's default, which is "on" for most: the OpenTelemetry GenAI instrumentations,
  OpenLLMetry, OpenInference (the whole `OPENINFERENCE_HIDE_*` set, embedding text included)
  and Langfuse's decorator by variable; libraries without one (the Vercel AI SDK's
  `recordInputs` and `recordOutputs`, callback handlers) by option in the module.

Set these explicitly instead of relying on a library's current defaults, which can change between
releases.

## Telemetry-safety audit

Developers and AI agents own safe instrumentation; the audit checks it, on demand and in CI before
merge.

### Static scan

Instrumentation code and instrumentation-library configuration, for:

- attributes set from request bodies, headers, cookies or raw function arguments;
- secret-like keys or values;
- IDs or other unbounded values used as metric labels or profile labels;
- baggage writes outside the allow-list;
- unsanitised exception messages, URLs or SQL, including secret-named values put into error
  messages and secrets sent as outbound query parameters;
- request bodies, whole or serialized, reaching a log, directly or through a variable read
  earlier in the same block, and log calls spread over several lines;
- personal values (emails, phone numbers, names, addresses, government IDs, dates of birth)
  written to a log, span attribute or exception message without the masking helper;
- header or body capture switched on in auto-instrumentation;
- sanitising or masking code that falls back to the raw value when it fails;
- AI content set outside the module's content helper or written to logs, content capture
  switched on in code or framework configuration, Langfuse keys in source or in the page, AI
  context in baggage, and one AI library instrumented twice ([audit](audit.md)).

### Dynamic scan

The telemetry captured during a verification run (the receiver's output, stdout and `/metrics`), for:

- secret patterns: JWTs, bearer tokens, API keys, card numbers, private keys, `Authorization` and
  `Cookie` values;
- bodies in span attributes;
- unbounded metric label values;
- raw personal values: canary emails and phone numbers may appear only masked;
- unexpected baggage, or more than 8 keys or 512 bytes;
- AI content in the ops stream, the logs or a production run's Langfuse stream (the receiver's
  summary lists content keys per stream).

Verification seeds canary secrets (a fake JWT, a bearer token, a test API key, a password field, a
session cookie, a test card number) through every surface: headers, cookies, query strings, bodies,
database parameters, exception messages, function arguments, messaging and GenAI attributes, and
baggage. None may appear in exported OTLP, stdout logs, profiles or the collector's output, while
the visible-by-design fields still appear as they are and seeded personal values appear only in
their masked form ([acceptance](acceptance.md)).

### Report

Every finding states its severity, location, the rule broken and the fix. A report never repeats a
secret value; it names the field and the pattern that matched.

| Field | Content |
| --- | --- |
| Severity | `P0`, `P1` or `P2` |
| Location | `file:line` for static findings; for dynamic findings the signal, the field and the request that produced it, plus `file:line` when the source can be traced |
| Rule | the rule on this page that is broken |
| Fix | the change that makes the code follow the rule |

| Severity | Meaning | In CI |
| --- | --- | --- |
| `P0` | a secret can reach, or did reach, exported telemetry: credentials, tokens, keys, `Authorization` or `Cookie` values, card numbers, sensitive input values, a Langfuse secret key in source or in the page | fails the check |
| `P1` | content or unbounded values where the rules don't allow them: bodies or raw arguments in span attributes, unsanitised exception messages, URLs or SQL literals, raw personal values written without the masking helper, IDs as metric labels or profile labels, baggage outside the allow-list, header or body capture switched on in auto-instrumentation, sanitising or masking that falls back to the raw value, AI content outside the content policy (in logs, on the ops path, set around the helper), AI content capture switched on in code | fails the check |
| `P2` | a safeguard missing where nothing leaks today: capture settings left at a library's defaults instead of set explicitly, no SDK attribute length limit, no cardinality limit on pushed OpenTelemetry metrics, scores sent from the browser with the public key, one AI library instrumented twice | reported, fixed in the normal flow |

```text
P0  src/orders/client.py:88  traces: no Authorization values in span attributes
    the incoming Authorization header is copied into a span attribute
    fix: remove the attribute; headers are captured only through the access-log allow-list
P1  src/orders/metrics.py:31  metrics: bounded labels only
    user_id is a label of app_orders_placed_total
    fix: drop user_id from the labels; keep user.id on the entry span
```

## Fail closed for safety

Sanitising code fails closed: if masking or sanitising a value fails, the value is omitted, never
written raw (the [masking helper](#pii-masking) returns nothing), and the application keeps serving. Telemetry becomes less detailed, never less safe. Delivery is the opposite and fails open
([principles](principles.md#fail-open-for-delivery-fail-closed-for-safety)).

## Later: optional PII hashing

Not part of the standard yet: optional hashing of personal data such as email addresses, beyond
the masking helper ([above](#pii-masking)). If hashing is added, it will be:

- server-side only, never in the browser: a key shipped to browsers is public, and a hash of a
  low-entropy value with a known key can be reversed by guessing inputs;
- keyed per privacy domain (for example `commerce-production`), with a key ID and version and a
  defined rotation;
- written to a separate `user.hash` field, so nothing is hashed twice.

[Documentation index](README.md)
