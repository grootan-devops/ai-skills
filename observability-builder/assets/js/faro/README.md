# Grafana Faro pieces for browser RUM

The page side of [browser RUM](../../../references/signals/rum.md): runtime config, start-up with
every self-hosted Faro feature, the three transports, and the `beforeSend` scrubber. Written
against Faro 2.13 and React Router 8, and also run on Faro 2.12 (which has no frustration
events); check the installed release's types before copying, and type-check after.

| File | What it gives the page |
| --- | --- |
| `config.ts` | `RumConfig` (the yml's `service` and `rum` blocks as JSON); `fetchRumConfig()` for a single-page app (JSON only, RUM off on any failure); `readRumConfig()` for a fullstack page (a server-rendered `<script type="application/json" id="rum-config">`). Drops match-all trace origins, clamps the sample rate, turns RUM off without an endpoint. |
| `init.ts` | `setRumUser(id?)` (sign-in with the opaque id, sign-out with none; kept until start-up). `startRum(config, { instrumentations, userId, view })`: `initializeFaro` with every feature on (navigation through `experimental.trackNavigation`, resource timing, web-vitals attribution, CSP, console with `consoleErrorAsLog`), session sampling, batching and compression, the scrubber, geolocation off; the transport from `rum.transport` (`faro`, `proxy`, `otlp`, the OTLP package loaded only for `otlp`); then `TracingInstrumentation` from a lazy chunk with exact `propagateTraceHeaderCorsUrls` origins and `omitTraceContextForUnsampledSessions`. |
| `scrub.ts` | `makeBeforeSend({ allowedQueryParams })`: walks the meta and the payload, OTLP span attributes included. Strips query values and fragments from every URL, masks emails, redacts credentials, JWTs, token-like strings, Luhn-valid card numbers and secret-named fields, drops personal user fields; keeps ids, timestamps, durations and sizes. Drops an item it can't scrub. Also `maskEmail`, `sanitizeUrl`, `scrubText`. |
| `react.tsx` | `reactIntegration()` (React Router 7/8 data router), `instrumentRouter(router)` (route-change events plus `setView` with the route template), `viewName()`, `RumErrorBoundary` (`FaroErrorBoundary`), `RouteErrorReporter` (the root route's `errorElement`, which reports each route error once). |

Copy the files into `src/observability/rum/`. Then:

- **Entry** (React, single-page app):

  ```tsx
  void fetchRumConfig().then((cfg) =>
    cfg ? startRum(cfg, { instrumentations: [reactIntegration()], userId: session?.userId, view: viewName() }) : null);
  const router = instrumentRouter(createBrowserRouter(routes));
  root.render(<RumErrorBoundary fallback={<Fallback />}><RouterProvider router={router} /></RumErrorBoundary>);
  ```

  The root route gets `errorElement: <RouteErrorReporter fallback={<Fallback />} />`. Rendering
  never waits for RUM. The auth code calls `setRumUser(session.userId)` after sign-in and
  `setRumUser()` on sign-out (the audit's `faro-user-not-reset` checks for the reset).
- **Release and transports:** `app.release` is the service version, which selects the source-map
  directory on the receiver. Delete `otlpTransports` and its call when the project doesn't use
  transport `otlp`: it needs `@grafana/faro-transport-otlp-http` to type-check.
- **Vue, Angular, Svelte:** `startRum(cfg, { userId, view })` without React. In the router's
  after-each hook call `faro.api.setView({ name: <route template> })`, and in the framework's
  error handler (`app.config.errorHandler`, Angular `ErrorHandler`, SvelteKit `handleError`)
  call `faro.api.pushError(error)`.
- **Fullstack (Next.js):** `instrumentation-client.ts` calls `readRumConfig()` and `startRum`.
- **User actions:** add `data-faro-user-action-name="<bounded-name>"` to the controls that start
  business actions.
- **Source maps:** build them hidden (Vite `build.sourcemap: 'hidden'`, webpack
  `hidden-source-map`) and move the `.map` files out of the served output, into the directory
  Alloy's `faro.receiver` reads ([collector](../../../references/collector/README.md#browser-rum-faroreceiver)).

[Documentation index](../../../references/README.md)
