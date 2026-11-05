# Single-page app (React, Vue, Angular, Svelte)

A static bundle (built with Vite, webpack or the framework's CLI) served by nginx or a CDN,
calling APIs that are instrumented on their own. The page follows
[browser RUM](../signals/rum.md) with Grafana Faro. Nothing in the app runs on a server, so the
deployment serves the runtime config, and nginx is the proxy when transport `proxy` is chosen.

`detect.py` reports such a project as workload `spa`.

## Module layout

```text
src/observability/rum/               # config.ts, init.ts, scrub.ts, react.tsx (assets/js/faro)
public/observability.json            # default with RUM off; the deployment mounts the real one
nginx default.conf                   # JSON access log, CSP, /observability.json, optional proxy
```

## Start-up

- The entry module starts the config fetch and `startRum` before rendering and doesn't wait for
  them ([rum.md](../signals/rum.md#configuration-at-run-time)).
- **React:** `instrumentRouter(createBrowserRouter(routes))`, `RumErrorBoundary` around the
  router, and `RouteErrorReporter` as the root route's `errorElement`
  ([assets/js/faro](../../assets/js/faro/README.md)).
- **Vue Router, Angular Router, SvelteKit:** the router's after-navigation hook calls
  `faro.api.setView({ name })` with the matched route's pattern (`/orders/:id`), never the
  path. The framework's global error handler calls `faro.api.pushError(error)`. Errors it doesn't
  see (`window.onerror`, unhandled rejections) are Faro's own.
- `user.id` comes from the app's verified auth state: after sign-in `faro.api.setUser({ id })`,
  after sign-out `faro.api.resetUser()`.

## Runtime configuration

- `observability.json` holds the `service` and `rum` blocks, rendered per environment by the
  deployment from `observability.yml` ([config.md](../config.md#rum)). The image ships a default
  with `"enabled": false`.
- nginx serves it from an exact location, `location = /observability.json { expires epoch;
  try_files $uri =404; }`. Under the SPA fallback a missing file would answer `index.html` with
  200, so the loader also checks the content type and keeps RUM off on anything that isn't JSON.

## Source maps

- Build source maps hidden: Vite `build: { sourcemap: 'hidden' }`, webpack
  `devtool: 'hidden-source-map'`. The bundle then has no `sourceMappingURL`.
- Move the `.map` files out of the served output in the build, and deliver them to the
  directory Alloy's `faro.receiver` reads, for example a volume or an object-store sync per
  release (`location` `path` may contain `{{ .Release }}`). nginx also answers 404 for `\.map$`.
- With hidden maps and `download = false` on the receiver, Alloy makes no outbound requests
  ([collector](../collector/README.md#browser-rum-faroreceiver)).

## nginx

Tested with nginx 1.30 against Alloy 1.21. The `map`s and `log_format` go inside `http {}` (a
`conf.d` file is included there), the rest inside `server {}`. Nothing in it names an
environment: one image serves every environment, and the per-environment parts come from mounts.

```nginx
# http {}
map $request_uri $path_only { "~^(?<p>[^?]*)" $p; }
map $http_x_forwarded_proto $page_scheme { default $http_x_forwarded_proto; "" $scheme; }
map $status $telemetry_failed { ~^[23] 0; default 1; }
log_format rum_json escape=json '{"time":"$time_iso8601","method":"$request_method","path":"$path_only",'
  '"status":$status,"bytes":$body_bytes_sent,"duration_s":$request_time,"traceparent":"$http_traceparent"}';

# server {}
access_log /dev/stdout rum_json;
add_header Content-Security-Policy "default-src 'self'; script-src 'self'; connect-src 'self' https://api.example.com" always;

location = /observability.json { expires epoch; try_files $uri =404; }
location ~ \.map$ { return 404; }
location / { try_files $uri /index.html; }
```

- **Access log:** JSON with the request path cut at `?`. Not `$uri`: after `try_files` falls
  back, `$uri` is `/index.html` for every route. Not `$request` or `$request_uri`: they put OAuth
  codes, state and tokens from callback URLs into the logs. `$http_traceparent` joins an asset or
  page request to a browser trace.
- **CSP `connect-src`:** `'self'`, the API origins and, for transport `faro` or `otlp`, the
  receiver's origin. Faro needs no `script-src` exception: it is part of the bundle.

### The proxy transport (`proxy`)

When `rum.transport` is `proxy`, nginx forwards the page's batches to an in-cluster receiver and
adds the key. The CSP stays `connect-src 'self'` plus the APIs.

```nginx
location = /telemetry/faro/collect {
  access_log /dev/stdout rum_json if=$telemetry_failed;
  client_max_body_size 1m;
  set $rum_upstream "";
  include /etc/nginx/rum/*.conf;        # mounted from a Secret; see below
  if ($rum_upstream = "") { return 503; }
  if ($request_method != POST) { return 405; }
  if ($http_origin != "$page_scheme://$http_host") { return 403; }
  proxy_http_version 1.1;
  proxy_set_header Connection "";
  proxy_set_header Cookie "";
  proxy_set_header Referer "";
  proxy_set_header Authorization "";
  proxy_pass $rum_upstream;
}
```

The mounted file, one per environment:

```nginx
resolver <cluster DNS address> valid=30s;
set $rum_upstream "http://alloy.observability.svc.cluster.local:12347/collect";
proxy_set_header X-API-Key "<receiver key>";
```

- **One image, every environment.** The origin check compares `Origin` with the page's own
  scheme and host (`X-Forwarded-Proto` behind an ingress that terminates TLS), so no origin is
  written down. The upstream, the resolver and the key come from the mount.
- **Starts without the mount.** A glob `include` that matches nothing is allowed, and the
  upstream is a variable resolved per request, so nginx starts with no Secret and with an
  unresolvable host; the proxy then answers 503 and the page keeps working.
- **Only the page's own posts:** `POST` (405 otherwise) from its own origin (403 otherwise),
  bodies up to the receiver's payload cap.
- **No session data upstream.** `proxy_set_header` doesn't inherit into a location that sets any
  of its own, so this location repeats the session headers it clears: no `Cookie`, `Referer` (it
  can carry OAuth codes) or `Authorization` reaches the receiver.
- **No `add_header` in this location:** one would drop the server-level CSP.
- **Only failures are logged** for the proxy route.

## Verification

Run the browser checks in [acceptance.md](../acceptance.md#browser-rum). The APIs the page calls
get the server checks.

[Documentation index](../README.md)
