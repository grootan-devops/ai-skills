# Fullstack app (Next.js)

One deployable that serves pages and an API from the same origin: Next.js with the App Router,
or a similar framework. The server side follows the [API backend](api.md) rules; the standard
instruments the server only, and the pages emit no telemetry. This page covers what differs
when the server is a Next.js app (16.x).

## Module layout

From [layout.md](../layout.md), under `src/` when the project uses a `src/` folder:

```text
instrumentation.ts                   # register(): Node-runtime server setup; onRequestError
src/observability/
├── server/                          # config, tracing, access log, logger, metrics, profiling (server-only)
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

## Configuration and deployment

- One `observability.yml` holds the server settings. The server module reads it at start-up.
- Nothing telemetry-related goes into `NEXT_PUBLIC_*` variables: they are baked in at build time,
  so one image couldn't serve two environments, and anything in them is public.
- The chart mounts the yml and the backend key file as for any service ([charts.md](../charts.md)).

## Verification

Run the server checks in [acceptance.md](../acceptance.md).

[Documentation index](../README.md)
