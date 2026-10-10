# Single-page app (React, Vue, Angular)

A static bundle (built with Vite or similar) served by nginx, calling APIs that are
instrumented on their own. The browser code is the same as in a [fullstack app](fullstack.md)
and follows [browser RUM and session replay](../signals/rum.md). What differs is that nothing
in the app runs on a server: nginx serves the configuration and is the proxy.

## Module layout

```text
src/observability/                   # rum, web vitals, route spans, errors, replay loader, config
public/observability.json            # runtime RUM config, mounted per environment by the chart
nginx default.conf                   # JSON access log with $http_traceparent; /telemetry/ proxy locations
```

## What differs from a fullstack app

| Concern | Single-page app |
|---|---|
| Start-up | the bundle's entry imports `src/observability/rum` first, before the router and the app, and runs it synchronously |
| Runtime configuration | `observability.json`, fetched once at start-up, mounted by the chart per environment, never baked into the bundle. Until it arrives, the page buffers nothing and exports nothing, so the RUM code can be a lazily loaded chunk the bundle imports only when the file switches RUM on. Ship a default with RUM off. nginx serves it from an exact location with `try_files $uri =404`: under the SPA fallback a missing file answers `index.html` with 200, so the page also checks the content type and keeps RUM off on anything that isn't JSON |
| Route changes | the router's navigation events (React Router, Vue Router, Angular Router) start the `route.change` span. `app.route` is the router's own pattern for the matched route |
| OTLP proxy | nginx `location /telemetry/v1/` forwards to the collector's OTLP/HTTP receiver, or to the backend with the key added by `proxy_set_header` from a mounted Secret (an nginx include file holding the `proxy_set_header` line, or an `envsubst` template) |
| Replay proxy | nginx `location /telemetry/session-replay/` forwards to the OneUptime host with the key and `x-oneuptime-app-identifier` added the same way. Allow only the recorder, config and chunk paths, return the recorder bytes unchanged and set `client_max_body_size 2m` |
| Access log | nginx writes a JSON access log with `$http_traceparent`, so each page and asset request can be joined to the browser trace that made it. It logs the path without its query (`$uri`): `$request` and `$request_uri` put OAuth codes, state and tokens from callback URLs into the pod logs. The proxy routes log only their failures |
| Server-side errors | none: browser errors are the only error records the app produces; the APIs report their own |

Without a proxy, the page would need a OneUptime **Browser** ingestion key restricted to the
app's origins and a CSP that allows the OneUptime origin. Prefer the proxy: the key stays on
the server and the CSP stays `'self'`.

## The nginx proxy

Tested with nginx 1.30 against the skill's receiver and against Alloy with OneUptime; in those
runs the replay location used a plain `upstream` over http, and the `resolver` + variable HTTPS
form below is its production shape. The `map`s and `upstream` go inside `http {}`, the locations
inside `server {}`. Replace the origin, the collector address and the OneUptime host; the key
file comes from a Secret.

```nginx
# http {}
map "$request_method:$http_origin" $telemetry_origin_ok {
  default 0;
  "~^GET:$" 1;                          # script loads send no Origin
  "GET:https://app.example.com" 1;
  "POST:https://app.example.com" 1;
}
map $status $telemetry_failed { ~^[23] 0; default 1; }
upstream otel_collector { server otel-collector.observability:4318; }

# server {}
location = /observability.json { expires epoch; try_files $uri =404; }

location ^~ /telemetry/ {
  client_max_body_size 2m;
  access_log /var/log/nginx/access.log main if=$telemetry_failed;

  location ~ ^/telemetry/v1/(?<otlp_signal>traces|metrics|logs)$ {
    if ($telemetry_origin_ok = 0) { return 403; }
    if ($request_method != POST) { return 404; }
    proxy_http_version 1.1;
    proxy_set_header Connection "";
    proxy_set_header Cookie "";
    proxy_set_header Referer "";
    proxy_set_header Authorization "";
    proxy_pass http://otel_collector/v1/$otlp_signal;
  }

  location ~ ^/telemetry/session-replay/(?<replay_artifact>v1/recorder\.js|latest/recorder\.js|v1/config|v1/chunk)$ {
    if ($telemetry_origin_ok = 0) { return 403; }
    include /etc/nginx/secrets/oneuptime-replay.conf;   # proxy_set_header x-oneuptime-token "...";
    proxy_set_header x-oneuptime-app-identifier my-app;
    proxy_http_version 1.1;
    proxy_set_header Connection "";
    proxy_set_header Cookie "";
    proxy_set_header Referer "";
    proxy_set_header Authorization "";
    proxy_hide_header Set-Cookie;
    proxy_hide_header Access-Control-Allow-Origin;
    proxy_hide_header Access-Control-Allow-Credentials;
    proxy_hide_header Cache-Control;
    expires epoch;
    resolver <cluster DNS IP> valid=30s;
    set $oneuptime_host oneuptime.example.com;
    proxy_ssl_server_name on;
    proxy_set_header Host $oneuptime_host;
    proxy_pass https://$oneuptime_host/telemetry/session-replay/$replay_artifact;
  }

  return 404;
}
```

What each part guards against, all seen in practice:

- **Named captures.** A `map` with regex entries overwrites the numbered captures when it's
  evaluated, so `$1` in `proxy_pass` turns empty after the `Origin` check and the request goes to
  the wrong path.
- **`proxy_set_header` doesn't inherit** into a location that sets any of its own, so each
  location repeats its list, including the session headers it clears.
- **No `add_header` in these locations.** A location with its own `add_header` drops every header
  set at `http` or `server` level, the Content-Security-Policy included. `expires epoch` gives
  `Cache-Control: no-cache` without one.
- **`^~` on the prefix.** Otherwise a static-asset regex location (`\.(js|css|…)$`) catches
  `recorder.js` first.
- **A variable upstream with a `resolver`** for an external host: an `upstream` block or a literal
  host is resolved at start-up, and an unresolvable host would stop nginx from serving the app.
- **Empty `Origin` only for GET:** the recorder's script loads send none.

## Verification

The browser checks apply as for a fullstack app; the server checks apply to the APIs the page
calls ([acceptance.md](../acceptance.md)).

[Documentation index](../README.md)
