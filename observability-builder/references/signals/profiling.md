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
3. **Same identity as the other signals.** The application name is `service.name`. The other
   resource attributes are the static labels, in Pyroscope syntax with dots replaced by
   underscores: `service_namespace`, `service_version`, `deployment_environment_name`,
   `k8s_namespace_name`, `k8s_pod_name`, `k8s_pod_uid` and `k8s_node_name`, plus
   `k8s_cluster_name` when the resource has `k8s.cluster.name`; empty values are left out
   ([data-model.md](../data-model.md#resource)). Labels a profiler adds of its own stay
   as they are: the SDK is never patched to remove them ([sdk-notes.md](../sdk-notes.md)).
4. **Sample rate** comes from `profiling.sample_rate`, in samples per second, translated to each
   profiler's own setting (a rate or an interval).
5. **Profile types where the runtime supports them.** CPU is available everywhere; the others
   depend on the runtime and its profiler. Typical sets, to check against the profiler's docs at
   run time:

   | Runtime | Typical profile types |
   |---|---|
   | Go | CPU, allocated and in-use memory, goroutines, mutex and block contention |
   | Java | CPU or wall clock, allocations, lock contention |
   | Python | CPU |
   | Node.js | CPU (wall clock), heap |

6. **Span profiles.** Samples carry the span they ran under, so a trace opens its profile
   ([span profiles](#span-profiles)), on a backend that indexes the ids apart from its labels.
   Use a span-profile integration where it attributes samples to the right request. On an event
   loop (Node.js, Python asyncio) none does: a thread or process-wide tag follows the thread, not
   the request, so concurrent requests would swap span ids. There the module adds the span ids
   to the labels of its heavy synchronous sections instead, which no other request can enter.
7. **`controller` labels on heavy code only.** Code that is slow or heavy on CPU, memory or
   locks (file parsing, scans and uploads, PII redaction, prompt assembly and tokenisation, LLM
   stream parsing, batch jobs) runs inside the profiler's labels API with `controller` set to a
   bounded function name, plus a bounded `operation` label where it helps. What a label covers depends on the runtime
   ([below](#labels-per-runtime)). Other functions get spans only
   ([functions.md](../functions.md#profiling-labels)).
8. **Bounded labels only.** Never trace, session, correlation, user or request ids, or secrets,
   as labels. `span_id` and `trace_id` are the one exception, only for span profiles (rule 6), on
   a backend that indexes them apart from its labels.
9. **One profiler per process.** A process is CPU-profiled either by its SDK or by the
   collector's eBPF profiler, never both ([collector](../collector/README.md)).

## Labels per runtime

A `controller` label is useful only where it lands on the samples of the work it names.

| Runtime | How a label attaches | What to label |
|---|---|---|
| Node.js (`@pyroscope/nodejs` 0.6) | `wrapWithLabels` sets the label, runs the call and resets the label when the call returns. It has no async context, so a label doesn't cross `await`: samples taken after an `await` inside the labelled function carry no `controller`. | The CPU-heavy synchronous sections: parsing and conversion, regex redaction, serialisation. Never a whole async function or an async job body: an I/O-bound async function gains nothing from a label, because its samples are idle time. |
| Go | pprof labels carried by the context | Check the SDK. |
| Java, Python | the agent's labels, kept per thread | Check the SDK. |

## Span profiles

Samples get the `span_id` and `trace_id` of the span they ran under (an integration also adds
`span_name`); the backend turns the ids into links from the trace to its profile.

| Runtime | Integration | Where it fits |
|---|---|---|
| Go | `otel-profiling-go`: wrap the SDK tracer provider | every server |
| Python, threaded | `pyroscope-otel`: a span processor that tags the thread with the local root span | servers that run one request per thread at a time (gunicorn sync and gthread workers) |
| Python, asyncio | the module's labels: its heavy-section helper adds the current span's ids to the thread tags around the synchronous call | the labelled heavy sections; the rest of the request isn't linked. `pyroscope-otel` (Grafana's Python span profiles) tags the thread for the whole root span, so on asyncio a request that awaits hands its tag to the next one |
| Java | `io.pyroscope:otel`'s `PyroscopeOtelSpanProcessor` on the SDK tracer provider; the ids reach pprof through the JFR-to-pprof exporter | every server; root spans only by default, every span (`setRootSpanOnly(false)`) where a request moves threads, as on Quarkus (Vert.x event loop, then a worker) |
| Node.js | the module's labels: its heavy-section helper adds the active span's ids next to `controller` (`setLabels` or `wrapWithLabels`, synchronous only) | the labelled heavy sections. Pyroscope has no Node.js span profiles: its labels have no async context, so they can't follow a request across `await` |

The ids are unbounded, so turn the integration on only for a backend that indexes them apart
from its labels. OneUptime does: `trace_id` and `span_id` sample labels become span links
(verified end to end with OneUptime 14 on Go, Python, Java and Node.js). For another backend, check how it stores
sample labels first; where it would turn them into series labels, leave span profiles out.

## Backend format limits

Backends differ in the profile formats they accept; check the backend's ingest docs.

- **JFR:** some backends can't ingest it (e.g. OneUptime). The service still profiles itself: the
  Java agent keeps recording JFR and an exporter in the module converts each snapshot to pprof
  with the JDK's JFR reader, one upload per event type, labels and span ids kept
  (`assets/java/PprofExporter.java`, [sdk-notes.md](../sdk-notes.md#java)). A collector-side
  profiler is no substitute for the service's own profiles.
- **One sample type per pprof upload:** some backends keep only one type from a combined upload
  (e.g. OneUptime). Multi-type profiles, such as a heap profile with several sample types, are
  split into one upload per type, each keeping its units.
- **Upload size:** proxies and gateways in front of the backend must accept the profiler's
  upload size.

Details per backend: [backends](../backends/README.md).

## Fields

Profile labels, static and dynamic, are defined in [data-model.md](../data-model.md):

| Label | Source |
|---|---|
| application name | `service.name` |
| `service_namespace`, `service_version`, `deployment_environment_name`, `k8s_namespace_name`, `k8s_pod_name`, `k8s_pod_uid`, `k8s_node_name`, and `k8s_cluster_name` when set | the resource, set once as static labels when the profiler starts |
| `controller`, `operation` | the labels wrapper on heavy code |
| `span_id` | a span-profile integration only |
| labels the profiler adds itself, e.g. `process.runtime.name`, `process.runtime.version` and `otel.scope.*` from the Node.js SDK | the SDK; they can't be dropped without patching it ([sdk-notes.md](../sdk-notes.md)) |

## Configuration

| Key | Default | Notes |
|---|---|---|
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
