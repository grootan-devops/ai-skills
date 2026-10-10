# Langfuse exporter and REST client (Go)

Go has no Langfuse SDK, so this package is the module's Langfuse side
([backends/langfuse.md](../../../references/backends/langfuse.md#app-route-default)).

| File | What it gives the module |
|---|---|
| `exporter.go` | `NewExporter(ctx, Config, Guard)`: OTLP/HTTP to `{base_url}/api/public/otel/v1/traces` with Basic auth from the keys, `x-langfuse-ingestion-version: 4` and gzip. It has its own transport, because `otlptracehttp` would read the `OTEL_EXPORTER_OTLP_*` variables the loader writes for the ops exporter (protocol, headers, certificates). It retries a 429, 502, 503 or 504 at most twice, after `Retry-After` (at most 5 s) or a short backoff. `Config.TLS` takes a private CA's pool; without it, the system pool, which reads `SSL_CERT_FILE`. The export scope and the content guard work on a copy of each span. `WithTranslation` adds the module's Langfuse-only keys (prompt link, completion start time) to that copy, so they never sit on the span the ops exporter shares. |
| `scope.go` | `ShouldExport` (`ai_context`, `ai_only`) and `IsAISpan` |
| `content.go` | `NewGuard(Decide, maxChars)`, built from the module's decision (the feature's content mode, the content sample, the redactor); the content keys, `Cap` (characters, not bytes) and `FailedStatus` |
| `client.go` | `Client`, with `Config.HTTPClient` for the module's traced client:<br>• `Prompt` for app code, or `FetchPrompt` (one fetch, no cache) where the app keeps its own cache;<br>• `Scores(size, onError)`, a bounded queue: `Send` never blocks, and `Close(ctx)` sends what is left within the grace period;<br>• `CreateScore` (stable ids). |
| `datasets.go` | `Client.Dataset(ctx, name, version)` for an experiment runner: the dataset and all its items |

`Prompt` never holds a request up for long:

- It returns a fresh copy from the cache (1 min).
- A stale copy comes back at once and is refreshed in the background.
- With no copy, it fetches within 2 s. On failure it returns the fallback, and serves it without
  fetching for 30 s.
- Requests that arrive while that first fetch runs get the fallback too, so warm the cache at start.

It returns text prompts only, with the version's `Config`.

Copy the `.go` files into the observability module (e.g. `internal/observability/langfuse`) and
keep the service's own `go.mod` versions. Then:

- **Build the guard.** Implement `Decide` from the feature registry and the yml's content ceiling.
  An experiment item may hold content whenever the ceiling is `full`.
- **Wire it per mode** (`genai.langfuse.mode`, export `app`):
  - `hybrid`: the exporter in `sdktrace.NewBatchSpanProcessor` on the module's tracer provider, next
    to the ops processor, plus one `Client` for app prompts and a `Scores` queue for scores.
  - `otel`: the exporter only; the module sends no scores. App code may still use the `Client` for
    prompts.
  - `sdk`: not available. The module's loader treats it as an invalid section: one ERROR naming
    the key, and Langfuse stays off while the ops telemetry runs as usual.
- **Make the model spans.** openai-go has no GenAI instrumentation, so the module's model-call
  helper makes the `chat {model}` / `embeddings {model}` CLIENT spans.
- **Set the span attribute limit.** It must be at least `genai.content.max_chars`: the SDK cuts a
  longer value blindly, so a JSON value ends up invalid before `Cap` can shorten it.
- **Clone before building a set.** A span's processors share its attribute slice, and
  `attribute.NewSet` sorts what it is given. The asset clones, so do the same in the module's ops
  guard: `attribute.NewSet(slices.Clone(span.Attributes())...)`.
- **Shut down in order:** the provider first (it flushes the exporter), then `Scores.Close`, within
  the grace period.

Tested with `go.opentelemetry.io/otel` v1.46.0 (Go 1.25):

- **Stand-in:** an `httptest` server that decodes the gzip OTLP payload (`go test ./...` in
  this directory).
- **Self-hosted Langfuse 4.55:**
  - generation and embedding types, the user, session and environment;
  - usage, and the prompt link;
  - a failed span at ERROR with its error type as the status message, and no content where the
    guard allowed none;
  - one score;
  - non-AI client spans left out.
