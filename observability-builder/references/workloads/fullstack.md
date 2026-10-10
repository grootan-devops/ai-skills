# Fullstack app (Next.js)

One deployable that serves pages and an API from the same origin: Next.js with the App Router,
or a similar framework. The server side follows the [API backend](api.md) rules; the browser
side follows [browser RUM and session replay](../signals/rum.md). This page covers what differs
when both live in one Next.js app (16.x).

## Module layout

From [layout.md](../layout.md), under `src/` when the project uses a `src/` folder:

```text
instrumentation.ts                   # register(): Node-runtime server setup; onRequestError
instrumentation-client.ts            # browser setup before hydration; onRouterTransitionStart
src/observability/
├── server/                          # config, tracing, access log, logger, metrics, profiling (server-only)
├── client/                          # rum, web vitals, route spans, errors, replay loader
└── shared/                          # route patterns, URL and message sanitising
app/telemetry/v1/[signal]/route.ts                      # same-origin OTLP proxy
app/telemetry/session-replay/[version]/[artifact]/route.ts   # session-replay proxy
app/error.tsx, app/global-error.tsx  # report through client/errors
```

Server modules start with `import 'server-only'`, so a client import of the config loader or the
backend key fails the build instead of shipping them to the browser.

## Server side

| Concern | Next.js hook |
| --- | --- |
| SDK start | `instrumentation.ts` `register()` runs once per server process before requests. Guard it with `process.env.NEXT_RUNTIME === 'nodejs'` and import the server module dynamically, so the Edge runtime never loads it |
| Start-up work | code in `register()` (a cache warm-up, for example) runs outside any request: it must not start a trace ([traces](../signals/traces.md)) |
| SERVER spans | Next.js creates its own spans once a tracer provider is registered. Keep one `SERVER` span per request, named `{method} {http.route}` with stable HTTP attributes and the route template (`/api/orders/[id]`). Either use Next's root span and map its attributes, or use the http instrumentation and drop Next's root. Never both. Check the span names and attributes of the installed version first ([sdk-notes.md](../sdk-notes.md)) |
| Access log | Route handlers and pages share no response hook, so the access log is written from the server root span as it ends: a span processor that also sees unsampled requests (a record-only wrapper). It covers route handlers, pages and unmatched routes. Static assets (`/_next/static/…`) and the telemetry proxy routes (`/telemetry/…`) are left out when they succeed |
| Errors | `onRequestError(error, request, context)` in `instrumentation.ts` records server errors from pages, route handlers and server actions. Route handlers that turn errors into responses record them in their shared error wrapper. Either way, one ERROR record per failure |
| Middleware | `proxy.ts` (formerly `middleware.ts`) runs before routing and can't see the response status: it's no place for the access log or metrics |
| Metrics port, profiling | as for any Node.js API (`next start` is one process) |

## Browser side

`instrumentation-client.ts` runs after the HTML loads and before React hydrates. Only its
synchronous top-level code is sure to finish first, so it must do the following without
awaiting anything:

1. Read the runtime configuration that the root layout rendered into the page: a
   `<script id="observability-config" type="application/json">` written by `app/layout.tsx`, a
   server component, from the server module. It holds only service name, version, environment,
   `rum.*` switches, the sample ratio and the app identifier, never headers.
2. Start the web tracer provider with the document-load and fetch instrumentations, the
   `session.id` span processor and the URL-cleaning exporter wrapper. Then the meter provider
   with web vitals, the logger provider and the `error` / `unhandledrejection` handlers. The
   fetch instrumentation ignores `/telemetry/` so the exports don't trace themselves.
3. Load the replay recorder after OpenTelemetry has patched `fetch`, when replay is on:
   - set `window.__ONEUPTIME_SESSION_REPLAY__ = {host: location.origin, token: 'local-proxy', appIdentifier, respectDoNotTrack: true, offlineStorage: false}`;
   - append a script element for `/telemetry/session-replay/v1/recorder.js`;
   - register `onSessionChange` to switch `session.id`.

Route changes come from `export function onRouterTransitionStart(url, navigationType)` in the
same file. It starts a `route.change` span with `app.route` set from the app's route patterns
(`/orders/[id]`). A client component in the root layout ends the span when `usePathname()`
reports the new path. Keep the pattern list in `shared/`, next to the app folder it mirrors;
Next's `event.fromRoutes` is still behind an experimental flag.

`app/error.tsx` and `app/global-error.tsx` report the error they receive through the same
client errors module, so render errors are recorded once, with `session.id` and the active
span.

An app with AI features keeps them on the server side: route handlers declare the feature and
call the model, AI feedback posts to a route handler that scores in Langfuse with the server's
keys, and chat transcripts carry `oneuptime-block` so replay never records them
([ai-agent.md](ai-agent.md)).

## The proxy routes

`app/telemetry/v1/[signal]/route.ts` (POST) and
`app/telemetry/session-replay/[version]/[artifact]/route.ts` (GET, POST) implement the proxy
rules on [rum.md](../signals/rum.md#transport-the-same-origin-proxy):

- an allow-list of signals and replay artifacts;
- the same-origin check and the 2 MiB cap;
- the key and app identifier from the server module, passing on `Origin`;
- recorder bytes unchanged, `cache-control: no-store`.

Both routes set `export const runtime = 'nodejs'` and `export const dynamic = 'force-dynamic'`,
and their own requests make no spans or metrics.

## Configuration and deployment

- One `observability.yml` holds the server and the `rum` settings. The server module reads it at
  start-up. The browser gets only the runtime JSON described above.
- Nothing telemetry-related goes into `NEXT_PUBLIC_*` variables: they are baked in at build time,
  so one image couldn't serve two environments, and anything in them is public.
- A Content Security Policy needs `script-src 'self'` and `connect-src 'self'` for RUM and
  replay through the proxy.
- The chart mounts the yml and the OneUptime key file as for any service ([charts.md](../charts.md)).

## Verification

Check the server and the browser together: the server checks and the browser RUM checks in
[acceptance.md](../acceptance.md).

[Documentation index](../README.md)
