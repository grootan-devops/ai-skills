# Application logs

The service's own log records: step logs, completed requests and job runs, business
milestones, warnings and the error records. They go through the app's logger, carry native
trace and span ids, and go to stdout by default, where the collector ships them.

## Required behaviour

1. **The language's standard logger plus the OpenTelemetry bridge** (the appender, handler or
   instrumentation for the logger the project already uses). No second logging library runs
   next to it.
2. **All application logging goes through the app's logger.** Application code never writes
   with `console.*`, `print`, `fmt.Println`, `System.out` or the like: those lines carry no
   trace ids or level, aren't exported over OTLP, and an error printed that way never becomes
   an exception record. The audit flags them (`logger-bypass`, [audit.md](../audit.md)). Tools
   whose stdout is their output keep it for the output ([batch](../workloads/batch.md#clis)).
3. **Format** `json` (default) or `plain`, from `logging.format`. Both carry the same fields
   ([line formats](#line-formats)); colour codes are never written.
4. **Native trace correlation.** Every record written inside a span carries the trace id, span
   id and trace flags as native fields: the bridge sets them for OTLP output, and the collector
   maps them from the line fields for stdout. A JSON `trace_id` field alone doesn't correlate
   until it is mapped.
5. **Constant messages.** The message is a constant string and values go in attributes
   ([message templates](../functions.md#message-templates)). A log sanitiser never sanitises
   message text, so never interpolate emails, file paths or object keys, original file names,
   cache keys, search text or upstream response text into a message. `correlation.id` is on
   every record; the full request context is on the access log and optional here.
6. **Levels** follow the [log-level policy](../functions.md#log-level-policy):
   - DEBUG: function steps started, finished or failed, with the duration (every wrapped
     function writes them);
   - INFO: completed requests (the access log for 2xx and 3xx), job runs, WebSocket connects and
     disconnects, business milestones;
   - WARN: 4xx responses, steps slower than `logging.application.slow_threshold`, retries,
     rejected connections;
   - ERROR: one record per failure, where it is handled; FATAL when the process can't continue.

   The log level controls the volume: `logging.application.level` sets the root level, so
   `debug` shows every step and `warn` only what went wrong or slow. Per-logger levels, such as
   quieting a chatty library, are set in the logging module's code. With the root at `debug`, the
   module holds third-party loggers (the framework, ORM, migrations, HTTP and database drivers,
   the telemetry SDKs) at `info` unless the configuration names them: frameworks write thousands
   of start-up lines at `debug`, and `debug` is meant to show the app's own steps.
7. **Output** is stdout by default, shipped by the collector. A service that sends directly to
   a backend without a log-shipping collector uses `otlp`, or `[stdout, otlp]` to keep stdout
   for local reading. A record is never shipped twice: a pod whose logs go over OTLP is not also
   tailed into the same backend.
8. **Safe by construction.** Secrets never reach a log. A code helper logs selected headers,
   cookies and body fields through the same allow-lists as the access log
   ([safe-capture.md](../safe-capture.md)). AI content (prompts, completions, tool arguments
   and results, retrieved documents) never reaches a log either: AI steps log ids, counts,
   sizes and token usage, the GenAI instrumentations' log events are off, and content goes only
   to the content destination ([genai.md](genai.md#content)).
9. **Errors** are ERROR records with `exception.*` attributes ([errors.md](errors.md)).
10. **stdio servers** (an MCP server on stdio, for example) write logs to stderr and keep stdout
    for the protocol.

## Line formats

Lines on stdout carry the event fields only; the collector attaches the resource from the same
values as the SDK ([data-model.md](../data-model.md)). Timestamps are RFC 3339 in UTC with at
least millisecond precision. Trace ids are 32 and span ids 16 lowercase hex characters, and
trace flags two hex characters (`01` means sampled).

### json

One JSON object per line. The fixed keys map to native fields; every other key is an attribute
with the same name.

| Key | Native field | Value |
| --- | --- | --- |
| `time` | `timeUnixNano` | event time |
| `level` | `severityText`, `severityNumber` | `TRACE`, `DEBUG`, `INFO`, `WARN`, `ERROR` or `FATAL` |
| `message` | `body` | the constant message |
| `logger` | instrumentation scope name | the logger's name |
| `trace_id`, `span_id`, `trace_flags` | `traceId`, `spanId`, `flags` | omitted outside a span or when the span context is invalid (all zeros); `trace_flags` is the W3C flags byte, where bit 0 is *sampled*. SDKs that set the random-trace-id flag (bit 1) write `03` for a sampled trace, so test bit 0, never the whole byte |
| `event_name` | `eventName` | only on event records: `exception`, `access_log`, `health.probe.failed`, `job_run`, business events |
| any other key | attribute | names from [data-model.md](../data-model.md), e.g. `correlation.id`, `exception.type` |

An error record, pretty-printed here and written as one line:

```json
{
  "time": "2026-01-15T09:15:03.481Z",
  "level": "ERROR",
  "message": "payment capture failed",
  "logger": "app.payments",
  "trace_id": "4bf92f3577b34da6a3ce929d0e0e4736",
  "span_id": "00f067aa0ba902b7",
  "trace_flags": "01",
  "event_name": "exception",
  "correlation.id": "c-7f3a",
  "exception.type": "PaymentDeclined",
  "exception.message": "provider declined the capture",
  "exception.stacktrace": "PaymentDeclined: provider declined the capture\n    at ..."
}
```

### plain

A fixed prefix that one collector regex maps to the native fields, the quoted message, then the
attributes as `key=value` pairs (logfmt; values with spaces are quoted). Inside the quoted
message, `"` and `\` are escaped with a backslash. The pair `event_name=<name>` carries the
native event name. Outside a span, the trace id, span id and trace flags are written as `-`.

```text
2026-01-15T09:15:02.123Z INFO 4bf92f3577b34da6a3ce929d0e0e4736 00f067aa0ba902b7 01 app.orders "order created" correlation.id=c-7f3a app.order.id=o-77
```

The collector's pattern:

```text
^(?P<time>\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?Z) (?P<level>TRACE|DEBUG|INFO|WARN|ERROR|FATAL) (?P<trace_id>[0-9a-f]{32}|-) (?P<span_id>[0-9a-f]{16}|-) (?P<trace_flags>[0-9a-f]{2}|-) (?P<logger>\S+) "(?P<message>(?:[^"\\]|\\.)*)"(?: (?P<attributes>.*))?$
```

**Multiline rule:** a record starts at a line that begins with a timestamp
(`^\d{4}-\d{2}-\d{2}T`); every following line that doesn't belongs to it. A stack trace is
written on those continuation lines in the runtime's own form, and the collector maps them to
`exception.stacktrace`:

```text
2026-01-15T09:15:03.481Z ERROR 4bf92f3577b34da6a3ce929d0e0e4736 00f067aa0ba902b7 01 app.payments "payment capture failed" event_name=exception correlation.id=c-7f3a exception.type=PaymentDeclined exception.message="provider declined the capture"
PaymentDeclined: provider declined the capture
    at PaymentService.capture (payment_service.ts:42:11)
    at OrderController.checkout (order_controller.ts:88:5)
```

## Fields

The native fields (timestamp, severity, body, trace id, span id, flags, event name, scope) and
the attributes (`code.function.name`, `code.file.path`, `code.line.number`, `thread.id`,
`thread.name`, `exception.*`, `correlation.id`, enrichment attributes) are defined in
[data-model.md](../data-model.md).

## Configuration

| Key | Default | Notes |
| --- | --- | --- |
| `logging.format` | `json` | `json` or `plain` |
| `logging.output` | `stdout` | `stdout`, `otlp`, or `[stdout, otlp]` |
| `logging.exporter.*` | | the OTLP exporter used when the output includes `otlp` ([config.md](../config.md)) |
| `logging.application.enabled` | `true` | `false` leaves the language's logger untouched, and the service loses standard error reporting |
| `logging.application.level` | `info` | `trace`, `debug`, `info`, `warn`, `error` or `fatal`; above `error` silences error reporting |
| `logging.application.slow_threshold` | none | a duration such as `2s` or `500ms`; a wrapped function slower than this writes a WARN step log, and unset means no slow-step warnings |

## Exclusions

Application logs have no path exclusions. Probe and API docs requests are filtered from the
access log, metrics and traces; a health handler's own logs follow the level policy, so routine
checks log at DEBUG.

## Failure behaviour

- **Logging never fails a request.** A write or export error is swallowed and counted, through
  SDK self-metrics or a rate-limited warning on stderr.
- **OTLP output** uses a batch processor with a bounded queue: it retries within bounds, drops
  and counts on overflow, and times out exports, so the service keeps serving when the
  collector or the backend is down.
- **Fails closed for safety.** A value that can't be masked is omitted, never written raw.

## Verification

[acceptance.md](../acceptance.md) checks native `traceId`, `spanId` and severity on OTLP and
stdout-shipped records (after collector mapping), severities by the level policy, step logs for
every wrapped call at `debug` and none at `info`, each exception arriving as one record, step
logs carrying span ids, no output that bypasses the logger, canary secrets absent, and the
service serving normally with the collector down.

[Documentation index](../README.md)
