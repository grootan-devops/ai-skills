# Function-level instrumentation

The functions that do a request's work get a **code-level wrapper**: a decorator, annotation or
helper written directly on the OpenTelemetry API, and on the Pyroscope API where profiling
applies. Nothing in `observability.yml` drives it. A trace then shows the API work and every call
to another service, with each step's logs under the span they belong to. Start-up code, helpers
and plumbing stay out of traces: they add spans without request meaning.

The wrappers live in the service's observability module; business code uses only them, the
OpenTelemetry API and the logger ([layout](layout.md)).

## What a wrapper does

1. **Span.** Start an `INTERNAL` child span named `{Class}.{method}` (or `{module}.{function}`)
   with `code.function.name`, `code.file.path`, plus `code.line.number` and `thread.name` where the
   runtime provides them without capturing a stack per call, the function's own
   [enrichment](enrichment.md) attributes, and argument summaries chosen in code, never raw
   arguments or payloads ([safe capture](safe-capture.md)). Context flows into async work
   (executors, goroutines, tasks, promises), so children nest under the right parent instead of
   starting new traces.
2. **Profiling, heavy work only.** Code that is slow or heavy on CPU, memory or locks also runs
   inside the Pyroscope labels API with a `controller` label
   ([profiling labels](#profiling-labels)). Other functions get spans only.
3. **Step logs, on every call.** DEBUG `<name> started`, then `<name> finished` or
   `<name> failed`, with `code.function.name`, `code.file.path`, the duration as
   `app.function.duration` in seconds and a result summary (or `error.type`); a WARN
   `<name> slower than threshold` when the call took longer than
   `logging.application.slow_threshold`, or a threshold the wrapper sets for that function. Step
   logs are written whether or not a span is recorded; the log level decides what shows
   ([log-level policy](#log-level-policy)).
4. **Result.** Record a result summary (counts, sizes, IDs, outcome) as attributes. Success leaves
   the status UNSET, the OpenTelemetry default that backends read as success; the wrapper never
   sets OK.
5. **Failure.** Set `error.type` and status ERROR with a sanitised message. The exception is
   recorded once: inner layers mark their span, write their DEBUG `failed` step log and rethrow,
   and the boundary that handles the failure writes the one ERROR log with `exception.*`.
   Logging errors at every layer would turn one failure into several error reports.
6. **Always** end the span and restore the labels, in `finally` or `defer`, so an exception or an
   early return can't leak a span or leave labels on the thread.

The same steps as language-neutral pseudo-code (`<name>` is fixed per function, so each message
stays a constant template):

```text
traced(function, name = "{Class}.{method}", attributes, heavy = false, threshold = slow_threshold):
  span = start INTERNAL span <name> with code.*, thread.name and attributes,
         only inside a trace (a request, a job run or a WebSocket message)
  if heavy: set profiling labels (controller, optional operation;   # Node.js: synchronous code only
            span_id and trace_id on event loops, signals/profiling.md)
  log DEBUG "<name> started"
  try:
    result = call function
    record a result summary on span  # status stays UNSET
    log DEBUG "<name> finished" with app.function.duration
    if duration > threshold: log WARN "<name> slower than threshold" with app.function.duration
    return result
  on error:
    set error.type and status ERROR (sanitised message) on span
    log DEBUG "<name> failed" with app.function.duration and error.type
    rethrow                          # the boundary that handles it writes the ERROR log
  finally:
    restore profiling labels; end span
```

A sampled request then produces a tree like this:

```text
SERVER     POST /orders                 access log at response end (INFO)
  INTERNAL   OrderController.create     DEBUG started, DEBUG finished
    INTERNAL   OrderService.create      DEBUG started, INFO order created, DEBUG finished
      INTERNAL   PriceCalculator.total  heavy: also runs with profiling labels
      INTERNAL   OrderRepository.insert DEBUG started, DEBUG finished
        CLIENT     INSERT orders        from the database instrumentation library
      CLIENT     payments charges.create  the client wrapper for the payment provider
        CLIENT     POST /v1/charges     from the HTTP client instrumentation
```

## Scope

Wrapped: the API's own work on the path of a request, a WebSocket message or a job run (or, in a
worker, a message).

- **Entry handlers:** HTTP handlers and controllers, WebSocket message handlers, job bodies,
  message consumers, MCP tool handlers.
- **Business and data layers:** service and repository functions that do I/O (in most languages
  the async ones) and functions heavy enough to carry profiling labels.
- **Clients of other services:** every call to a third-party or internal service (see
  [calls to other services](#calls-to-other-services)).

Not wrapped, because the spans would have no request meaning:

- start-up and shutdown code: configuration loading, dependency injection, route and provider
  registration, migrations, cache warm-ups;
- helpers and utilities: formatting, parsing, validation, mapping, masking, small pure
  functions, synchronous helper methods on service classes;
- plumbing that isn't request work: middleware and filters (the framework instrumentation is
  set to skip them too, [hooks](hooks.md)), logging and error classes, decorators, schema and
  type modules, generated code, getters, setters and constructors.

Wrappers create a span only inside an existing trace (a request, a job run or a WebSocket
message), so code that runs at start-up or in the background never produces stray root spans;
its step logs are still written. A scheduled job starts its own root span for each run
([batch](workloads/batch.md)). Configure client, database and cache instrumentations the same
way (their "require parent span" option), and keep their calls outside a trace out of the client
metrics too ([metrics](signals/metrics.md)).

Volume is controlled by **sampling** and the **log level**, not by skipping functions. The
sampler named in `observability.yml` (`tracing.sampler` name and argument) decides per trace: in
an unsampled request these spans are no-ops, while the access log is still written
([traces](signals/traces.md)). Step logs are DEBUG, so at the default level `info` they are off;
`debug` shows every step. An overhead budget that counts spans per request will keep the result
in check once the load tests in [acceptance](acceptance.md) exist.

## Calls to other services

Every call to a third-party API or another service is a `CLIENT` span, so a trace shows which
dependency was called, for what, how long it took and how it ended:

- **The client's own span.** The module that talks to a dependency (an SDK wrapper, an API client,
  a repository for a remote store) wraps each operation in a `CLIENT` span named
  `{peer} {operation}`, for example `payments charges.create` or `identity users.get`.
  `service.peer.name` is the dependency's stable name. `server.address` and `server.port` come
  from its configured endpoint. Safe operation attributes are added, such as the bucket rather
  than the object key, a result count or an idempotency key. A vendor SDK that builds its own
  HTTP client is invisible to the HTTP instrumentation: use its public transport hook if it has
  one (one decorator covers every call site), otherwise wrap each call site
  ([sdk-notes.md](sdk-notes.md)).
- **HTTP calls underneath.** The HTTP client instrumentation's span nests under it, named
  `{method} {url.template}`, with `service.peer.name` from a map of the configured dependency
  hosts kept in code. `url.template` replaces ids and emails in the path with placeholders. In
  `url.full` and `url.path` ids stay visible, an email in the path becomes `{email}`, and
  storage object keys and other content in paths never appear
  ([safe capture](safe-capture.md)).
- **Domain conventions first.** Where semantic conventions define the call, use them: GenAI
  (`gen_ai.*`, usually from the GenAI instrumentation), database (`db.*`), messaging
  (`messaging.*`), RPC (`rpc.*`), MCP.
- **AI steps.** A model or embedding call is a GenAI `CLIENT` span from the provider's
  instrumentation, or from the module's model-call helper where no instrumentation covers the
  client; never both. The function wrapper doesn't add a second span around it. Agent runs,
  tool executions, retrievals, workflows, guardrails and evaluations use the module's GenAI
  helpers instead of the plain wrapper: they set the step's conventional name, operation and
  Langfuse type, record content only through the content helper, and set the WARNING level for
  a degraded success ([genai.md](signals/genai.md#step-kinds)). The same contract holds with
  the `sdk` mode, where the module's step helpers make Langfuse SDK observations.
- **No auto-instrumentation.** Calls over transports without one (raw TCP protocols, a
  scanner daemon, SMTP) have only the client span, so it carries `server.address`,
  `server.port`, `network.transport` and the outcome.
- **Failure.** `error.type` and status ERROR when the call fails, as for any function; the
  caller that handles the failure writes the one ERROR log.
- **Never captured.** Credentials, request and response bodies, and query values outside the
  allow-list.

## Profiling labels

Only code that is slow or heavy on CPU, memory or locks gets profiling labels, for example:

- file parsing and conversion, report and image generation;
- virus scans and uploads, PII redaction, prompt assembly, tokenisation and LLM stream parsing;
- serialisation, compression and cryptography;
- large loops, bulk transformations and the mapping of big query results;
- batch jobs, lock-guarded sections and pool waits.

It runs inside the runtime's Pyroscope labels API (for example `tag_wrapper` in Python,
`TagWrapper` in Go, `LabelsWrapper` in Java, `wrapWithLabels` in Node.js) with a `controller`
label naming the work, a bounded function name such as `DocumentParser.parse`, plus a bounded
`operation` label where it helps. Every other function gets a span only. Each distinct label set
is a separate profile series with its own cost, and spans already time every function, so labels
are kept for the places where CPU, memory or lock time needs attributing.

- **What the label covers depends on the runtime.** In Node.js a label covers synchronous work
  only: it doesn't cross `await`. Label the CPU-heavy synchronous sections (parsing and
  conversion, regex redaction, serialisation), never a whole async function or an async job
  body; an I/O-bound async function gains nothing from a label, because its samples are idle
  time. Other runtimes differ ([profiling](signals/profiling.md#labels-per-runtime)).
- Label values are bounded (code locations and fixed operation names), never IDs or secrets.
- Static identity labels are set once when the profiler starts
  ([data model](data-model.md#rum-and-profiling)).
- `trace_id` and `span_id` are never set by hand. Where the runtime supports span profiles, the
  span-profile integration links samples to spans; Node.js has none.

## Log-level policy

All application logging goes through the app's logger, at these levels. The log level controls
the volume: `debug` shows every function step, `info` (the default) what happened, `warn` only
what went wrong or slow.

| Level | For | Examples |
| --- | --- | --- |
| DEBUG | function steps: started, finished or failed, with the duration | `OrderService.create started` with `app.cart.items_count=3`; `OrderService.create finished` with `app.function.duration` |
| INFO | completed requests (the access log for 2xx and 3xx), job runs, WebSocket connects and disconnects, business milestones | `request completed`, `job invoice-export completed`, `websocket connected`, `order created` |
| WARN | 4xx responses, steps slower than `logging.application.slow_threshold`, retries, rejected connections | `request completed` with status 404, `OrderService.create slower than threshold`, retry 2 of 3, `websocket rejected` |
| ERROR | one record per failure, where it is handled, with `exception.*` | `payment capture failed` |
| FATAL | the process cannot continue | required application configuration missing at start-up |

TRACE is not used by the module. The access log follows the response: INFO for 2xx and 3xx, WARN
for 4xx, ERROR for 5xx. Severity numbers and the other native log fields are in the
[data model](data-model.md#logs-errors-and-trace-correlation).

## Message templates

Messages are constant templates; values go in attributes. Constant messages group well in a backend,
while an ID inside the text makes every message unique and can't be queried as a field. Secrets
never reach a log ([safe capture](safe-capture.md)), and every record carries native trace and span
IDs.

```text
Good   body: "order created"                    app.order.id=o-77  app.cart.items_count=3
Avoid  body: "order o-77 created with 3 items"
```

[Documentation index](README.md)
