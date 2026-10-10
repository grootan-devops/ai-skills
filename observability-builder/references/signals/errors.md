# Errors

Errors are reported through logs. Each failure produces one ERROR log record with `exception.*`
attributes and native trace ids, shipped with the other logs; the backend builds its error
tracking from these records. There is no error-reporting section in `observability.yml` and no
separate error SDK.

## Required behaviour

1. **One ERROR record per failure, where it is handled.** The boundary that handles the failure
   (the global error handler, a consumer loop, a job runner, or code that catches and gives up)
   writes one ERROR record with event name `exception`, `exception.type`, `exception.message`,
   `exception.stacktrace` and the native trace and span ids. Inner layers mark their span and
   rethrow without logging, so one failure is one report.
2. **Span status.** The failing span and each caller the failure passes through get status ERROR
   and `error.type`; a caller that handles the failure and carries on stays UNSET, and no span is
   ever set to OK ([traces.md](traces.md#status)). The module's code adds no exception span
   event: span events are being deprecated, and a second copy could count the failure twice.
   Where an auto-instrumentation adds one anyway, check that the backend doesn't count it twice.
3. **Process hooks.** Uncaught exceptions, unhandled promise rejections, uncaught thread errors
   and panics reach process-level hooks, which write the record (FATAL when the process can't
   continue), flush telemetry within the shutdown timeout, then let the runtime proceed as it
   would have. Go panics are recovered in the request middleware and where each goroutine starts.
4. **4xx are not reported by default.** A client error writes no ERROR record and leaves the
   `SERVER` span UNSET; the access log records it at WARN. Reporting a specific 4xx is an
   explicit choice in the error handler's code.
5. **Recovered failures aren't errors.** A retry that succeeds or a fallback that is used is a
   WARN, without an error report. For AI steps that includes a fallback prompt or model, a
   guardrail block and a recovered tool failure, which also carry a WARNING level for Langfuse
   ([genai.md](genai.md)).
6. **`exception.*` only on the error record.** The keys belong to the one ERROR record per
   failure. A warning about a retry, a fallback or a telemetry upload that failed carries
   `error.type` and a sanitised `app.error.message` instead: backends such as OneUptime turn any
   record with an `exception.*` key into an exception, whatever its severity.
7. **Responses keep their format.** The global error handler records the failure, then returns
   the response the application returned before: the existing error format is an API contract.
   RFC 9457 `application/problem+json` is used only when the project opts in.
8. **No stack traces in responses.** Stack traces and internal exception messages never reach a
   response. If the existing format already leaks one, the audit reports it instead of the
   instrumentation silently changing the contract.
9. **Sanitised messages.** `exception.message` is a template without payloads or credentials,
   and the message line inside `exception.stacktrace` uses the same sanitised text
   ([safe-capture.md](../safe-capture.md)). Model-provider errors need care: their messages
   can quote the prompt, a tool argument or a request id with the key's prefix, so the
   provider's message is replaced by the error type and status code, in records and in the
   status description of AI spans ([genai.md](genai.md#required-behaviour)).
10. **One source.** A vendor error SDK that reports the same failures is replaced, not run next
    to this.
11. **Outcomes the status code hides.** Some APIs answer 2xx with the outcome in the body (an
    envelope such as `{"status": 500, "body": …}`), or turn every exception into one 4xx in a
    catch-all handler, so status-based dashboards and alerts never see those server failures.
    - The catch-all still reports a server failure (anything that isn't a client error) as one
      ERROR record and marks the entry span ERROR, while the response keeps its 4xx.
    - Making the body's outcome visible is the team's choice (instrument step 2). When chosen,
      the module reads the status from each response body before it is written, through the
      framework's response-body hook (Spring's `ResponseBodyAdvice`, a Nest interceptor, an
      Express `res.json` wrapper) and the code that writes the function spans'
      [result summary](../functions.md). It
      records `app.result.status` on the entry span and the access-log record, sets the access-log
      level and the span status by the real outcome (ERROR for a body status of 500 or more or a
      reported failure, WARN for 4xx in the status or the body), and counts
      `app_request_results_total{method,route,result_status,outcome}` with `outcome` one of
      `success`, `client_error` or `server_error` and `result_status` from a fixed list (else
      `other`). The HTTP metrics keep the HTTP status; the response is unchanged.

## Fields

Full definitions are in [data-model.md](../data-model.md).

| Where | Fields |
|---|---|
| Log record, native | severity ERROR (17) or FATAL (21), event name `exception`, trace id, span id, flags |
| Log record, attributes | `exception.type`, `exception.message`, `exception.stacktrace`, `code.function.name`, `code.file.path`, `code.line.number`, `thread.name`, `correlation.id` |
| Span | status ERROR with a sanitised message, `error.type` |
| Metrics | the `status_code` label on the HTTP duration metrics (`error` when a call got no response), `outcome="failure"` on `job_runs_total`, failed probes in `app_health_probe_failures_total`; no error ids or messages in labels |

On stdout the record is one line in `json`; in `plain` the stack trace follows on continuation
lines that the collector joins into the same record ([app-logs.md](app-logs.md#plain)).

## Configuration

Errors have no section of their own; they depend on the application-log settings:

| Key | Requirement |
|---|---|
| `logging.application.enabled` | stays `true`: error records are application log records |
| `logging.application.level` | `error` or lower; `fatal` silences error reporting |
| `logging.output`, `logging.exporter` | errors travel with the other logs |

## Exclusions

Errors are never excluded by path. A failing health probe is reported as
`health.probe.failed` ([hooks.md](../hooks.md)), not as an exception, unless the health handler
itself throws.

## Failure behaviour

- **Reporting never changes the outcome.** If writing or exporting the record fails, the client
  still gets the application's normal error response.
- **Fails closed for safety.** If sanitising a message fails, the message is omitted; the type
  and the stack frames remain.
- **Bounded exit.** Process hooks flush within the shutdown timeout and never hang the exit.

## Verification

[acceptance.md](../acceptance.md) checks one ERROR record per failure with `exception.*` and
native ids, arriving as one record; ERROR and `error.type` on the failing span and its callers;
no stack trace in a response and the existing error format unchanged; and canary secrets absent
from exception messages.

[Documentation index](../README.md)
