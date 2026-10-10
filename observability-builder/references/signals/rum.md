# Browser RUM and session replay

What a web page emits so that a slow page, a failed click or a browser error can be followed
from the user's screen into the API, its logs and its profiles. The page uses the OpenTelemetry
Web SDK for traces, logs and metrics, and OneUptime's recorder for session replay. RUM and
session replay are the one area of the standard tied to a backend (OneUptime); the OTLP
destination and its key still come from the yml, and the browser sends everything through the
app's own origin, so the backend key never reaches the page.

## What the page emits

| Signal | Source | Content |
|---|---|---|
| Page loads | `@opentelemetry/instrumentation-document-load` | one trace per full load, with the resource fetch spans |
| Route changes | the app's router hook | one span per client-side navigation, named `route.change`, carrying `app.route` |
| API calls | `@opentelemetry/instrumentation-fetch` (and `-xml-http-request` where XHR is used) | CLIENT spans that send `traceparent` to the API, so the backend `SERVER` span continues the browser trace |
| User actions (optional) | the app | one span per business action, named `ui.<action>` after the action, never after the content |
| Errors | `error` and `unhandledrejection` handlers, framework error boundaries | one ERROR log record per error with `exception.type`, a sanitised `exception.message` and `exception.stacktrace`, inside the active span |
| Web vitals | the `web-vitals` package | histograms `web_vital.lcp`, `web_vital.inp`, `web_vital.fcp`, `web_vital.ttfb` (unit `ms`) and `web_vital.cls` (unit `1`), with `web_vital.rating` and `app.route`; INP counts only interactions of 40 ms or more. LCP, FCP and TTFB describe the page load but are often reported after a client-side navigation, so they carry the route the page loaded on; INP and CLS carry the route current at the report |
| Session replay | OneUptime's recorder (rrweb) | DOM snapshots and events in OneUptime's own chunk format, not OTLP |

Traces, logs and metrics are exported as OTLP/HTTP (JSON is fine), with delta temporality for
metrics, and flushed at once, never from a timer: an unloading page runs no more timers.

- **On `visibilitychange` to hidden:** flush traces, logs and metrics. On a navigation or a tab
  close the browser fires `pagehide` first and `visibilitychange` after it; `web-vitals` reports
  INP and CLS from its own capturing listener on `window` at that moment, so they leave in this
  flush.
- **On `pagehide`:** flush traces and logs only. The SDK's metric reader runs one export at a
  time and a flush waits for the export in flight, so a metrics export started on `pagehide` would
  hold back the one that carries INP and CLS until the page is gone.
- **Delta temporality:** a page flushes more than once as it ends; cumulative exports would send
  each value again, and a backend that sums the points (OneUptime does) would count it twice.

## Resource and attributes

The browser is part of the same service as the server that serves it: one `service.name`, and
the same `service.version` and `deployment.environment.name`. OneUptime classifies browser
telemetry as RUM by the `browser.*` resource attributes.

| Where | Attributes |
|---|---|
| Resource | `service.name`, `service.version`, `deployment.environment.name`, `browser.language`, `browser.platform`, `browser.mobile`, `browser.brands` (where `navigator.userAgentData` exists), `user_agent.original` |
| Every span and log | `session.id` (the replay session, see below), `app.route` (the route template) |
| Action spans and their logs | `correlation.id`, also sent to the API as `x-correlation-id` |
| Never | input values, element text, user identity beyond what the replay policy captures, `session.id` on the resource (it changes during a page's life) |

`app.route` is a template such as `/orders/[id]`, matched from the pathname against the app's
route patterns, never the raw path: ids in paths make every page a new route.

## Session id and trace links

- **One session id.** `session.id` on spans and logs is the replay recorder's session id (32
  lowercase hex characters, shared by the tabs of one browser, rotated after 30 minutes idle or
  4 hours). The recorder reports it through
  `(window.OneUptimeReplayQueue ||= []).push(['onSessionChange', id => …])`. Spans and logs of
  the first seconds often end before the recorder reports. Stamp `session.id` when they are
  exported (in the exporter wrapper), not when they start, so they carry the replay session. Use
  a per-tab fallback id (`crypto.randomUUID()` in `sessionStorage`) only for what is exported
  before the recorder reports.
- **Browser → API.** The fetch instrumentation adds `traceparent` to same-origin requests. A
  cross-origin API needs `propagateTraceHeaderCorsUrls` with an explicit list of origins, never a
  match-all pattern, and that API's CORS must allow `traceparent`, `tracestate` and `baggage`.
