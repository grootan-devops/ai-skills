# Safe capture

Telemetry is safe by construction: the code that records a value decides, at that point, whether
and how it may be recorded. This page gives the rules per signal, what is deliberately visible, how
third-party instrumentation is configured, how the audit checks all of it, and what happens when
sanitising fails.

## Principle

**Masking and safe capture live in the instrumentation code, and the telemetry-safety audit checks
that code.** Developers and AI agents write telemetry that is safe by construction; nothing captures
everything and relies on a redactor or configuration to clean it up later. So there is:

- no privacy section in `observability.yml`;
- no generic masking framework;
- no redaction processor in the SDK or the collector.

A downstream redactor has to recognise secrets by pattern after they have been captured, and every
pattern it misses is a leak that has already left the process. The code that sets an attribute
knows what the value is, so the decision belongs there, where a static scan can also check it. The
access-log allow-lists in `observability.yml` say what to capture, not how to mask it.

## Rules per signal

| Signal | Rule the instrumentation code follows |
|---|---|
| Traces | only approved semantic and business attributes ([operations](data-model.md#operations), [enrichment](enrichment.md)) plus body sizes and summaries; never passwords, tokens, API keys, `Authorization` or `Cookie` values, full request or response bodies, or other secrets. Function wrappers included: no raw arguments or payloads as attributes. AI content only on the Langfuse path (row below). |
| Logs | explicit allow-lists for headers, cookies and body fields (`logging.access_log` in `observability.yml`); full-body logging is off by default and, when switched on, bounded, temporary and logs-only. Messages are constant strings and values go in attributes, because a log sanitiser never sanitises message text: emails, file paths or object keys, original file names, cache keys, search text and upstream response text are never interpolated into a message |
| Metrics | short, bounded labels only; never user, session, tenant, order or request IDs, raw paths, secrets or other unbounded values |
| Baggage | allow-listed keys only; never secrets, request content or the AI trace context |
| AI content | prompts, completions, system instructions, tool arguments and results, retrieved documents: recorded only through the module's content helper, which applies the feature's mode (the lower of the environment's `genai.content.capture` and the feature's own), the content sample, redaction where the attribute is created and the size cap; sent to Langfuse only, never to logs, metrics, span events, baggage or the ops backend, where AI spans keep an allow-list of keys ([genai](signals/genai.md#content)) |
| Profiles | bounded operational labels only (`controller`, `operation`); never IDs or secrets |
| RUM | no input values, element text, secrets or unsafe URL parameters; query values are dropped except for allow-listed parameters |
| Session replay | the replay provider's native masking and blocking for passwords, cards, one-time codes, personal-data fields and free text ([below](#browser-and-session-replay)) |
| Request paths | a path segment that holds an email is masked as `{email}`: in `url.path` on SERVER spans, in the access log and in error-log context, and in `url.full` and `url.path` on CLIENT spans. Ids stay visible: emails are the only identifiers masked |
| Exceptions, URLs, database queries | sanitised before recording: exception messages as templates without payloads or credentials; `url.query` and `url.full` keep only allow-listed parameter values, emails in paths are masked (row above) and other content in paths, such as storage object keys, is templated; `db.query.text` is parameterised (no literals), or `db.query.summary` is used instead |

Bodies are captured only in logs, never in span attributes. Selected fields come first, a small
masked capture next, and the full body only as a last resort: bounded, temporary and explicitly
switched on.

## Visible by design

Some values are recorded as they are, because operators need them to find and debug a specific
request:

- the client IP as `client.address`, from the framework's trusted-proxy resolver; the raw
  `X-Forwarded-For` header can be forged by any client and is not logged;
- user, tenant, session and order IDs on spans and logs;
- the body fields selected in the access-log allow-lists.

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

## Browser and session replay

The OpenTelemetry browser SDK:

- never records input values or element text;
- strips the query and fragment from `url.full`, except allow-listed parameters;
- sanitises exception messages before recording them;
- sends `traceparent` only to the same origin and allow-listed origins, never to a match-all
  pattern.

Session replay (OneUptime) uses the provider's own masking:

- a masking mode: `sensitive_inputs` (the default), `all_inputs` or `all_text`; password, card,
  one-time-code and file inputs are always masked;
- mask, block and ignore selectors (the `oneuptime-mask`, `oneuptime-block` and `oneuptime-ignore`
  classes, or the equivalent replay-policy selectors) on personal-data fields: email, phone,
  address, date of birth, national ID, free-text notes and chat. In the default mode
  `oneuptime-mask` hides text but not what a user types into an input, so typed personal data
  needs `oneuptime-block`, a policy mask selector or the `MaskInputsOnly` mode
  ([rum.md](signals/rum.md#safe-capture-in-the-browser));
- attributes are recorded even under masking: elements whose `aria-label`, `title`, `alt`,
  `data-testid` or image `src` carry personal data are blocked, not masked;
- consent: replay follows the site's consent tool and honours Global Privacy Control and Do Not
  Track;
- replay starts only when its masking is in place, and not on login or OAuth callback pages,
  whose URLs carry codes.

To find the fields, scan the frontend's forms for input types `password`, `email` and `tel`;
`autocomplete` values `cc-*`, `one-time-code`, address tokens and `bday`; names such as `ssn`,
`pan`, `iban` or a national ID; textareas and search boxes over people; then the views that show
personal data (user tables, audit logs, session lists, account menus, notifications and toasts)
and attributes built from it (`aria-label={`Select ${name}`}`, avatars). Propose the selector list to the owner, mark the
confirmed elements, and apply or report the matching replay-policy settings.

## Telemetry-safety audit

Developers and AI agents own safe instrumentation; the audit checks it, on demand and in CI before
merge.

### Static scan

Instrumentation code and instrumentation-library configuration, for:

- attributes set from request bodies, headers, cookies or raw function arguments;
- secret-like keys or values;
- IDs or other unbounded values used as metric labels or profile labels;
- baggage writes outside the allow-list;
- RUM capture of input values or element text, and session replay that can start without masking;
- unsanitised exception messages, URLs or SQL, including secret-named values put into error
  messages and secrets sent as outbound query parameters;
- request bodies, whole or serialized, reaching a log, directly or through a variable read
  earlier in the same block, and log calls spread over several lines;
- header or body capture switched on in auto-instrumentation;
- sanitising code that falls back to the raw value when it fails;
- AI content set outside the module's content helper or written to logs, content capture
  switched on in code or framework configuration, Langfuse keys in source or in the page, AI
  context in baggage, and one AI library instrumented twice ([audit](audit.md)).

### Dynamic scan

The telemetry captured during a verification run (the receiver's output, stdout and `/metrics`), for:

- secret patterns: JWTs, bearer tokens, API keys, card numbers, private keys, `Authorization` and
  `Cookie` values;
- bodies in span attributes;
- unbounded metric label values;
- unexpected baggage;
- raw input values in RUM;
- AI content in the ops stream, the logs or a production run's Langfuse stream (the receiver's
  summary lists content keys per destination).

Verification seeds canary secrets (a fake JWT, a bearer token, a test API key, a password field, a
session cookie, a test card number) through every surface: headers, cookies, query strings, bodies,
database parameters, exception messages, function arguments, messaging and GenAI attributes, and
baggage. None may appear in exported OTLP, stdout logs, profiles or the collector's output, while
the visible-by-design fields still appear as they are. In the browser, canary values typed into
sensitive fields and URL queries must not appear in OTLP payloads, browser logs, URLs or replay
chunks ([acceptance](acceptance.md)).

### Report

Every finding states its severity, location, the rule broken and the fix. A report never repeats a
secret value; it names the field and the pattern that matched.

| Field | Content |
|---|---|
| Severity | `P0`, `P1` or `P2` |
| Location | `file:line` for static findings; for dynamic findings the signal, the field and the request that produced it, plus `file:line` when the source can be traced |
| Rule | the rule on this page that is broken |
| Fix | the change that makes the code follow the rule |

| Severity | Meaning | In CI |
|---|---|---|
| `P0` | a secret can reach, or did reach, exported telemetry: credentials, tokens, keys, `Authorization` or `Cookie` values, card numbers, sensitive input values (RUM included), session replay without masking, a Langfuse secret key in source or in the page | fails the check |
| `P1` | content or unbounded values where the rules don't allow them: bodies or raw arguments in span attributes, unsanitised exception messages, URLs or SQL literals, IDs as metric labels or profile labels, baggage outside the allow-list, header or body capture switched on in auto-instrumentation, sanitising that falls back to the raw value, AI content outside the content policy (in logs, on the ops path, set around the helper), AI content capture switched on in code | fails the check |
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
written raw, and the application keeps serving. Session replay starts only when its masking is in
place. Telemetry becomes less detailed, never less safe. Delivery is the opposite and fails open
([principles](principles.md#fail-open-for-delivery-fail-closed-for-safety)).

## Later: optional PII hashing

Not part of the standard yet: optional hashing of personal data such as email addresses, beyond
masking emails in request paths ([above](#rules-per-signal)). If hashing is added, it will be:

- server-side only, never in the browser: a key shipped to browsers is public, and a hash of a
  low-entropy value with a known key can be reversed by guessing inputs;
- keyed per privacy domain (for example `commerce-production`), with a key ID and version and a
  defined rotation;
- written to a separate `user.hash` field, so nothing is hashed twice.

[Documentation index](README.md)
