# Instrument a project

Steps for `observability instrument`. Read the reference pages named in each step (all in this
`references/` folder); this file only orders the work and lists what to ask.

## 1. Detect

Run `python3 scripts/detect.py <project>` and read what it can't see. Establish:

- language, runtime version (production image, not just the dev machine), framework and
  version, module system and bundler (bundled first-party code can't be auto-instrumented
  function by function), process model (pre-fork, cluster, workers, serverless);
- the entrypoint and how the process starts: the container's real command (detect's
  `start_commands`, read from the Dockerfile, the entrypoint script it runs and a Procfile), the
  chart's command, and a TypeScript loader that runs source directly (detect's `loader`, such as
  `tsx`). The package.json `start` script may not be what the image runs;
- existing logging, access logging, error handling, metrics, tracing, APM agents, profilers,
  and logging that bypasses the app's logger (`console.*`, `print`, `fmt.Println`);
- outbound dependencies (databases and their connection pools, caches, queues, HTTP clients,
  cloud SDKs, GenAI SDKs) and background work (in-process schedulers, timers and intervals,
  work that continues after a response, consumers, cron jobs);
- WebSocket endpoints and the message types they handle;
- AI features: detect's `ai` field (model providers, AI frameworks, vector stores,
  LLM-observability tools, and the names of their variables in example env files); the code
  that calls models (chat, retrieval, extraction, classification, agents with tools, batch AI
  jobs, indexing), where conversations and message ids live, any human approval step, and the
  existing LLM observability with its content capture;
- health, readiness and liveness endpoints, and what they check; the API docs paths (Swagger
  UI, the OpenAPI document);
- where the verified user and tenant become available (after which middleware);
- how responses report failure: a status code, or a 2xx envelope with the outcome in the body,
  and whether a catch-all handler turns every exception into one 4xx;
- the deploy shape (chart, compose files, ingress rules), how the deployment replaces config
  files (a mounted `application.yml` given with `--spring.config.location` replaces the packaged
  one), and the project's own agent rules.

Classify the workload with the workload pages (`workloads/`) (API, worker, BFF, fullstack, SPA,
gateway, MCP server, AI agent, batch).

## 2. Ask

Suggest an answer for each question from what you found, then use what the user says.

