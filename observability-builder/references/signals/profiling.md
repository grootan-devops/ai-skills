# Profiling

Continuous profiles show where a service spends CPU, memory and lock time, down to the function.
Profiling is off by default. When it is on, the runtime's profiler sends samples over the
Pyroscope protocol or as OTLP profiles, through the collector by default.

## Required behaviour

1. **Protocol per backend:** the Pyroscope protocol (`protocol: pyroscope`) or OTLP profiles
   (`protocol: otlp`), whichever the destination ingests. Check that the collector and the
   backend both support OTLP profiles before choosing it.
2. **Through the collector by default.** The profiler sends to the collector's profile receiver,
   which forwards to each configured backend with that backend's auth. In direct mode,
   `profiling.exporter` holds the destination and its headers ([config.md](../config.md)).
3. **Same identity as the other signals.** The application name is `service.name`. The M and C
   resource rows become static labels, set once at profiler start, in Pyroscope syntax with dots
   replaced by underscores: `service_namespace`, `service_version` and
   `deployment_environment_name` always (M), and the platform's rows when the resource has them
   (C): `k8s_namespace_name`, `k8s_pod_name`, `k8s_node_name` and `k8s_cluster_name` on
   Kubernetes; `cloud_*`, `aws_ecs_*`, `faas_*` and `gcp_cloud_run_job_*` on ECS, Fargate,
   Lambda and Cloud Run; `host_name` and `container_id` on a VM or plain Docker. Empty values
   are left out. The R and O resource rows (`telemetry_*`, `process_runtime_*`, `os_type`) are
   never set as labels by the module (P): identity is enough
   ([data-model.md](../data-model.md#profile-labels)). Labels a profiler adds of its own stay
   as they are: the SDK is never patched to remove them ([sdk-notes.md](../sdk-notes.md)).
4. **Sample rate** comes from `profiling.sample_rate`, in samples per second, translated to each
   profiler's own setting (a rate or an interval).
5. **Profile types where the runtime supports them,** by class
   ([data-model.md](../data-model.md#profile-types)); check the profiler's docs at run time:

   | Profile type | Class | Runtimes |
   | --- | --- | --- |
   | CPU | M, whenever profiling is on | all |
   | Allocation and heap (allocated objects and space, in use) | R, where the SDK supports it | Go, Java, .NET, Node.js (heap); Python with `pyroscope-io` 1.3 or later (`mem_enabled`), off by default and on only after the overhead test |
   | Lock, mutex and block contention | O | Go, Java, .NET |
   | Goroutines or threads | O | Go |
   | Wall clock | O, useful for I/O-bound services | Node.js, Java |

6. **Span profiles.** Samples carry the span they ran under, so a trace opens its profile
   ([span profiles](#span-profiles)), on a backend that indexes the ids apart from its labels.
   Use a span-profile integration where it attributes samples to the right request. On an event
   loop (Node.js, Python asyncio) none does: a thread or process-wide tag follows the thread, not
   the request, so concurrent requests would swap span ids. There the module adds the span ids
   to the labels of its heavy synchronous sections instead, which no other request can enter.
7. **`controller` labels on heavy code only.** Code that is slow or heavy on CPU, memory or
   locks runs inside the profiler's labels API with `controller` set to a bounded name, plus a
   bounded `operation` label where it helps (O). What a label covers depends on the runtime
   ([below](#labels-per-runtime)). Other functions get spans only
   ([functions.md](../functions.md#profiling-labels)). Which operations get one
   ([data model](../data-model.md#which-operations-get-a-controller-label)):

   | Operation | `controller` |
   | --- | --- |
   | Application function heavy on CPU, memory or locks (parsing, conversion, serialisation, compression, crypto, image or report generation, PII redaction, tokenisation, big loops) | C: `{Class}.{method}` |
   | GenAI prompt assembly, stream parsing | C, when heavy |
   | Job run | R: `job {name}` |
   | HTTP server entry | O: the route template, only when the service is CPU-bound per route |
   | HTTP, database, cache, messaging, RPC, MCP and A2A client calls; I/O-bound functions | P: their samples are idle time, and spans already time them |
   | Start-up, helpers, middleware | P: out of scope |

8. **Bounded labels only.** Never user, tenant, session, request or correlation ids, routes
   with ids, free text or secrets as labels (P), and `trace_id` or `span_id` are never set by
   business code. Span ids reach samples only through span profiles (rule 6), on a backend that
   indexes them apart from its labels.
9. **One profiler per process.** A process is CPU-profiled either by its SDK or by the
   collector's eBPF profiler, never both ([collector](../collector/README.md)).

## Labels per runtime

A `controller` label is useful only where it lands on the samples of the work it names.

| Runtime | How a label attaches | What to label |
| --- | --- | --- |
| Node.js (`@pyroscope/nodejs` 0.6) | `wrapWithLabels` sets the label, runs the call and resets the label when the call returns. It has no async context, so a label doesn't cross `await`: samples taken after an `await` inside the labelled function carry no `controller`. | The CPU-heavy synchronous sections: parsing and conversion, regex redaction, serialisation. Never a whole async function or an async job body: an I/O-bound async function gains nothing from a label, because its samples are idle time. |
| Go | pprof labels carried by the context | Check the SDK. |
| Java, Python | the agent's labels, kept per thread | Check the SDK. |

## Span profiles

Samples get the `span_id` and `trace_id` of the span they ran under (an integration also adds
`span_name`); the backend turns the ids into links from the trace to its profile.

| Runtime | Integration | Where it fits |
| --- | --- | --- |
| Go | `otel-profiling-go`: wrap the SDK tracer provider | every server |
| Python, threaded | `pyroscope-otel`: a span processor that tags the thread with the local root span | servers that run one request per thread at a time (gunicorn sync and gthread workers) |
| Python, asyncio | the module's labels: its heavy-section helper adds the current span's ids to the thread tags around the synchronous call | the labelled heavy sections; the rest of the request isn't linked. `pyroscope-otel` (Grafana's Python span profiles) tags the thread for the whole root span, so on asyncio a request that awaits hands its tag to the next one |
| Java | `io.pyroscope:otel`'s `PyroscopeOtelSpanProcessor` on the SDK tracer provider; the ids reach pprof through the JFR-to-pprof exporter | every server; root spans only by default, every span (`setRootSpanOnly(false)`) where a request moves threads, as on Quarkus (Vert.x event loop, then a worker) |
| Node.js | the module's labels: its heavy-section helper adds the active span's ids next to `controller` (`setLabels` or `wrapWithLabels`, synchronous only) | the labelled heavy sections. Pyroscope has no Node.js span profiles: its labels have no async context, so they can't follow a request across `await` |

The ids are unbounded, so turn the integration on only for a backend that indexes them apart
from its labels. Check how the backend stores sample labels first; where it would turn them into
series labels, leave span profiles out.

## Backend format limits

Backends differ in the profile formats they accept; check the backend's ingest docs.

- **JFR:** some backends can't ingest it. The service still profiles itself: the
  Java agent keeps recording JFR and an exporter in the module converts each snapshot to pprof
  with the JDK's JFR reader, one upload per event type, labels and span ids kept
  (`assets/java/PprofExporter.java`, [sdk-notes.md](../sdk-notes.md#java)). A collector-side
  profiler is no substitute for the service's own profiles.
- **One sample type per pprof upload:** some backends keep only one type from a combined upload.
  Multi-type profiles, such as a heap profile with several sample types, are
  split into one upload per type, each keeping its units.
- **Upload size:** proxies and gateways in front of the backend must accept the profiler's
  upload size.

Details per backend: [backends](../backends/README.md).

## Fields

Profile labels, static and dynamic, are defined in
[data-model.md](../data-model.md#profile-labels):

| Label | Kind | Class | Source |
| --- | --- | --- | --- |
| application name | static | M | `service.name` |
| `service_namespace`, `service_version`, `deployment_environment_name` | static | M | the resource, set once when the profiler starts |
| platform rows: `k8s_*`, `cloud_*`, `aws_ecs_*`, `faas_*`, `gcp_cloud_run_job_*`, `host_name`, `container_id` | static | C, when the resource has them | the resource, set once when the profiler starts |
| R and O resource rows: `telemetry_*`, `process_runtime_*`, `os_type` | static | P | never set by the module |
| `controller` | dynamic | C, code wrapped as a profiled hot path | the labels wrapper on heavy code |
| `operation` | dynamic | O, a bounded name inside a controller | the labels wrapper |
| `span_id` | correlation | C, runtime with a span-profile integration | the integration only ([span profiles](#span-profiles)) |
| ids, routes with ids, free text | — | P | — |
| labels the profiler adds itself, e.g. `process.runtime.name`, `process.runtime.version` and `otel.scope.*` from the Node.js SDK | static | — | the SDK; they can't be dropped without patching it ([sdk-notes.md](../sdk-notes.md)) |

## Configuration

| Key | Default | Notes |
| --- | --- | --- |
| `profiling.enabled` | `false` | |
| `profiling.exporter.endpoint` | `http://localhost:4040` | the collector's profile receiver, or a backend in direct mode |
| `profiling.exporter.protocol` | `pyroscope` | `pyroscope` or `otlp` |
| `profiling.exporter.headers` | `{}` | backend auth in direct mode, from `${file:...}` or `${env:...}` |
| `profiling.sample_rate` | `100` | samples per second |

## Exclusions

None by path: profiling samples the whole process, not individual requests, so it can't exclude
paths. Probe and API docs requests cost only the samples they use.

## Failure behaviour

- **Best effort.** When the destination is down, uploads fail and samples are dropped; the
  profiler never blocks a request or holds memory without bound.
- **Labels are always restored.** The labels wrapper restores the previous labels when the
  function ends, on failure too (`finally` or `defer`).
- **Off is one switch.** `profiling.enabled: false` turns the profiler off at the next start.

## Verification

[acceptance.md](../acceptance.md) checks that profiles carry the same identity as the traces,
metrics and logs (the resource attributes as static labels), that samples of the labelled heavy
code carry the `controller` label, that no id appears as a profile label, and that the service
keeps serving when the destination is down.

[Documentation index](../README.md)
