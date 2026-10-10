# Access log

One structured log record per HTTP request, written by the outermost middleware when the
response ends. It is on by default and exists for every request, whether or not its trace was
sampled. It is the only place where request headers, cookies and body content may be recorded,
and only from explicit allow-lists.

## Required behaviour

1. **One record per request at response end,** when the response completes or the connection
   closes, from the middleware described in [hooks.md](../hooks.md). Unsampled requests are
   logged too: the record carries the trace and span ids either way, and its trace flags show
   whether the trace was sampled.
2. **Level by status:** ERROR for 5xx, WARN for 4xx, INFO for 2xx and 3xx. The access log has
   its own switch, `logging.access_log.enabled`, and the log level controls its volume like any
   record's: at `warn`, only 4xx and 5xx requests are logged
   ([log-level policy](../functions.md#log-level-policy)). Where the API hides the outcome behind
   its status code (a 2xx envelope, a catch-all 4xx) and the team opts in, the level follows the
   real outcome and the record carries `app.result.status`
   ([errors.md](errors.md#required-behaviour), item 11).
3. **Client address** comes from the framework's trusted-proxy resolver as `client.address`.
   The raw `X-Forwarded-For` is never logged.
4. **Allow-listed headers only,** recorded as `http.request.header.<name>` and
   `http.response.header.<name>`. The schema rejects `authorization`, `proxy-authorization`,
   `cookie`, `set-cookie`, `x-forwarded-for`, `forwarded` and `x-real-ip`.
5. **Named cookies only,** recorded as `http.request.cookie.<name>`. The raw `Cookie` and
   `Set-Cookie` headers are never logged, and a cookie whose value is a credential (session
   token, auth, CSRF) is never named.
6. **Bodies are off by default and go to logs only,** never to span attributes. When a body is
   needed, use the first step that is enough:

   | Step | Setting | Use |
   |---|---|---|
   | 1. Selected fields | `body.fields: [amount, currency]` | the normal case; the named JSON fields are logged as they are, so never name a secret |
   | 2. Small masked capture | `body.full: true` with a small `max_bytes`, e.g. `1024` | when fields aren't enough; the body passes through the module's masking code first |
   | 3. Full body | `body.full: true` with a larger `max_bytes` | last resort: bounded, temporary, switched on explicitly and off again after the investigation |

   Bodies are teed without consuming the stream and capped at `max_bytes`. Fields are read only
   from complete JSON bodies; multipart and binary bodies are never captured.
7. **The framework's default access log is turned off,** so each request has exactly one
   access-log record.
8. **Same format and output as application logs:** `logging.format` (`json` or `plain`) and
   `logging.output` (stdout, OTLP or both) apply ([app-logs.md](app-logs.md)).

## Fields

Native fields and attribute rules are in [data-model.md](../data-model.md); the record holds:

| Group | Fields |
|---|---|
| Native | timestamp, severity by status, body (the constant message `request completed`), trace id, span id, trace flags, event name `access_log` |
| HTTP server | the HTTP server span's attributes: `http.request.method`, `http.route` (template or `unmatched`), `url.scheme`, `url.path`, `url.query` (allow-listed parameter values only), `server.address`, `server.port`, `client.address`, `user_agent.original`, `network.protocol.version`, `http.request.body.size`, `http.response.status_code`, `http.response.body.size`, `error.type` |
| Duration | `http.server.request.duration`, in seconds like the `http_server_request_duration_seconds` metric |
| Request context | `correlation.id`, `user.id`, `tenant.id`, `session.id`, client app and version, as set on the entry span ([enrichment.md](../enrichment.md)) |
| Allow-listed | `http.request.header.<name>`, `http.response.header.<name>`, `http.request.cookie.<name>` |
| Bodies (opt-in) | `http.request.body.field.<path>` and `http.response.body.field.<path>` for selected fields, `http.request.body.content` and `http.response.body.content` for a masked capture cut at `max_bytes` |

Header attribute names use the lowercase header name. Selected fields keep their dot path, so
`order.total` becomes `http.request.body.field.order.total`. A path segment that holds an email
is written as `{email}` in `url.path`; ids stay visible ([safe-capture.md](../safe-capture.md)).

## Configuration

| Key | Default | Notes |
|---|---|---|
| `logging.access_log.enabled` | `true` | |
| `logging.access_log.exclude_paths` | `[/health, /ready, /live, /metrics, /status]` | successful responses only; add the API docs paths; exact paths, or `/path/*` for a subtree |
| `logging.access_log.request.headers` | `[]` | lowercase header names |
| `logging.access_log.request.cookies` | `[]` | cookie names |
| `logging.access_log.response.headers` | `[]` | lowercase header names |
| `logging.access_log.{request,response}.body.fields` | `[]` | JSON field names or dot paths |
| `logging.access_log.{request,response}.body.full` | `false` | masked capture, steps 2 and 3 |
| `logging.access_log.{request,response}.body.max_bytes` | `4096` | 1 to 65536 |
| `logging.format`, `logging.output`, `logging.exporter` | | shared with application logs ([config.md](../config.md)) |

## Exclusions

A successful response (status 200–399) to a path in `logging.access_log.exclude_paths` writes
no record: the probes, `/metrics` and the service's API docs paths (for example `/api-docs` and
`/api-docs/*`). An entry matches the exact path, query string ignored, and an entry ending in
`/*` matches every path below it, so `/health` is excluded while `/api/healthcare` is logged
([config.md](../config.md#path-exclusions)). A failed response to an excluded path, other than
a 404, writes a `health.probe.failed` record in place of the access-log record: WARN, or ERROR
on 5xx, with the access-log fields, the probe and the dependency, which is the name the
readiness response itself gives the failing check, or `unknown` when it names none
([hooks.md](../hooks.md)). A 404 on an excluded path is an ordinary request with its own
access-log record.

## Failure behaviour

- **Never changes the response.** The record is written after the response, and a logging
  error is swallowed: the request has already succeeded or failed on its own.
- **Fails closed for safety.** If masking a header, cookie or body value fails, the value is
  omitted, never written raw. A body larger than `max_bytes` is cut for a full capture and not
  parsed for fields ([safe-capture.md](../safe-capture.md)).
- **Fails open for delivery.** With OTLP output, export failures are retried within bounds,
  then dropped and counted; stdout writing doesn't depend on the collector.

## Verification

[acceptance.md](../acceptance.md) checks one record per request at response end, unsampled
requests included; the level by status; only the allow-listed headers, cookies and body
fields, with raw `Cookie` and `Set-Cookie` never logged; `client.address` behind trusted
proxies, with a spoofed `X-Forwarded-For` ignored; canary secrets absent while the selected
fields appear as they are; successful probes and docs pages absent, `/api/healthcare` present
and the `health.probe.failed` record on a failed `/ready`.

[Documentation index](../README.md)
