# Langfuse pieces for Node and TypeScript

Langfuse's JS SDK makes the AI observations and has the span processor. These files cover what
it doesn't ([backends/langfuse.md](../../../references/backends/langfuse.md#app-route-default)):

- the export scope;
- the content guard on a copy of each span, which also drops the request context;
- an OTLP transport that takes nothing from the ops exporter's variables;
- the score calls kept out of request traces.

| File | What it gives the module |
| --- | --- |
| `scope.ts` | `exportScope(scope, { isDefaultExportSpan })`, the processor's `shouldExportSpan` and the `otel` exporter's filter. The SDK asks it at span start too, to mark the app root, so at start it counts every span that could still join a feature; at the end the stamp decides. Also `isAiSpan`. |
| `content.ts` | `newContentGuard(decide, maxChars)` from the module's decision (the feature's content mode, the content sample, the redactor); the content keys, `cap` (characters), `failedStatus`; `guardedCopy`, which fails closed (a guard that throws keeps no content), and `copySpan`. `guardedCopy` drops the request-context and HTTP detail keys (`isRequestContextKey`: `client.*`, `user_agent.*`, request and response headers, cookies, `url.query`, `url.full`, `network.peer.*`) in every mode, the fail-closed copy included. |
| `processor.ts` | `guarded(inner, guard)`: wraps `LangfuseSpanProcessor` so it exports the guard's copy of each ended span. The SDK's `mask` sees only `langfuse.*` keys and edits the live span the ops exporter shares. |
| `exporter.ts` | Two OTLP/protobuf transports on `createOtlpHttpExportDelegate`, both reading no `OTEL_EXPORTER_OTLP_*` variable: `sdkTransport(config, { name, version })`, the `exporter` option of `LangfuseSpanProcessor` in `hybrid` and `sdk`; `LangfuseExporter`, for `otel`, with Basic auth, the ingestion header, gzip, the scope and the guard. `OTLPTraceExporter` merges the header variables into its own and takes the TLS files and compression, and can't be made to read none. Protobuf costs less CPU than JSON; Langfuse's JSON answer (`{}`) is read as no partial success, without a warning. Both keep each request body under `maxRequestBytes` (3 MiB by default) through `SizeCapped`: a batch whose high-side estimate (`estimateRequest`, nothing serialized) fits goes out whole; only a batch near the cap is measured span by span and split, and a span that alone exceeds it is dropped with a counted `diag.warn`, at most one a minute. `routeExportErrors()` makes export failures a rate-limited WARN. |
| `client.ts` | `createClient` (the process's one `LangfuseClient`), `sendScore` and `closeClient`. Both run under `suppressTracing(ROOT_CONTEXT)`: the SDK's batch timer would otherwise inherit a request's context, and its call would show up as a CLIENT span of that request. |

Copy the files into the observability module (e.g. `src/observability/langfuse/`). Then:

- **Build the guard.** Implement `Decide` from the feature registry and the yml's content ceiling.
  An experiment item may hold content whenever the ceiling is `full`.
- **Wire it per mode** (`genai.langfuse.mode`, export `app`):
  - `hybrid`: in `NodeSDK`'s `spanProcessors`, after the stamping processor and the ops processor,
    add `guarded(new LangfuseSpanProcessor({ publicKey, secretKey, baseUrl, shouldExportSpan,
    mediaUploadEnabled, exporter: sdkTransport(config, { name: LANGFUSE_SDK_NAME, version:
    LANGFUSE_SDK_VERSION }) }), guard)`.
    - Pass no `environment` or `release`: the processor would overwrite the experiment
      environment, Langfuse reads the environment from the resource, and the module stamps the
      release.
    - `mediaUploadEnabled` is true only for `media: upload` with `full`.
    - Scores go through `sendScore`.
  - `sdk`: the same processors, plus `setLangfuseTracerProvider(trace.getTracerProvider())`
    (`NodeSDK` has no accessor for its provider). The model-call helper wraps `observeOpenAI`
    over a tap of the app's client, and the steps use `startActiveObservation`
    ([sdk-notes](../../../references/sdk-notes.md#genai-and-langfuse) lists what the wrapper
    leaves to the helper).
  - `otel`: `new BatchSpanProcessor(new LangfuseExporter(config, guard))`; the module sends no
    scores.
- **Around it:**
  - an allow-listed baggage propagator, because `@langfuse/tracing` writes `langfuse_trace_id`
    into baggage;
  - `configureGlobalLogger({ level: LogLevel.NONE })` from `@langfuse/core` right after the loader;
  - `routeExportErrors()` in the preload, after `diag.setLogger(logger, DiagLogLevel.WARN)`: a
    failed export reaches the global error handler, whose default writes it as `diag.error`;
    this handler writes one WARN per distinct message a minute, the message only;
  - the span attribute value limit at 8192, with `genai.content.max_chars` (8000 by default) at
    or below it, so the guard's cap runs before the SDK cuts blindly; a project that needs more
    raises both together and records it;
  - at shutdown, the provider first, then `closeClient`.
- **TLS** to Langfuse uses the process's trust store; a private CA goes in `NODE_EXTRA_CA_CERTS`.

Tested with `@opentelemetry/api` 1.9.1, `@opentelemetry/core` and `@opentelemetry/sdk-trace`
2.11.0, `@opentelemetry/otlp-exporter-base` and `@opentelemetry/otlp-transformer` 0.222.0,
`@langfuse/*` 5.11.1, `openai` 7.23 and Node 22:

- **Conformance harness:** a reference Express app passes every check in `hybrid`, `sdk` and
  `otel`.
- **Environment check:** with ops header, TLS and compression variables set, none of them
  reached the Langfuse stand-in.

The OTLP/protobuf transport, `estimateRequest` (never below the serialized size in a randomised
check) and `routeExportErrors` were checked against a stand-in with the same versions.

`createOtlpHttpExportDelegate`, `httpAgentFactoryFromOptions`, `ProtobufTraceSerializer`,
`ISerializer` and `TraceExporterMetricsHelper` are newer, less settled exports; check their
signatures when you upgrade.
