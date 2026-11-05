// Starts Faro from the runtime config: every self-hosted feature on, the transport chosen by
// rum.transport, the scrubber last before send. Tracing (OpenTelemetry, the heaviest part) is a
// lazily loaded chunk added after start-up, so it never delays the first render.
import {
  type Faro,
  faro as globalFaro,
  getWebInstrumentations,
  initializeFaro,
  type Instrumentation,
  LogLevel,
  type Transport,
} from '@grafana/faro-web-sdk';
import type { RumConfig } from './config';
import { makeBeforeSend } from './scrub';

const LEVELS: Record<string, LogLevel> = {
  trace: LogLevel.TRACE,
  debug: LogLevel.DEBUG,
  log: LogLevel.LOG,
  info: LogLevel.INFO,
  warn: LogLevel.WARN,
  error: LogLevel.ERROR,
};

function escapeRegExp(text: string): string {
  return text.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
}

// Exact origins only: `https://api.example.com` matches that origin and its paths, nothing else.
export function originPatterns(origins: string[]): RegExp[] {
  return origins.map((o) => new RegExp(`^${escapeRegExp(o.replace(/\/+$/, ''))}(/|$)`));
}

// Transport otlp only: needs @grafana/faro-transport-otlp-http installed. A project that doesn't use
// otlp deletes this function and its call, or the type check fails on the missing package.
async function otlpTransports(cfg: RumConfig['rum']): Promise<Transport[]> {
  const { OtlpHttpTransport } = await import('@grafana/faro-transport-otlp-http');
  const base = cfg.endpoint.replace(/\/+$/, '');
  return [
    new OtlpHttpTransport({
      logsURL: `${base}/v1/logs`,
      tracesURL: `${base}/v1/traces`,
      ...(cfg.api_key ? { apiKey: cfg.api_key } : {}),
      requestOptions: { credentials: 'omit' },
    }),
  ];
}

let pendingUserId: string | undefined;

// Call on sign-in with the opaque id from the verified session, and on sign-out with no id. Before
// Faro has started, the id is kept and applied at start-up, so an early sign-in isn't lost.
export function setRumUser(id?: string): void {
  pendingUserId = id;
  if (!globalFaro.api) return;
  if (id) globalFaro.api.setUser({ id });
  else globalFaro.api.resetUser();
}

export interface StartOptions {
  // Framework integrations, e.g. new ReactIntegration({ router: … }).
  instrumentations?: Instrumentation[];
  // The opaque id from the app's verified session; never an email, a username or a name.
  userId?: string;
  // The first view's name (a route template); later views come from the router hook.
  view?: string;
}

export async function startRum(config: RumConfig, options: StartOptions = {}): Promise<Faro | null> {
  const { service, rum } = config;
  if (!rum.enabled) return null;
  // proxy: same origin, the proxy adds the key; faro: the receiver's public spam-filter key.
  const fetchTransport = rum.transport === 'otlp' ? undefined : { url: rum.endpoint, apiKey: rum.transport === 'faro' ? rum.api_key || undefined : undefined };
  const transports = rum.transport === 'otlp' ? await otlpTransports(rum) : undefined;

  const faro = initializeFaro({
    ...(fetchTransport ?? {}),
    ...(transports ? { transports } : {}),
    requestCompression: true,
    ...(options.view ? { view: { name: options.view } } : {}),
    // release selects the source-map path in faro.receiver's location blocks ({{ .Release }}).
    app: { name: service.name, namespace: service.namespace, version: service.version, release: service.version, environment: service.environment },
    instrumentations: [
      ...getWebInstrumentations({ captureConsole: true, enablePerformanceInstrumentation: true, enableContentSecurityPolicyInstrumentation: true }),
      ...(options.instrumentations ?? []),
    ],
    experimental: { trackNavigation: rum.track_navigation },
    trackResources: rum.track_resources,
    trackGeolocation: false,
    webVitalsInstrumentation: { reportAllChanges: false, trackAttributionSources: true },
    consoleInstrumentation: {
      disabledLevels: rum.console_disabled_levels.map((l) => LEVELS[l]).filter((l) => l !== undefined),
      // console.error becomes an error-level log; exceptions come from real errors only, once each.
      consoleErrorAsLog: true,
      serializeErrors: false,
    },
    sessionTracking: { enabled: true, persistent: false, samplingRate: rum.session_sample_rate },
    batching: { enabled: true, sendTimeout: 250, itemLimit: 50 },
    dedupe: true,
    // Faro adds its own endpoint (url, or the OTLP transport's URLs) and /collect to this list.
    ignoreUrls: rum.ignore_urls,
    beforeSend: makeBeforeSend({ allowedQueryParams: rum.allowed_query_params }),
    preventGlobalExposure: false,
  });

  const userId = options.userId ?? pendingUserId;
  if (userId) faro.api.setUser({ id: userId });

  // Lazy chunk: OpenTelemetry fetch/XHR spans that carry traceparent to the listed API origins only.
  void import('@grafana/faro-web-tracing')
    .then(({ TracingInstrumentation }) => {
      faro.instrumentations.add(
        new TracingInstrumentation({
          omitTraceContextForUnsampledSessions: true,
          instrumentationOptions: { propagateTraceHeaderCorsUrls: originPatterns(rum.propagate_trace_origins) },
        }),
      );
    })
    .catch(() => faro.api.pushLog(['rum tracing chunk failed to load'], { level: LogLevel.WARN }));

  return faro;
}
