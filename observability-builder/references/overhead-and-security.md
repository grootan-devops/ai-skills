# Overhead and security

Instrumentation must never cost a service its performance or its security. This page is the one
place that states the performance budget and the security rules for the instrumentation itself.
Each rule is short and says why; the detail lives on the linked pages. What telemetry may
carry is in [safe-capture.md](safe-capture.md) and [data-model.md](data-model.md); how delivery
fails open is in [principles.md](principles.md#telemetry-is-never-a-production-dependency).

## Budget

The overhead test runs the same load twice: once with telemetry on, once with
`OTEL_SDK_DISABLED=true` as the baseline, both at the configured sample ratio. Telemetry passes
when the difference stays within these limits:

| Measure | Allowed increase |
| --- | --- |
| p50 latency | +5% or +1 ms, whichever is larger |
| p99 latency | +10% or +5 ms, whichever is larger |
| CPU | +10% (at least +2 points of a core), or at most +1 ms of CPU per request |
| Resident memory (RSS) | +50 MiB |
| Errors | none added |
| Export-queue drops | 0 at nominal load |

The CPU check passes when either rule does. The per-request rule is
`(cpu_seconds_on / requests_on - cpu_seconds_off / requests_off) x 1000` ms. Why: when stubs
answer in microseconds, the app does almost nothing without telemetry, so a small absolute cost
(about 0.6 ms for an AI request with 14 spans to two destinations, seen) shows as a large share of
a core. One span costs about 12 µs of CPU plus OTLP encoding (seen), so 1 ms fits about 4 spans
and 2 log records with room to spare.

These are defaults. A project may adjust them, and the report states the budget it used and why
any limit changed. A limit is never raised to make a failing run pass without the user agreeing
and that note.

### Over the CPU budget

Fix the span count first, then measure again:

- **No handler span when the SERVER span already covers it.** A function span that has the same
  name, timing and attributes as the request's SERVER span adds cost and no information.
- **One span for a tool call and a retrieval** only when they are the same operation: a tool
  whose whole work is the retrieval gets one span, not one each.
- Then look at what each remaining span costs (attribute work, content conversion, encoding).

Never raise the budget to make a run pass unless the user agrees.

### The overhead test

`scripts/overhead.py` starts the app twice, once with telemetry on and once with
`OTEL_SDK_DISABLED=true`. It drives the same fixed request mix at each, compares the results
against the budget and exits non-zero when any limit is exceeded. Run
`python3 scripts/overhead.py --help` for its options. Point the exporters at the local receiver
([acceptance.md](acceptance.md#local-receiver)), so the run measures real exports and never
reaches a real collector. Use a request mix that matches the service's real traffic: reads,
writes, a dependency call, and an AI call against the model stub where there is one. Report the
numbers for both runs ([acceptance.md](acceptance.md#overhead-and-load)).

Stubs for dependencies (the model provider, downstream APIs, the database) answer with realistic
latency: the production p50 when it is known, else tens of milliseconds for an API and hundreds
for a model. Why: against a stub that answers at once, the app's own work is tiny and every
relative number (latency %, CPU %) overstates what telemetry costs in production.

## Performance

### Never on the request path

- **Export is batched and asynchronous.** Use batch processors for spans and logs. A simple or
  synchronous processor is for tests only. Why: a synchronous export makes every request wait
  on the network.
- **Queues are bounded,** 2048 spans or log records by default. Why: an unbounded queue grows
  until the process runs out of memory when the collector is down.
- **Every exporter has a timeout** (10 s) and shutdown has a bounded flush. Why: a hung export
  must not hold a worker or a shutdown forever ([config.md](config.md#exporter-blocks)).
- **Profile uploads are asynchronous** too ([signals/profiling.md](signals/profiling.md)).

### Cheap hot paths

- **Get tracers, meters and loggers once.** Obtain them at module load and cache them; never
  call `get_tracer`, `get_meter` or `getLogger` per call. Why: a wrapper that asked for its
  tracer on every call made 24,000 lookups in one profile (seen).
- **Compile once.** Masking, sanitising and route-template patterns are built at start-up, not
  per call. Why: compiling a pattern per request costs more than the request's own work.
- **Build step-log messages lazily.** Check the level before formatting. Why: an eager
  f-string or format call runs on every call even when debug is off (seen in eval runs).
- **No telemetry calls inside tight loops.** Count inside the loop, record once after it.
- **No global lock per span.** Decide exclusions at span start or with a sampler. Why: a filter
  that took a global lock on every span end serialised all requests (seen).
- **AI content work only when capture allows it.** Check the capture mode before converting
  messages or parsing tool arguments. Why: with capture off, the conversion was still running
  on every model call (seen) ([signals/genai.md](signals/genai.md#content)).
- **Content capping is linear.** Cut once; don't re-serialise the whole value for every
  dropped item (seen).

### Size limits

- **Span attributes:** at most 128 per span and 8192 characters per value by default. The
  content helper caps AI content at `genai.content.max_chars` (8000 by default, at most the
  limit), so the SDK never cuts content blindly ([signals/genai.md](signals/genai.md#content)).
  Why: a global limit raised to 40,000 applied to every span in the service (seen). A project
  that needs longer content raises both together, the limit equal to `max_chars` with a comment
  naming it, and records the exception in the report (audit `attribute-limit-raised`).
- **Log attribute values are capped** too. Why: a user agent left uncut in logs (seen).
- **Export batches are capped:** the SDK's own limit, or about 3 MiB per OTLP or Langfuse
  request. Why: a custom transport without a cap sent requests the backend rejected (seen).

### One span per operation

Don't wrap the same outbound HTTP call twice. The service-call span with the HTTP client span
nested under it is by design ([functions.md](functions.md)); two HTTP `CLIENT` spans for one
request are not. Why: duplicate spans double export volume and confuse dependency views.

### Cardinality

- Metric labels come from the allow-lists in the data model
  ([data-model.md](data-model.md#metrics)).
- Span names stay low-cardinality: templates, never raw paths or ids
  ([signals/traces.md](signals/traces.md)).
- Profile label sets are bounded ([data-model.md](data-model.md#profile-labels)).

Why: each new label value is a new series or index entry in the backend, and in the SDK's memory.

### Profiling

CPU sampling stays at the SDK default (100 Hz). Labels go only on heavy sections, never on
I/O-bound code ([signals/profiling.md](signals/profiling.md)). Why: labels cost on every sample
and say nothing about code that waits.

### Start-up

Platform and metadata detectors run with a short timeout, 3 s in total at most, only on their
platform, and never again per request ([sdk-notes.md](sdk-notes.md#resource-detectors)). Why:
a metadata lookup hung the start of a service off its platform (seen).

### Sampling

Parent-based by default. At a public edge, use a trace-id ratio and ignore the caller's
sampled flag ([signals/traces.md](signals/traces.md#propagation)). Why: a caller must not be
able to force every request to be traced.

## Security

### Transport

- **TLS** to any backend outside the cluster or private network (SaaS backends, another
  cluster, the internet), and to any collector reached across such a boundary.
- **Plaintext only on a private hop:** loopback, a same-pod or same-task sidecar, or an
  in-cluster collector whose traffic is limited by a NetworkPolicy or carried by a service mesh
  with mTLS. The yml says which in a comment on the endpoint. Why: plaintext defaults with no
  note were copied into production (seen).
- **Certificates come from mounted files** (`tls.ca_file`, `cert_file`, `key_file`), never
  inline ([config.md](config.md#exporter-blocks)).

### Credentials

- **Exporter, collector and Langfuse credentials** come only from mounted files or secret
  environment variables, through `${file:...}` or `${env:...}` in the yml. Never in code, a yml
  literal, the image or logs ([config.md](config.md#interpolation)).
- **The loader logs names, never values,** of the variables it overrides.
- **Operator headers are not dropped silently.** An `OTEL_EXPORTER_OTLP_HEADERS` set by the
  operator is mapped into the yml's `headers` or reported. Why: a loader that deleted every
  inherited `OTEL_*` variable also deleted the collector's auth headers, with no note (seen)
  ([config.md](config.md#precedence)).

### Exposed surfaces

- **`/metrics` and profiling or debug endpoints** run on their own port, never the application
  port ([signals/metrics.md](signals/metrics.md)).
- **Bind to loopback** when the scraper runs in the same pod, task or instance: a sidecar on
  ECS or Cloud Run, an agent on a VM.
- **Bind to `0.0.0.0`** only when the scraper reaches it across the network, such as a
  Kubernetes pod scrape. Restrict it with a NetworkPolicy or security group.
- **Never route it** through a load balancer, ingress or public service.
- **The yml comment says which,** on `metrics.prometheus.host`. Why: both eval runs bound
  `0.0.0.0` with no note (seen).
- **No exporter zPages or debug endpoints** in production.
- **Process collectors** expose PIDs and paths: acceptable only on that private port.

### Identity and data

- Identity comes only from trusted middleware after authentication
  ([data-model.md](data-model.md#request-and-identity-context)).
- Personal data is masked through the shared helper, which fails closed
  ([data-model.md](data-model.md#pii-masking)). Secrets never leave the process.
- **Log formatters emit an allow-list** of extra fields, not every library's extras. Why: an
  allow-all pass-through let library fields reach the logs unchecked (seen).
- Database statements are recorded parameterised only ([safe-capture.md](safe-capture.md#rules-per-signal)).
- Baggage stays within its limits ([data-model.md](data-model.md#trace-context)).

### Supply chain

- OpenTelemetry, Pyroscope and Langfuse packages come from their official publishers only.
- Pin exact versions, of a release at least 14 days old unless it is a security fix.
- Update the lockfile and commit it with its checksums.
- No install scripts from unknown packages.

Why: telemetry packages run inside every request and see its data.

### Packaging

The image includes `observability.yml`, or the deployment mounts it. The verify step checks
the built image or the manifest. If neither is done, the report lists it as a blocking handoff,
not a footnote ([charts.md](charts.md)). Why: without the file, the service starts with no
telemetry (seen) ([config.md](config.md#validation-and-failure-behaviour)).

### Least privilege

The collector's RBAC covers only the resources it reads. The app needs no extra permissions for
telemetry ([collector/README.md](collector/README.md)).

## Browser RUM

The page pays for RUM in download size, parse time and main-thread work, so it has its own budget
([signals/rum.md](signals/rum.md#performance)). `scripts/rum_overhead.mjs` loads the same page
with RUM on and off under a 4x CPU slowdown, runs one scripted interaction, and compares the
medians. Playwright comes from the project; run `node scripts/rum_overhead.mjs --help` for the
options. The "off" side is either the same build with `rum.enabled: false` (runtime cost only),
or a build with the RUM module replaced by no-op stubs (size and runtime cost). Report which.

| Measure | Allowed increase |
| --- | --- |
| Initial JavaScript (gzip) | +50 KB |
| Lazily loaded chunks (gzip) | 60 KB |
| FCP and LCP (median) | +5% or +50 ms, whichever is larger |
| Total blocking time | +50 ms |
| Slowest interaction (event timing) | +16 ms |
| Console errors | none added |

Measured on a React and React Router app with Faro 2.13: +44 KB initial JavaScript, 29 KB lazy
(the tracing chunk and the OTLP transport), +8 to +12 ms FCP and LCP, and no added blocking time
or interaction latency.

Over budget, check in this order:

- the tracing package is in the initial bundle instead of a lazy chunk;
- `reportAllChanges` is on;
- `trackResources` sends a timing for every asset of a heavy page (keep it, but check the batch
  count);
- the scrubber walks large payloads (a custom `pushEvent` with big attributes);
- start-up blocks the render.

Security rules for the page and its receiver:

- **Receiver exposure.** Transport `faro` exposes only `POST /collect` and its preflight, over
  TLS, with exact CORS origins, per-app rate limiting and a payload cap. Transport `proxy`
  exposes nothing; the proxy checks `Origin`, caps the body and strips cookies, `Referer` and
  `Authorization` ([workloads/spa.md](workloads/spa.md#the-proxy-transport-proxy)).
- **Keys.** The Faro key in transport `faro` is public. Never reuse a backend credential for
  it, and never put a backend key in page code (`browser-key`, `faro-key-in-bundle`).
- **Source maps.** Hidden in the build, never served, and given to the receiver from files.
  Receiver downloads stay off, so no browser input makes the collector fetch a URL.
- **CSP.** `connect-src` lists the receiver origin only for transport `faro` or `otlp`; Faro
  needs no `script-src` or `unsafe-*` exception.
- **Supply chain.** All Faro packages on one release at least 14 days old, from the npm
  registry, bundled with the app, never loaded from a CDN at run time.

## Checks

The audit catches the static cases ([audit.md](audit.md#1-static-scan)): `sync-exporter`,
`exporter-no-timeout`, `plaintext-remote-endpoint`, `telemetry-credential-literal`,
`metrics-public-bind`, `attribute-limit-raised`, `log-extras-passthrough` and
`eager-debug-format`, and for browser RUM `faro-propagate-all`, `faro-user-pii`,
`faro-key-in-bundle`, `faro-no-scrub`, `faro-public-sourcemaps` and `faro-report-all-changes`. The overhead test and the security checks run in the instrument verify
step ([instrument.md](instrument.md#7-verify)) and in acceptance
([acceptance.md](acceptance.md#overhead-and-load),
[acceptance.md](acceptance.md#security-of-the-instrumentation)).

[Documentation index](README.md)
