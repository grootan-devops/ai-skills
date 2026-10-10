# Batch jobs, cron jobs and CLIs

Work that runs, does its job and ends: scheduled tasks run by an in-process scheduler (cron
expressions, timers, intervals) inside a long-running service, Kubernetes Jobs and CronJobs,
one-off scripts and command-line tools. They answer no requests, so there is no access log; the
**run** is the unit of observation. Everything in [API backend](api.md) that isn't about
requests still applies: the module, the logger, function spans, errors through logs and safe
capture.

## What changes

| Concern | API backend | Job run, CLI |
|---|---|---|
| Unit of work | request | run: one execution, one scheduler tick or one manual trigger |
| Entry span | SERVER span per request | root span `job {name}` per run |
| Per-unit record | access log | one job log record (`job_run`), or one ERROR record when the run failed |
| Metrics | `/metrics`, scraped | `job_runs_total` and `job_duration_seconds`: on the service's `/metrics` for in-process jobs; pushed by a process that exits ([below](#metrics)) |
| Shutdown | flush on SIGTERM | a process that exits flushes before every exit, then exits with the run's status |
| Health probes | excluded when successful, reported when failed | none of its own |
| Module addition | `adapters/<framework>` | the `job` helper ([layout.md](../layout.md)) |

## The job helper

One helper in the observability module wraps the job body, so every job behaves the same way and
business code calls it once, from the scheduler callback, the manual trigger or `main`:

```text
result = run_job("invoice-export", trigger = cron | manual | internal, links = [], body)
  1. start the root span "job invoice-export" with no parent, links to the context that
     started the run (if any), app.job.name and app.job.trigger; bind its ids to the logger
  2. run the body inside it; the body may return the items processed and failed
  3. success: app.job.items.processed and .failed on the span; one INFO job_run record;
     failure: error.type and status ERROR on the span; one ERROR record with exception.*
  4. job_runs_total{job_name,outcome} and job_duration_seconds{job_name}; end the root span
  5. a process that exits: force-flush and shut down the providers, then return the exit code
```

## Job root span

- **One per run, always a root.** A scheduler tick, a manual trigger and work the service
  starts itself each begin a new trace: never a child of the start-up span or of whatever span
  happens to be current. Without it, a scheduled job's queries and calls run untraced, because
  the client instrumentations only trace inside a trace; the audit flags such schedulers
  (`scheduler-no-root-span`, [audit.md](../audit.md)).
- **Work that outlives a response.** A request that hands work off to finish after its response
  (a background upload, a follow-up job) starts a new run: a root span `job {name}` with
  `app.job.trigger=internal` and a link to the request's span, never a child of the request
  span that has already ended.
