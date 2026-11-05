# Fullstack app (Next.js)

One deployable that serves pages and an API from the same origin: Next.js with the App Router,
or a similar framework. The server side follows the [API backend](api.md) rules, and the pages
follow [browser RUM](../signals/rum.md) with Grafana Faro. This page covers what differs when
the app is a Next.js app (16.x).

## Module layout

From [layout.md](../layout.md), under `src/` when the project uses a `src/` folder:

```text
instrumentation.ts                   # register(): Node-runtime server setup; onRequestError
instrumentation-client.ts            # page side: readRumConfig() and startRum() (browser RUM)
src/observability/
├── server/                          # config, tracing, access log, logger, metrics, profiling (server-only)
├── rum/                             # config.ts, init.ts, scrub.ts (assets/js/faro), client-only
└── shared/                          # route patterns, URL and message sanitising
```

Server modules start with `import 'server-only'`, so a client import of the config loader or the
backend key fails the build instead of shipping them to the browser.

## Server side

| Concern | Next.js hook |
| --- | --- |
| SDK start | `instrumentation.ts` `register()` runs once per server process before requests. Guard it with `process.env.NEXT_RUNTIME === 'nodejs'` and import the server module dynamically, so the Edge runtime never loads it |
| Start-up work | code in `register()` (a cache warm-up, for example) runs outside any request: it must not start a trace ([traces](../signals/traces.md)) |
| SERVER spans | Next.js creates its own spans once a tracer provider is registered. Keep one `SERVER` span per request, named `{method} {http.route}` with stable HTTP attributes and the route template (`/api/orders/[id]`). Either use Next's root span and map its attributes, or use the http instrumentation and drop Next's root. Never both. Check the span names and attributes of the installed version first ([sdk-notes.md](../sdk-notes.md)) |
| Access log | Route handlers and pages share no response hook, so the access log is written from the server root span as it ends: a span processor that also sees unsampled requests (a record-only wrapper). It covers route handlers, pages and unmatched routes. Static assets (`/_next/static/…`) are left out when they succeed |
| Errors | `onRequestError(error, request, context)` in `instrumentation.ts` records server errors from pages, route handlers and server actions. Route handlers that turn errors into responses record them in their shared error wrapper. Either way, one ERROR record per failure |
| Middleware | `proxy.ts` (formerly `middleware.ts`) runs before routing and can't see the response status: it's no place for the access log or metrics |
| Metrics port, profiling | as for any Node.js API (`next start` is one process) |

An app with AI features keeps them on the server side: route handlers declare the feature and
call the model, and AI feedback posts to a route handler that scores in Langfuse with the
server's keys ([ai-agent.md](ai-agent.md)).

## Page side (browser RUM)

| Concern | Next.js |
| --- | --- |
| Start-up | `instrumentation-client.ts` runs in the browser before hydration: `const cfg = readRumConfig(); if (cfg) void startRum(cfg, { userId, view })`. It must not import anything from `server/` |
| Runtime config | the root layout (a server component) renders the `service` and `rum` blocks of the server's yml as `<script type="application/json" id="rum-config">`, never `rum.api_key_file`. Serialise with `JSON.stringify` and escape `<` as `\u003c`, so a value can't close the script tag |
| Views | a client component in the root layout calls `faro.api.setView({ name })` on `usePathname()` changes, with the route's pattern from the app's route list (`/orders/[id]`), never the path |
| Errors | `app/error.tsx` and `app/global-error.tsx` call `faro.api.pushError(error)` once in an effect. Server errors are the server's (`onRequestError`); the page reports only what happens in the browser |
| Trace context | `propagate_trace_origins` is the app's own origin plus any other API origins. Same-origin `fetch` to route handlers joins the server's trace |
| Proxy transport | a route handler `app/telemetry/faro/collect/route.ts` (`POST` only) checks `Origin` against the app's origin, caps the body at the receiver's payload limit, forwards the body with `content-type` and `content-encoding` to the in-cluster receiver with `X-API-Key` from the mounted key file, and passes no `Cookie`, `Referer` or `Authorization`. It runs on the Node runtime, is excluded from the server's tracing, access log and HTTP metrics, and logs only its failures |
| Source maps | `productionBrowserSourceMaps` stays off for the served output; maps for the receiver come from a separate build step that keeps them out of `.next/static` ([spa.md](spa.md#source-maps)) |

## Configuration and deployment

- One `observability.yml` holds the server settings and the page's `rum` block. The server
  module reads it at start-up and renders the page's part into the HTML.
- Nothing telemetry-related goes into `NEXT_PUBLIC_*` variables: they are baked in at build time,
  so one image couldn't serve two environments, and anything in them is public.
- The chart mounts the yml and the backend key file as for any service ([charts.md](../charts.md)).
  On another platform (a container service, a VM), the platform's detectors add the resource
  rows ([config.md](../config.md#resource-per-platform)).
- Request identity comes from the server's auth (the session or token the server verifies),
  never from a header or cookie the page sets for telemetry; values that may hold personal data
  pass through the shared masking helper ([data-model.md](../data-model.md#request-and-identity-context)).

## Verification

Run the server checks and the browser checks in [acceptance.md](../acceptance.md#browser-rum).

[Documentation index](../README.md)