- **Replay → backend traces.** When the recorder loads after OpenTelemetry has patched `fetch`,
  and the replay config has `sameOriginTracePropagation` on (the default), it adds
  `tracestate: oneuptime=sid:<session id>` to same-origin requests. OneUptime then stamps the
  backend spans with the session. Load order matters: register OpenTelemetry first, the recorder
  second. Otherwise the recorder writes its own `traceparent`, and the API's span no longer
  continues the browser's fetch span. Calls made at page load can go out before the recorder
  starts and carry no session `tracestate`. Their fetch spans still carry `session.id`, so don't
  delay those calls just to get the header. The recorder skips every URL that starts with its
  `host`; behind the same-origin proxy that is the page origin, so an app that calls its API with
  absolute URLs sets `host` to `location.origin + "/."` ([sdk-notes.md](../sdk-notes.md)).

## Transport: the same-origin proxy

The page sends everything to its own origin. A proxy in the app server (route handlers in a
fullstack app, nginx for a single-page app) adds the backend key and forwards:

| Browser request | Proxy forwards to |
|---|---|
| `POST /telemetry/v1/traces`, `/v1/metrics`, `/v1/logs` | `{rum.exporter.endpoint}/v1/<signal>`, adding `rum.exporter.headers` |
| `GET /telemetry/session-replay/v1/recorder.js`, `GET …/latest/recorder.js`, `GET …/v1/config`, `POST …/v1/chunk` | `{rum.session_replay.host}/telemetry/session-replay/<version>/<artifact>`, adding the key and `x-oneuptime-app-identifier: {rum.app_identifier}` |

The proxy:

- accepts only these routes and methods (404 otherwise), only requests whose `Origin` is the app's
  own origin (403 otherwise) and bodies up to 2 MiB (413 otherwise);
- forwards `content-type` and `content-encoding` with the body, and returns the backend's status
  and body;
- forwards nothing from the user's session: no `Cookie`, `Referer` or `Authorization`. Same-origin
  requests send the full page URL as `Referer`, so an export from an OAuth callback page would
  carry the authorisation code to the backend;
- returns the recorder scripts byte for byte, because the recorder is loaded with an integrity
  hash, and passes back only the backend's `x-oneuptime-*` response headers, with
  `cache-control: no-store`;
- passes the browser's `Origin` on to the backend. A OneUptime **Browser** ingestion key refuses
  requests without it. A **Server** key with no origin list doesn't need it;
- creates no spans and no HTTP metrics for its own requests (that would trace the telemetry
  itself), and logs its failures.

The browser never holds the backend key: the recorder's config gets a placeholder token
(`local-proxy`) and the page's own origin as its host. With the proxy, a Content Security Policy
of `script-src 'self'` and `connect-src 'self'` covers RUM and replay.

## Runtime-served configuration

The page reads its RUM settings when it loads, so one build serves every environment: the
service name, version and environment, `rum.enabled`, `rum.sample_ratio`,
`rum.session_replay.enabled` and `rum.app_identifier`. A fullstack app renders them into the page
as a JSON script element; a single-page app fetches a JSON file that the deployment mounts.
Exporter headers are never part of it. Build-time public variables (for example Next.js
`NEXT_PUBLIC_*`) are baked into the bundle and are the wrong place for any of these values.

## Sampling

The browser starts its traces, so it samples at the root: a `traceidratio` sampler with
`rum.sample_ratio` (default `1.0`). The API's parent-based sampler then follows the browser's
decision. Session replay has its own sampling in the OneUptime replay policy.

## Safe capture in the browser

The browser rules from [safe-capture.md](../safe-capture.md), applied in code:

- **No input values or element text.** No instrumentation that records what users type or read;
  action spans name the action.
