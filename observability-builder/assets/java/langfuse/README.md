# Langfuse exporter and REST client (Java)

Java has no Langfuse SDK, so these classes are the module's Langfuse side
([backends/langfuse.md](../../../references/backends/langfuse.md#app-route-default)).

| File | What it gives the module |
| --- | --- |
| `LangfuseExporter.java` | `LangfuseExporter.create(config, guard)`: an `OtlpHttpSpanExporter` to `{base_url}/api/public/otel/v1/traces` with Basic auth from the keys, `x-langfuse-ingestion-version: 4` and gzip; the export scope and the content guard work on a copy of each span. A guard that throws keeps the span out (fail closed). `withTranslation` adds the module's Langfuse-only keys to that copy. |
| `ExportScope.java` | `shouldExport` (`ai_context`, `ai_only`) and `isAiSpan` |
| `ContentGuard.java` | `ContentGuard.of(decide, maxChars)`, built from the module's decision (the feature's content mode, the content sample, the redactor); the content keys, `cap` and `failedStatus` |
| `GuardedSpanData.java` | The guarded copy: a `DelegatingSpanData` with its own attributes, status and events |
| `LangfuseClient.java` | `prompt` for app code (it behaves as in the Go copy, with the version's `config`), or `fetchPrompt` (one fetch, no cache) where the app keeps its own cache. `scores(size, onError)` is a bounded queue whose `send` never blocks and whose `close(grace)` sends what is left; `createScore` takes a stable id; `dataset(name, version)` is for an experiment runner. Pass the module's traced `HttpClient` to the constructor so a prompt fetch inside a request gets its CLIENT span. |
| `LangfuseConfig.java` | The yml's values. `toString` leaves the keys out. |

Copy the files into the observability module and set the package. They need, at the service's own
versions:

- `io.opentelemetry:opentelemetry-sdk` and `opentelemetry-exporter-otlp`;
- `com.fasterxml.jackson.core:jackson-databind`;
- Java 17 or later.

The exporter brings the OkHttp sender. Where the service pins another OkHttp, exclude
`opentelemetry-exporter-sender-okhttp` and add `opentelemetry-exporter-sender-jdk`.

Then:

- **Build the guard.** Implement `ContentGuard.Decide` from the feature registry and the yml's
  content ceiling. An experiment item may hold content whenever the ceiling is `full`. With
  `genai.content.destination: ops`, `Decide` always refuses: content then goes to the ops
  backend's AI views, and Langfuse gets none.
- **Wire it per mode** (`genai.langfuse.mode`, export `app`):
  - `hybrid`: `BatchSpanProcessor.builder(LangfuseExporter.create(...))` on the module's
    `SdkTracerProvider`, next to the ops processor, plus one `LangfuseClient` for app prompts and
    its `scores(...)` queue for scores.
  - `otel`: the exporter only; the module sends no scores.
  - `sdk`: not available. The module's loader treats it as an invalid section: one ERROR naming
    the key, and Langfuse stays off while the ops telemetry runs as usual.
- **With the OpenTelemetry Java agent,** register the exporter from an agent extension
  (`AutoConfigurationCustomizer.addTracerProviderCustomizer`), or prefer the collector route.
- **Make the model spans.** The module's model-call helper makes the `chat {model}` CLIENT spans,
  unless an instrumentation such as Spring AI's already does.
- **Set the span attribute limit.** `SpanLimits` must allow at least `genai.content.max_chars`
  per value: a shorter limit cuts content blindly before `cap` can shorten it.
- **Shut down in order:** the provider first (it flushes the exporter), then `Scores.close`, within
  the grace period.

`LangfuseClient` uses HTTP/1.1. On plain `http`, the JDK client's HTTP/2 upgrade request made a
self-hosted Langfuse close the connection without an answer. Configure a client you pass in the
same way. The OTLP exporter's builder reads no `OTEL_EXPORTER_OTLP_*` variables, so the ops
exporter's settings never reach Langfuse. It retries with the SDK's default policy. A self-hosted
Langfuse behind a private CA needs that CA in the JVM truststore, or `setTrustedCertificates` on
the builder in `LangfuseExporter.create`.

Tested with OpenTelemetry Java 1.66.0, Jackson 2.22.3, and Java 17 and 21:

- **Stand-in:** a `com.sun.net.httpserver` server that decodes the gzip OTLP payload.
- **Self-hosted Langfuse 4.55:** the same results as the Go copy.