| Question | Why it matters |
| --- | --- |
| Where does each signal go — a collector (endpoint) or directly to a backend (endpoint, auth header, token file path)? Do several clusters send to the same backend? | Sets every `exporter` block; tokens stay in mounted files; a backend shared by clusters needs `k8s.cluster.name` in the resource ([data-model.md](data-model.md#resource)); in OneUptime, profiles can't be told apart by cluster or environment within one project ([oneuptime.md](backends/oneuptime.md#profiles)) |
| Do logs go to stdout only, OTLP only, or both? | stdout-only needs a collector that ships pod logs; errors are reported from logs |
| The ops metrics are served on `/metrics` on their own port (default 9464) for Prometheus or the collector to scrape: which port, and who scrapes it? Does the team want app metrics pushed over OTLP as well (off by default)? | The port needs a chart service port and monitor; ops metrics are the default set, app metrics are added only on request |
| Log level per environment, and the slow-step threshold (`logging.application.slow_threshold`, e.g. `2s`)? | The level controls the volume; steps slower than the threshold log a WARN |
| Which scheduled and background jobs run in the process, under which names and triggers (`cron`, `manual`, `internal`)? Which work continues after a response? | Each run gets a root span `job {name}`, a job log and the job metrics |
| Which WebSocket endpoints are there, and what is the bounded set of message types? | Message spans are named `WS {type}`; other types become `WS unknown` |
| Which API docs paths does the service serve? | They are excluded from traces, metrics and the access log like the probes |
| Is profiling on now? Which heavy code gets `controller` labels? | Native profiler modules may not build on the production base image; in Node.js labels cover synchronous code only |
| Function-span scope: the API work and every call to another service (default), or a narrower first pass? Which dependencies does the service call, and by what name should each appear (`service.peer.name`)? | Start-up code, helpers and middleware never get spans; peer names make dependencies readable in every backend |
| Which request headers, response headers, named cookies and body fields may the access log carry? | Allow-lists only; nothing secret |
| Which business attributes should the main functions record? Propose them from the enrichment catalogue ([enrichment.md](enrichment.md)). | Attributes are code, not config |
| For frontends: which form fields are sensitive? Is there a consent tool? Which OneUptime host, app identifier and key type (Browser or Server) does RUM use? | Session replay and RUM masking are set in code and the replay policy; the key type decides whether the proxy passes `Origin` on |
| For AI features: which features are there, under which names (kebab-case), and which entry points start each (routes, WebSocket message types, job names, queue consumers, MCP tool handlers)? | Feature names are the Langfuse trace names; the entry points are always sampled, because LLM views count spend from the spans they receive ([genai.md](signals/genai.md)) |
| For AI features, who spent what: for each entry point, who does the AI work run for (the signed-in user, an API client or service account, the user behind a job, an MCP client's subject), and what group owns the spend (tenant, workspace, organisation, team, project, cost centre, or none)? What is the conversation id? Propose the answers from the app's auth, tenancy and billing code. | Nothing is fixed by the standard: the values come from the app's verified context and reach both LLM tools, as `user.id`, `team.id` and `gen_ai.conversation.id` on AI spans ([genai.md](signals/genai.md#who-spent-what)) |
| Which tool shows AI content, Langfuse or the ops backend's AI views (OneUptime)? Never both. | Sets `genai.content.destination`; with `ops`, content lives in the ops backend under its project's access and retention ([genai.md](signals/genai.md#destination)) |
| Does AI telemetry go to Langfuse too? Cloud (which region) or self-hosted (which URL)? One project per environment, or separate projects for production and non-production (recommended when non-production may hold real data)? The app route or the collector route? Where are the keys mounted? Which mode: `hybrid`, `sdk` (Python and TypeScript) or `otel` (no scores), and should environments switch between them? | Sets `genai.langfuse.*` and the handoff; keys stay in mounted files; app code is the same in every mode, so a variable can pick it per environment ([genai.md](signals/genai.md#modes)) |
| Which content may each environment record (`full` only where data is synthetic)? Which features may record redacted content in production, with which redactor, signed off by whom? Media as references or uploads? | Content is the riskiest telemetry an AI service has; production defaults to `off` ([genai.md](signals/genai.md#content)) |
| For MCP: does the service run an MCP server, call MCP servers, or both? Which transport (stdio, Streamable HTTP)? Does the SDK trace MCP itself? Which tools and prompts does the server register, and which servers does the client call? | One span source per side; the registered names bound the MCP metrics' `target` label ([mcp.md](workloads/mcp.md)) |
| Which models does the service call that the LLM tools can't price (self-hosted, fine-tuned, gateway aliases, deployment names)? | Unpriced models cost 0 in both tools until the platform adds their prices: a handoff ([oneuptime.md](backends/oneuptime.md#ai--llm-monitoring), [langfuse.md](backends/langfuse.md#usage-cost-and-models)) |
| Does the app use Langfuse prompt management, and where does it fetch? Which scores does the service send (user feedback, approval decisions, in-app evaluations)? Does any step wait for a person to approve? Should prompt or model changes be judged on a dataset first (experiments, and which datasets)? | Prompt management is the app's own: the instrumentation links each generation to the prompt it used and moves any Langfuse client into the module; scores need stable ids and score configs; approvals are three linked traces; an experiment runner is a command in the repository that runs where content may be `full` ([ai-agent.md](workloads/ai-agent.md)) |
| When responses hide the outcome (a 2xx envelope with a status in the body, a catch-all 4xx for server errors): should the body's status be recorded and counted? | Status-based dashboards miss those failures; opting in adds `app.result.status`, the real-outcome access-log level and `app_request_results_total` ([errors.md](signals/errors.md#required-behaviour), item 11) |
| Are breaking changes acceptable now (access-log format, log format, metric names, vendor SDK removal)? | Dashboards, alerts and parsers may depend on the old shape |

## 3. Audit what exists

Run `python3 scripts/audit.py <project>` and read the existing instrumentation. Mark each
piece conforming, fix or replace ([principles.md](principles.md), [safe-capture.md](safe-capture.md)).
Typical replacements: a custom access logger that prints bodies, a logger that can't carry
trace IDs, `console` or `print` logging beside the app's logger, ad-hoc metrics endpoints,
metrics built on the OpenTelemetry SDK with patched names, schedulers that start no trace,
vendor SDKs. Write the migration note now.

Run the project's own build, type check and tests now, before you change anything, and keep
the results per suite: step 7 compares against them. When the app can run, record its API
responses too, so step 7 can prove the contract held. List one request per route and outcome
(success, 404, 401, failure, probes) in a JSON file, then run
`python3 scripts/contract.py record --base <url> --requests <file> --out <scratch>/before.json`.
Run the app's dependencies (database, object store, cache) as throwaway containers you start and
stop yourself, with test values in a scratch env file, never the project's `.env`. When a
dependency's image is no longer published (MinIO's Docker Hub image, for example), use a
compatible stand-in such as another S3-compatible server and say so in the report. Requests that
change state between runs (a failed login that counts attempts, a create with a unique name)
make the comparison fail on its own: pick requests that answer the same way each time.

## 4. Research

Start with [sdk-notes.md](sdk-notes.md). It lists SDK and framework behaviour that
earlier runs had to find by reading SDK source: how to drop resource detectors, middleware
spans and route templates, duplicate metric sources, connection-pool statistics,
environment-variable quirks, profiler label limits and framework built-ins. Confirm the entries
that matter against the versions you install. For metrics, start from the stack's mainstream
Prometheus client library and what it provides
([signals/metrics.md](signals/metrics.md#per-stack)). Then, for each signal and each framework
or client in use, find, in this order: the framework's built-in observability, the official
OpenTelemetry instrumentation, a framework-official plugin, a maintained community library.
Check:

- current versions from the package manager (`npm view <pkg> version`,
  `pip index versions <pkg>`, `go list -m -versions <module>`, Maven Central);
- the publisher, the last release date, deprecation notices and known incompatibilities with
  the runtime and module system (for example ESM loader hooks, pre-fork servers);
- the semantic-convention stability opt-ins the SDK needs;
- the chosen backend's ingest docs (protocol, auth, temporality, profile formats);
- for AI features: one instrumentation per AI library, its content switches and the
  semantic-convention opt-in it needs ([sdk-notes.md](sdk-notes.md) has the pins that
  matter), and Langfuse's live docs for current SDK details: `https://langfuse.com/llms.txt`,
  any docs page with `.md` appended, or the docs MCP server; Langfuse's own agent skill covers
  platform work ([backends/langfuse.md](backends/langfuse.md)).

Record the source of every choice for the report.

## 5. Plan and confirm

Show the user, before editing: the files to add and change ([layout.md](layout.md)), the
libraries with versions, where each hook goes ([hooks.md](hooks.md)), the
`observability.yml`, the enrichment attributes per function, the metrics with their labels,
the jobs and WebSocket endpoints in scope, the chart handoff list and the migration note.
Start only after they confirm.

List risks and findings for the user's call only when they are about observability: signals or
paths that stay uncovered, broken trace propagation, masking and content gaps, data the
telemetry must not carry and how it is kept out, costs and cardinality, decisions such as a log
format the team keeps, and gaps in this skill. Security and code issues outside telemetry (a
secret shipped in a bundle, an auth flow that puts a token in a URL, a dependency to upgrade)
belong to other review layers: leave them out. So do the app's own uses of Langfuse (how it
fetches, caches and falls back on prompts, its datasets), which are the developers' call: at most a
line in the handoff list. Where telemetry has to work around one, describe only the telemetry
side, for example "query values are redacted on the auth callback route and replay starts after
it".

## 6. Implement

Work in this order so each step builds on the previous one:

1. Create the observability module at the layout's path for the language.
2. Config loader: read `observability.yml` (path from `OBSERVABILITY_CONFIG`, default the repo
   root), resolve interpolation, write the standard `OTEL_*` settings, warn about stray ones;
   a broken file degrades telemetry and binds no metrics port ([config.md](config.md)).
3. Entrypoint: start the SDK before the framework loads (preload flag, agent, post-fork hook),
   with exporters, the sampler, a resource of the eight standard attributes, plus
   `k8s.cluster.name` where several clusters send to one backend (the SDK's default detectors
   off where it allows it), and auto-instrumentation for the clients found in step 1.
   Configure the framework instrumentation to skip its middleware, router and handler layers
   ([hooks.md](hooks.md)).
4. Logging: JSON or plain output with native trace and span IDs, levels per the policy, no
   colours in JSON mode, constant messages with values in attributes; route every `console`,
   `print` or `fmt.Println` call in application code through the app's logger
   ([signals/app-logs.md](signals/app-logs.md)).
5. Access log middleware, outermost; request attributes on the entry span at response end;
   the route template taken when the framework matches the route, and `unmatched` as the
   route of a 404; emails in request paths masked; successful probes and API docs requests
   suppressed, failed probes reported ([signals/access-log.md](signals/access-log.md)).
6. Function wrappers: a decorator or helper on the OpenTelemetry API for the API work in scope
   (handlers, services and repositories that do I/O), never start-up code, helpers or
   middleware, and creating spans only inside a trace; DEBUG step logs on every call and a
   WARN past the slow-step threshold; client wrappers that make each call to another service
   a `{peer} {operation}` CLIENT span with `service.peer.name`, plus the dependency-host map
   and `url.template` naming for HTTP client spans; the client, database and cache
   instrumentations set to require a parent span; profiling labels only on heavy code
   ([functions.md](functions.md)).
7. Errors: one recording helper called from the global error handler and process hooks;
   response format unchanged ([signals/errors.md](signals/errors.md)).
8. Jobs: every scheduled or background run through the job helper: a root span `job {name}`,
   one job log record and the job metrics; work that continues after a response is a new run
   linked to the request ([workloads/batch.md](workloads/batch.md)).
9. WebSocket: a root span for each handshake and each message, the connected, rejected and
   disconnected records and the `websocket_*` metrics
   ([workloads/api.md](workloads/api.md#websocket)).
10. Metrics: the default set with the stack's Prometheus client library on `/metrics`, on its
    own port, with the short labels and the standard buckets; client metrics only for calls
    inside a trace, never the SDK's own exports; the database pool metrics; OTLP push only if
    the team asked for app metrics ([signals/metrics.md](signals/metrics.md)).
11. Profiling, if on: the resource attributes as static labels and `controller` labels on heavy
    code ([signals/profiling.md](signals/profiling.md)).
12. GenAI, when the service calls a model ([signals/genai.md](signals/genai.md),
    [workloads/ai-agent.md](workloads/ai-agent.md)):
    - the loader writes every capture switch from `genai.content.capture` and adds
      `gen_ai_latest_experimental` to the semantic-convention opt-in;
    - one instrumentation per AI library, started in the module; the GenAI metrics at the
      model-call hook;
    - the feature registry, `genai.feature()` at each AI entry point after authentication, with
      the user, team and conversation the plan agreed, and the stamping processor;
    - the step helpers (retrieval, memory, tools, agents, workflows, guardrails, evaluations) and
      the content helper with the features' redactors, the size cap, and the turn's input and
      output for the trace;
    - the ops guard around the ops exporter: identity onto AI spans, the allow-list, content
      only at the `ops` destination, no AI keys on other spans; and, with Langfuse on, the
      mode's wiring (the SDK's processor, or the module's OTLP exporter) on the same provider, on
      a transport that reads no `OTEL_EXPORTER_OTLP_*` variable, as the one span sender;
    - for MCP, the adapter or the SDK's own tracing, `params._meta` propagation and the MCP
      metrics ([workloads/mcp.md](workloads/mcp.md));
    - the score client in `hybrid` and `sdk`;
    - the module's Langfuse client for app code. An existing `Langfuse()` construction in the app
      moves to it, and the app's prompt code hands the prompt it used to the model-call helper.

      Copy the tested pieces: `assets/js/langfuse` for Node, and the exporter and REST client in
      `assets/go/langfuse` or `assets/java/langfuse`.
    - message ids returned with AI results and stored with their trace and observation ids,
      for feedback and approvals;
    - when experiments are wanted, the runner command: `genai.experiment()` and
      `genai.experiment_item()` around the feature's own functions, fixture data for tools, the
      evaluators and their scores ([signals/genai.md](signals/genai.md#experiments)).
13. Shutdown: flush on signals and before every explicit process exit.
14. Frontends ([signals/rum.md](signals/rum.md), [workloads/fullstack.md](workloads/fullstack.md) or [workloads/spa.md](workloads/spa.md)):
    - the browser module, started before the app renders: the web tracer, meter and logger
      providers; document-load and fetch instrumentation; route-change spans with `app.route`
      templates; error handlers; web vitals;
    - the same-origin proxy routes for OTLP and session replay; the backend key stays on the
      server;
    - runtime-served configuration, never build-time variables;
    - URL and exception-message cleaning before export;
    - masking marks: `oneuptime-block` on inputs that take personal data and on elements whose
      attributes or image sources carry it, `oneuptime-mask` on text that shows it;
    - the replay recorder loaded after the web SDK, with `session.id` taken from it, and not on
      login or OAuth callback pages;
    - the proxy forwards no `Cookie`, `Referer` or `Authorization`.

    Report the replay-policy settings the masking plan needs. The skill doesn't change them.
15. `observability.yml` and the dependency manifest. Leave lockfiles to the project's normal
    install when it needs credentials you don't have, and say so.

Follow the project's rules from step 1 throughout (env loading, error helpers, logging
sanitizers, test conventions).

## 7. Verify

- `python3 scripts/validate.py <project>/observability.yml`
- `python3 scripts/audit.py <project> --module <module-path>` — no P0 or P1 left, and no
  `logger-bypass`, `scheduler-no-root-span` or `sdk-internal-patch` finding unless the report
  says why.
- The project's build, type check and tests, run again on the same machine as the baseline
  from step 3 and compared suite by suite. A failure that was there before is not a
  regression.
  - When a dependency can't be installed without credentials (a private registry), test in a
    scratch copy: the original from the repository (for example `git archive HEAD`) and the
    changed tree, both with the same public dependencies and the private package replaced by
    the same stub. Never read credential files (`.npmrc`, `.env`, token files) to make an
    install work.
  - Two kinds of test break are expected from instrumentation. Fix them in the tests, never by
    unwrapping code: logger mocks that lack a level the new code calls (add the method), and
    tests that scan source text for a declaration the span wrappers change, for example
    `export const x = async` becoming `export const x = traced("…", async …)` (widen the
    pattern). Tests that spied on `console` move to the logger.
- `python3 scripts/contract.py compare --base <url> --requests <file> --against <scratch>/before.json`:
  every status, content type and body is unchanged, except the changes the user agreed to.
- The failure modes, each with the app still serving: the collector down, `OTEL_SDK_DISABLED=true`,
  a broken yml, and the metrics port taken. A broken yml (missing, unreadable or not valid
  YAML) gives one ERROR naming the file, key and reason, keeps logs on stdout, exports nothing
  and binds no metrics port: check that the port stays free. `python3 scripts/hold_port.py <port>`
  holds a port on every IPv4 and IPv6 address. Holding only one address lets a dual-stack bind
  succeed, so that check can pass by accident.
- **When the app can run, run it against a real receiver.** In-memory and console exporters
  never open a connection, so they can't catch a TLS, protocol, endpoint-path or timeout
  mistake. Any of these makes every export fail in production while every local check
  passes. Start the skill's receiver ([acceptance.md](acceptance.md), "Local receiver"), which
  prints its endpoints.

  ```bash
  uv run --no-project --with grpcio --with opentelemetry-proto --with protobuf --with pyyaml \
    scripts/receive.py --out <scratch>/telemetry
  ```

  Point the exporter endpoints at it through the yml's own env overrides, or through a scratch
  copy of the yml selected with `OBSERVABILITY_CONFIG`, so the project's yml stays as shipped.
  Start the app with its real start command, and send:
  - a write that calls a dependency;
  - a read, a 404 and a failure;
  - a health probe, a failed readiness probe and an API docs page;
  - where they exist, a WebSocket connection with one message, and a job run (trigger it or
    wait for its schedule).

  Stop the app with SIGTERM and read `<scratch>/telemetry/summary.md`:
  - every enabled signal arrived, with a resource of the standard attributes and nothing else
    (the service name, namespace, version and environment the yml sets, plus the `k8s.*` ones
    in a cluster, `k8s.cluster.name` included where the chart sets it);
  - each request is one trace rooted at its `SERVER` span, with API functions nested under it,
    outbound calls as `CLIENT` spans carrying the peer name and no framework middleware spans;
    the 404 has the route `unmatched`, and a failed request keeps its full route template. The
    other roots are job runs (an `INTERNAL` root `job {name}` with `app.job.name`) and WebSocket
    handshakes and messages; start-up work and helpers make none. `summary.md` flags any other
    root as not a `SERVER` span: a stray root to fix;
  - no span has status OK; the failure's spans are ERROR with `error.type`;
  - log records carry trace ids; a failure gives one ERROR record; at `debug`, every wrapped
    call has its step logs;
  - OTLP metrics arrive only if `metrics.otlp` is on;
  - profiles arrive under the service name with the resource attributes as static labels.

  For pages, run the receiver with `--replay-assets <dir>` and drive them in a headless browser:
  - get the recorder files (`loader.js`, `recorder.js`, `manifest.json`) from the OneUptime host's
    `/telemetry/session-replay/` routes or the deployment's image;
  - Playwright with an installed Chrome is enough;
  - load a page, navigate, type test values into the sensitive fields, call the API, trigger an
    error; load the OAuth callback route with a test code in its query, and set a test session
    cookie on the page's origin.

  `summary.md` should then show:
  - the browser resource and `session.id` on browser spans;
  - route-change spans with templates;
  - the API's `SERVER` span under the browser's fetch span;
  - replay chunks.

  The output folder keeps replay metadata only, so pass every test value to the receiver with
  `--canaries <file>` (a JSON `{label: value}`): its summary and `canaries.json` name each value
  that reached OTLP, Langfuse, replay events or requests, profiles or rejected requests, without
  writing it. Also grep the app's and the proxy's logs for them.

  For AI features, run the app twice against the model stub (`scripts/llm_stub.py`) and the
  receiver's Langfuse stand-in, started with `--genai-content-destination` set to the service's
  destination, once as a non-production environment with `full` content and once as production
  with `off`, with stray capture variables set to "on", and check the GenAI section of
  `summary.md` ([signals/genai.md](signals/genai.md#verification)). Run an experiment
  runner once over a dataset the stand-in serves (`--langfuse-datasets`). Never use a real
  provider key: point the client at the stub with a dummy one.

  Then:
  - scrape `/metrics` on its own port: the default set with the contract's names, labels and
    buckets, the library's process and runtime metrics, client metrics only for calls made
    inside a trace, no series for the probes or the docs page, and no id or raw path in a label
    value ([acceptance.md](acceptance.md#metrics));
  - read the access-log lines on stdout, and check that no line bypasses the logger;
  - grep the output folder and the app's stdout and stderr for every secret value you sent
    (token, cookie, API key, password, card number, debug header). None may appear.

## 8. Report

The skill version (the ai-skills release it was installed from: `git describe --tags`, or the root `VERSION`); files changed; dependencies with versions and sources; the answers
from step 2; the platform-builder handoff list ([charts.md](charts.md)); the migration note;
functions the wrappers skipped and why; checks run and their results; open observability risks
(the same scope as in step 5: nothing about security or code outside telemetry).

End with a telemetry inventory. Build it from what the app really exported in step 7, the
`/metrics` scrape and the receiver's summary, not from the code alone:

| Section | What it lists |
| --- | --- |
| Metrics | every metric family on `/metrics` with its type and label names, each marked as contract ([signals/metrics.md](signals/metrics.md)), library default or added for this app; anything in the contract the stack can't produce, and why |
| Logging outside the logger | the `logger-bypass` findings that remain (file:line), and why each one stays, for example a CLI script or init code that runs before the logger exists |
| Work outside the API | scheduled jobs, timers, queue consumers, WebSocket endpoints, background tasks and scripts: for each, what it has now (root span, job log record, metrics, profiling label) and what could still be added |
| Attributes | the resource attributes as exported, and the attributes on each kind of span (request, client, function, job, WebSocket), noting any that repeat a resource attribute or conflict with another |
| AI | each feature with its entry points, step kinds and content mode per environment, the redactors, the Langfuse route and scope, the prompts and scores used, any experiment runner and its datasets, the GenAI metrics, and the Langfuse platform handoff ([backends/langfuse.md](backends/langfuse.md#platform-handoff)) |
