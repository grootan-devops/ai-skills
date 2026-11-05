# Browser RUM (Grafana Faro)

What a web page emits so that a slow page, a frustrated click or a browser error can be followed
from the user's screen into the API, its logs and its profiles. Browser RUM uses the
[Grafana Faro Web SDK](https://github.com/grafana/faro-web-sdk) and nothing else. It sends to
Grafana Alloy's `faro.receiver`, which writes logs to Loki and traces to Tempo. Every feature
below works self-hosted with open-source Alloy, Loki, Tempo and Grafana. Grafana Cloud-only
features (session replay, geolocation, the Frontend Observability app) are out of scope.

This page states what Faro 2.13 does, verified end to end against Alloy 1.21 on all three
transports. Check the installed release's types and changelog before relying on an option
([sdk-notes.md](../sdk-notes.md)). The templates in [`assets/js/faro/`](../../assets/js/faro/README.md)
implement this page.

## Packages

| Package | Use |
| --- | --- |
| `@grafana/faro-web-sdk` | `initializeFaro`, `getWebInstrumentations`, the metas, the fetch transport |
| `@grafana/faro-web-tracing` | OpenTelemetry fetch and XHR spans, `traceparent` to listed API origins. Loaded lazily after start-up |
| `@grafana/faro-react` | React apps: `ReactIntegration` (router views), `FaroErrorBoundary`, `FaroProfiler`; re-exports the web SDK |
| `@grafana/faro-transport-otlp-http` | Only for transport `otlp` (experimental): Faro's model as OTLP/HTTP JSON logs and traces |

All Faro packages use the same release. Vue, Angular and Svelte use the web SDK with their own
router and error hooks ([workloads/spa.md](../workloads/spa.md)).

## Features

Every row is on in the standard. **Faro default** is what `initializeFaro` does without the
setting; **standard setting** is what the template sets. The Loki `kind` is the label the
receiver writes; for transport `otlp` the same data arrives as OTLP logs (see
[Transports](#transports)).

| Feature | Faro default | Standard setting | Emits (`kind`, name) |
| --- | --- | --- | --- |
| Errors | on | `ignoreErrors` only for known third-party noise; `preserveOriginalError` off | `exception`: type, value, stack frames (de-minified by Alloy) |
| Console | on, `debug`/`trace`/`log` off | `disabledLevels: [trace, debug, log]`, `consoleErrorAsLog: true`, `serializeErrors: false` | `log` with level `warn`/`info`/`error`. `console.error` becomes an error log, so each real failure is one exception, not two |
| Web vitals | on | `reportAllChanges: false`, `trackAttributionSources: true` | `measurement` `type=web-vitals`: `lcp`, `cls`, `inp`, `fcp`, `ttfb`, each with value, delta, rating and attribution (LCP element and render delay, INP input delay, processing and presentation, TTFB phases) |
| Performance | on | `trackResources: true` (default tracks only fetch and XHR timings) | `event` `faro.performance.navigation`, `faro.performance.resource` |
| Sessions | on | `sessionTracking: { enabled: true, persistent: false, samplingRate }` | `event` `session_start`, `session_resume`, `session_extend`; `session_id` on every record |
| Page | meta | default `page.id`; URL scrubbed | `page_url` on every record |
| View | on (`default` until set) | `faro.api.setView({ name })` with the route template on every route change, from the app's router hook | `event` `view_changed` (`toView`); `view_name` on every record |
| Navigation (soft) | **off** (experimental) | `experimental: { trackNavigation: true }` | `event` `faro.navigation` |
| User actions | on | names from `data-faro-user-action-name` only, never element text | `event` `faro.user.action` with `userActionName`; spans and events inside the action carry its name |
| Frustration | on (Faro 2.13 and later) | — | `event` `faro.frustration.rage_click`, `.dead_click`, `.error_click`; ordinary clicks send nothing. On 2.12, report it as pending the upgrade ([sdk-notes](../sdk-notes.md#browser-rum-grafana-faro)) |
| CSP violations | on | — | `event` `securitypolicyviolation` with the directive and blocked URI (scrubbed) |
| User | meta | `faro.api.setUser({ id })` with the opaque id from the verified session; nothing else | `user_id` on every record |
| Custom signals | API | `pushEvent`, `pushLog`, `pushMeasurement`, `pushError` with bounded names and allow-listed values | `event`, `log`, `measurement`, `exception` |
| Tracing | add `TracingInstrumentation` | lazy chunk; `propagateTraceHeaderCorsUrls` = exact API origins; `omitTraceContextForUnsampledSessions: true` | CLIENT spans to Tempo; `event` `faro.tracing.fetch` with `traceID` and `spanID` |
| React | add `ReactIntegration` | React Router 7/8 data router via `createReactRouterV7DataOptions({ matchRoutes })` and `withFaroRouterInstrumentation(router)`; `FaroErrorBoundary` outside the router; a reporting `errorElement` on the root route | `event` `route_change` with `toRoute` (the template); exceptions from render errors |
| Transport controls | fetch transport | `requestCompression: true`, batching on (250 ms, 50 items), `dedupe: true`, `beforeSend` scrubber, `ignoreUrls` for the receiver | — |
| Geolocation | Grafana Cloud only | `trackGeolocation: false` | — |

Three behaviours found in testing:

- **Data routers catch render errors first.** React Router's route error boundary catches
  render, loader and action errors before an outer `FaroErrorBoundary` sees them, so nothing is
  reported. Set `errorElement` on the root route to a component that reports `useRouteError()`
  through `faro.api.pushError` once (`RouteErrorReporter` in the template). Route error
  responses (a 404 from a loader) are not exceptions.
- **The React router integration doesn't set the view.** It sends `route_change` events only, so
  every record keeps `view_name=default`. The template's `instrumentRouter` also calls `setView`
  with the matched route template, and the first view is passed to `initializeFaro`.
- **Faro copies the user meta onto spans.** The tracing integration writes `user.id`,
  `user.email` and `user.name` onto every span from whatever `setUser` was given. The scrubber
  drops the personal ones, but the rule is to never pass them.

## Configuration at run time

The page reads its settings when it loads, so one build serves every environment:

- **Single-page app:** `observability.json`, fetched once, mounted per environment
  ([workloads/spa.md](../workloads/spa.md)).
- **Fullstack app:** the server renders the same JSON into the page as
  `<script type="application/json" id="rum-config">`
  ([workloads/fullstack.md](../workloads/fullstack.md#page-side-browser-rum)).

The JSON is the `service` block and the `rum` block of `observability.yml`
([config.md](../config.md#rum)), never `rum.api_key_file`. Build-time public variables
(`VITE_*`, `NEXT_PUBLIC_*`, `REACT_APP_*`) bake values into the bundle, so one image can't serve
two environments: never use them for RUM settings.

RUM stays off when the config is missing, isn't JSON (an SPA fallback answers `index.html` with
200), or has no endpoint. Starting RUM never delays the first render: the config fetch and
`initializeFaro` run alongside it, and the tracing chunk loads after start-up. Signals emitted
before start-up are lost, except web vitals, which come from buffered performance entries.

## Resource and identity

Faro's metas map onto the standard's resource and identity rows
([data-model.md](../data-model.md#browser-rum-faro)):

- `app.name` = `service.name` and `app.namespace` = `service.namespace`: the browser is part of
  the service that serves it, so the page and its server share the name. Name a frontend that is
  deployed on its own after its own deployable.
- `app.version` = `service.version`; `app.environment` = `deployment.environment.name`.
- `user.id` comes only from the app's verified session (the page's own auth state after
  sign-in), never from a value the page invents.
- `session.id` is Faro's session, rotated by Faro. It joins RUM records and spans, not backend
  logs.

## Transports

`rum.transport` picks one of three. Ask which one at the start of `instrument`; the default is
`faro`, and whether a same-origin proxy is needed is always asked
([instrument.md](../instrument.md#2-ask)).

| | `faro` (default) | `proxy` | `otlp` (experimental) |
| --- | --- | --- | --- |
| Page posts to | the public `faro.receiver` URL, `…/collect` | `/telemetry/faro/collect` on its own origin | an OTel collector's OTLP/HTTP receiver: `/v1/logs`, `/v1/traces` |
| Key | `X-API-Key` in the page. Public by design: a spam filter, not a secret | added by the proxy from a mounted Secret; never in the page | optional `x-api-key`, public as in `faro` |
| Receiver exposure | public: TLS, `cors_allowed_origins` = the app's exact origins, `per_app` rate limiting, a payload cap | in-cluster only | public collector receiver with CORS for the app's origins |
| CSP `connect-src` | `'self'`, the receiver origin, the API origins | `'self'` and the API origins | `'self'`, the collector origin, the API origins |
| Source maps | Alloy resolves stacks from `location` blocks | as `faro` | none: stacks stay minified |
| Loki shape | logfmt or JSON lines; labels `app_name`, `app_environment`, `kind` | as `faro` | OTLP logs: labels from the resource (`service_name`, `deployment_environment_name`), fields as structured metadata |
| Extra hop and cost | none | one proxy hop | none |

Pick `proxy` when the receiver must not be public, the key must not reach browsers, or the CSP
must stay `'self'`. Pick `otlp` only when the collector is not Alloy; it loses stack
de-minification and the receiver's rate limiting, and the package is experimental.

The proxy rules are in [workloads/spa.md](../workloads/spa.md#the-proxy-transport-proxy)
(nginx) and [workloads/fullstack.md](../workloads/fullstack.md#page-side-browser-rum) (a route
handler).

## Trace context

- `TracingInstrumentation` sends `traceparent` only to `rum.propagate_trace_origins`: exact
  origins, never a match-all pattern. A match-all would send trace headers, and CORS preflights,
  to every third party the page calls.
- Each API on that list allows `traceparent` and `tracestate` in its CORS policy
  ([traces.md](traces.md)), so the browser's CLIENT span and the API's SERVER span are one trace.
- `omitTraceContextForUnsampledSessions: true`. Without it, a session outside the sample sends
  `traceparent` with the unsampled flag, and the API's parent-based sampler drops its own spans
  too. With it, unsampled sessions send no trace context and the API samples on its own.
- The receiver's own URL is never traced: Faro ignores its transport URLs and `/collect`.
- `faro.tracing.fetch` events carry `traceID`, so a Loki record links to its Tempo trace.

## Safety

The scrubber (`beforeSend`) is the last step before anything leaves the page. It fails closed:
an item it can't scrub is dropped ([safe-capture.md](../safe-capture.md#browser-rum)).

| Data | Rule |
| --- | --- |
| URLs (page, resources, fetch spans, navigation, CSP, stack frames, URLs inside messages) | query values not in `rum.allowed_query_params` and fragments removed, user info removed |
| Emails in any text | masked: `j***@example.com` |
| Bearer and Basic credentials, JWTs, long token-like strings, card numbers (Luhn-checked) | `[redacted]` |
| Fields named like secrets (password, token, key, authorization, cookie, card) | `[redacted]` |
| `user.email`, `user.username`, `user.fullName`, `user.attributes`; `user.email`/`user.name` span and event attributes | dropped |
| Identifiers, timestamps, durations and sizes | kept as they are |
| Input values, element text, DOM content | never captured: Faro doesn't record them, and user-action names come from `data-faro-user-action-name` |

The scrubber is a backstop. Code still never puts personal data into `setUser`, event
attributes, measurement contexts or log messages.

## Performance

- Start-up is non-blocking and the tracing chunk is lazy (see above).
- Batching, compression and dedupe stay on; `reportAllChanges` stays off.
- `rum.session_sample_rate` (default `1.0`) samples whole sessions: an unsampled session sends
  nothing.
- The scrubber walks each item once, with a depth limit.
- The overhead budget and how to measure it are in
  [overhead-and-security.md](../overhead-and-security.md#browser-rum). Measured on a React
  app: +45 KB gzip initial JS, a 26 KB lazy tracing chunk, +12 ms FCP and LCP at 4x CPU
  slowdown, no added blocking time or INP.

## What RUM does not do

- No session replay, heatmaps or geolocation (Grafana Cloud only).
- No metrics from the page: web vitals are measurements (log lines). Dashboards and alerts
  derive metrics from them with LogQL, Loki recording rules or Alloy `loki.process`
  ([backends/grafana.md](../backends/grafana.md#browser-rum)).
- No profiles: `FaroProfiler` measures React component render time as measurements, not
  Pyroscope profiles.

[Documentation index](../README.md)