- **URLs.** On every browser span, `url.full` and `url.query` keep only allow-listed query
  parameters (values of others become `REDACTED`) and never the fragment. Apply it where the
  spans are created (the instrumentations' attribute hooks) or in an exporter wrapper that cleans
  spans before export. Document-load spans record `location.href`, so they need it too.
- **Errors.** Exception messages are sanitised before they're recorded: values that look like
  emails, tokens (JWTs, `Bearer` values and secret pairs such as `token=…`, `password: …`,
  `client_secret=…`), long digit runs or URLs with query values become `REDACTED`. The same applies to
  the first line of the stack trace, which repeats the message. The replay recorder copies
  uncaught error messages into the replay too, beyond the reach of the OTel export. So an
  error message that embeds personal data must be fixed where the error is thrown.
- **Replay masking.** OneUptime's recorder masks password, card (`autocomplete=cc-*`), one-time-code,
  hidden and file inputs in every mode, and never records clipboard events, request or response
  bodies or headers. Other personal data needs marks in code:
  - `oneuptime-block` on inputs and areas whose content must not be recorded at all (email,
    phone, address, date of birth, national ID, free-text notes, chat). In the default mode,
    `oneuptime-mask` hides an element's text but not what a user types into an input, so typed
    personal data needs `oneuptime-block`, a replay-policy mask selector, or the
    `MaskInputsOnly` mode.
  - `oneuptime-mask` on text that shows personal data.
  - `oneuptime-block` also on elements whose **attributes** hold personal data: the recorder keeps
    attributes under `oneuptime-mask`, so an `aria-label`, `title`, `alt` or `data-testid` built from
    a name or an email, or an avatar's photo URL in `src`, leaks unless the element is blocked.
    Block the component (an avatar, a list whose rows carry names in their labels) rather than
    each instance.
  - `oneuptime-ignore` where input events themselves must not be recorded.
  - On AI chat pages, `oneuptime-block` on the transcript and the message box: the replay
    would otherwise hold what the user and the model wrote, outside the content policy
    ([ai-agent.md](../workloads/ai-agent.md#chat-interfaces-and-streaming)).
- **AI feedback** (thumbs, ratings, comments) goes to the app's own API, which scores it in
  Langfuse from the server; the page never holds a Langfuse key, and browser telemetry never
  records prompts or answers ([ai-agent.md](../workloads/ai-agent.md#user-feedback)).
- **Consent.** The recorder honours Do Not Track and Global Privacy Control by default
  (`respectDoNotTrack: true`); a consent tool calls `grantConsent` and `revokeConsent`. Replay
  stores nothing offline (`offlineStorage: false`).
- **Replay starts only with masking in place.** Mark the fields first, then turn replay on.
- **Replay waits on authentication pages.** The recorder records the page URL with its query, and
  the login and OAuth callback pages carry an authorisation code, state or PKCE values there. On
  those routes the loader isn't added; it starts on the first route change away from them. The
  OpenTelemetry side still cleans their URLs like any other.

## Configuration

The `rum` section of `observability.yml` ([config.md](../config.md)):

| Key | Default | Meaning |
|---|---|---|
| `rum.enabled` | `false` | RUM on or off |
| `rum.exporter.endpoint` | none | the backend's OTLP base URL the proxy forwards to, e.g. `https://oneuptime.example.com/otlp`; the browser always posts to `/telemetry/v1/<signal>` on its own origin |
| `rum.exporter.headers` | `{}` | added by the proxy, e.g. `x-oneuptime-token: "${file:/var/run/secrets/observability/oneuptime-token}"` |
| `rum.app_identifier` | none | the RUM application identifier in OneUptime: the browser resource's `service.name`, which OneUptime keys its RUM applications by; a different value puts the replays in a second application, apart from the spans and web vitals ([OneUptime](../backends/oneuptime.md#rum)) |
| `rum.sample_ratio` | `1.0` | the browser's root sampling ratio |
| `rum.session_replay.enabled` | `false` | record sessions |
| `rum.session_replay.host` | none | the OneUptime origin the replay proxy forwards to, e.g. `https://oneuptime.example.com` (an origin, not a path: the recorder appends `/telemetry/session-replay/…` itself) |

## OneUptime reference

Seen with OneUptime 14.0.x. Confirm against the version you send to.

| Item | Detail |
|---|---|
| OTLP ingest | `POST {host}/otlp/v1/traces`, `/v1/metrics`, `/v1/logs`, header `x-oneuptime-token` |
| Ingestion keys | Project Settings → Telemetry & APM → Ingestion Keys. **Browser** keys carry an allowed-origins list; **Server** keys don't. Check a key with `GET /telemetry/session-replay/v1/validate` |
| Recorder | not published on npm; OneUptime serves the loader (`v1/recorder.js`) and the recorder (`latest/recorder.js`, loaded with the integrity hash from the config) |
| Replay config | `GET /telemetry/session-replay/v1/config` returns the replay policy: `enabled`, `maskingMode` (`MaskSensitiveInputsOnly` by default, `MaskInputsOnly`, `MaskAllText`), mask and block selectors, consent mode, capture trigger, sample percentage, `sameOriginTracePropagation`, `recorderIntegrity` |
| Replay upload | `POST /telemetry/session-replay/v1/chunk`, `application/octet-stream`, frames of `<envelope JSON> \n<payload>`, the payload a gzip or plain JSON array of rrweb events; answered `202 {"directive": "continue"…}`. 401, 403 and 404 stop the recorder |
| Replay policy | set in OneUptime per application: masking mode, mask and block selectors, consent, trigger, sampling, allowed origins, retention. The skill reports the settings the masking plan needs; it doesn't change them |

## Verification

Drive the pages in a headless browser against the local receiver (`scripts/receive.py`, started
with `--replay-assets <dir>` so it serves the recorder files and accepts replay chunks), as the
instrument workflow's verify step describes. Check: same-origin transport, the browser resource,
page-load and route-change spans with route templates, the browser → API trace link, `session.id`
matching the replay session, one sanitised exception record per error, masked replay chunks, and
no typed value, query value or fragment in any browser payload
([acceptance.md](../acceptance.md#browser-rum-and-session-replay)).

[Documentation index](../README.md)