- **Triggered by another trace.** A run started by a pipeline or a parent process that passes
  `TRACEPARENT` and `TRACESTATE` in the environment links to that context too. Message
  processing follows the messaging rules instead
  ([async workers](api.md#async-workers-and-messaging)).
- **Kind and name.** `INTERNAL`, named `job {name}`, where the name is the job's fixed name:
  never dates, ids or arguments.
- **Attributes.** `app.job.name`; `app.job.trigger`: `cron` for a schedule, `manual` for an
  operator's trigger (an admin endpoint, a CLI), `internal` for work the service starts itself;
  when the run ends, `app.job.items.processed` and `app.job.items.failed`.
- **Status.** UNSET, also when some items failed; ERROR with `error.type` only when the run
  itself failed ([traces](../signals/traces.md#status)).
- **Children.** Nested function spans ([functions.md](../functions.md)) and CLIENT spans for
  databases, HTTP calls and messaging; their client metrics count, since they run inside a
  trace.
- **Large runs.** A run that handles many items starts a new trace for each chunk (a page, batch
  or file) with a link to the run's root span. Every function stays wrapped, the sampler decides
  per chunk, and no single trace grows without bound. Item failures are still logged where they
  are handled.
- **Overlaps.** A tick that is skipped because the previous run is still going, or that overlaps
  it, is logged at WARN.
- **Profiling.** A CPU-heavy job labels its heavy sections with `controller`; in Node.js never
  the whole async job body, because labels don't cross `await`
  ([profiling](../signals/profiling.md#labels-per-runtime)).

## Job log

One record per run, written once the outcome is known and carrying the root span's native trace
and span ids. It is the run's equivalent of the access log and is written whatever the sampling
decision.

| Field | Value |
|---|---|
| Severity and event name | INFO with event name `job_run` when the run completed; a failed run writes one ERROR record with event name `exception` instead |
| Body | a constant message per job: `job {name} completed` or `job {name} failed` |
| `app.job.name`, `app.job.trigger` | the run's identity |
| `app.job.outcome` | `success`, `partial` (completed with failed items) or `failure` |
| `app.job.duration` | run duration in seconds |
| `app.job.items.processed`, `app.job.items.failed` | what the run handled, when the body reports it |
| `exception.type`, `exception.message`, `exception.stacktrace` | on failure: this record is the run's one ERROR exception record ([signals/errors.md](../signals/errors.md)) |

Milestones during the run (a page processed, a file uploaded) are INFO; the wrapped functions'
step logs are DEBUG ([log-level policy](../functions.md#log-level-policy)). Custom attributes use
the project's namespace, `app.` by default.

## Metrics

Every run records `job_runs_total{job_name,outcome}` and `job_duration_seconds{job_name}`
([metrics](../signals/metrics.md#the-default-set)); never the run's id or trace id as a label.

- **In-process jobs** inside a long-running service record them in the service's Prometheus
  registry, so they appear on its `/metrics`.
- **A process that exits** (a Kubernetes Job or CronJob, a CLI) is gone before a scrape and runs
  no `/metrics` server. Its outcome and duration are always on the root span and the job log.
  When the team wants the job metrics from the process itself, it switches OTLP push on for the
  job and records them with OpenTelemetry instruments named so that the Prometheus translation
  gives the contract's names (a counter `job.runs` and a histogram `job.duration` in seconds,
  with the attributes `job_name` and `outcome`):

```yaml
metrics:
  prometheus:
    enabled: false                 # no /metrics server in a process that exits
  otlp:
    enabled: true                  # off by default; on for this job
    interval: 15s                  # shorter than a run; the final flush sends the rest
    exporter:
      endpoint: "${OBS_METRICS_ENDPOINT:-http://alloy:4317}"
      protocol: grpc
      compression: gzip
      timeout: 10s

logging:
  access_log:
    enabled: false                 # no requests, no access log
```

- **Temporality** stays cumulative in the SDK: each run starts its series with its own start
  time, and the collector converts for backends that need delta.

[signals/metrics.md](../signals/metrics.md) has the contract and [config.md](../config.md) the
`metrics.otlp` settings.

## Flush before exit

Exporters batch in the background, so a process that exits without flushing loses its last
spans, logs and metrics — often the ones that explain a failure.

1. End the root span and write the job log.
2. Force-flush, then shut down the tracer, meter and logger providers; the meter's final
   collection happens here. The whole step is bounded by the exporter timeout.
3. Exit with the run's status code.

- **Every exit path** goes through these steps: normal return, handled failure, explicit exit
  calls and termination signals (SIGTERM from a deadline or eviction, SIGINT from a terminal).
  Route explicit exits through one helper — some exit calls skip deferred and cleanup code
  (`os.Exit` in Go, for example).
- **Fail open:** an unreachable collector delays exit by at most the timeout and never changes
  the exit code; telemetry never fails a job.
- **Stdout logs** are shipped from the pod's log file by the collector: keep finished Job pods
  long enough for it to read the last lines, or send logs over OTLP (include `otlp` in
  `logging.output`).

## CLIs

- The span name is the command (`job {tool} {subcommand}`); arguments are recorded only as
  allow-listed flags, never raw — they may hold secrets.
- When stdout is the tool's output, logs go to stderr or OTLP so they never mix with the data
  the tool prints.
- An unreachable collector must not hang the tool: keep the export timeout short.
- Telemetry is configured by `observability.yml` like any service; a CLI on a developer machine
  can leave every exporter off.

## Kubernetes Jobs and CronJobs

- `service.name` is the job's stable name, never the generated Job or pod name; `k8s.pod.name`
  in the resource tells the runs' pods apart ([data-model.md](../data-model.md#resource)).
- The pod annotations and downward-API environment from [charts.md](../charts.md) go on the
  Job's pod template. There is no metrics port and no ServiceMonitor.

[Documentation index](../README.md)
