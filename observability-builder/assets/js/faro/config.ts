// Runtime RUM settings: read once at start-up, so one build serves every environment.
// Never from build-time public variables (VITE_*, NEXT_PUBLIC_*): those are baked into the bundle.

export type RumTransport = 'faro' | 'proxy' | 'otlp';

export interface RumConfig {
  service: { name: string; namespace?: string; version: string; environment: string };
  rum: {
    enabled: boolean;
    transport: RumTransport;
    // faro: the receiver's /collect URL; proxy: the page's own path; otlp: the collector base URL.
    endpoint: string;
    // Public by design (it reaches every browser): a spam filter, never a secret. Empty in proxy mode.
    api_key?: string;
    session_sample_rate: number;
    // Exact API origins that get traceparent. Never a match-all pattern.
    propagate_trace_origins: string[];
    console_disabled_levels: Array<'trace' | 'debug' | 'log' | 'info' | 'warn' | 'error'>;
    ignore_urls: string[];
    // Query parameters whose values may stay in URLs; every other value is replaced.
    allowed_query_params: string[];
    track_resources: boolean;
    track_navigation: boolean;
  };
}

const DEFAULTS: RumConfig['rum'] = {
  enabled: false,
  transport: 'faro',
  endpoint: '',
  session_sample_rate: 1,
  propagate_trace_origins: [],
  console_disabled_levels: ['trace', 'debug', 'log'],
  ignore_urls: [],
  allowed_query_params: [],
  track_resources: true,
  track_navigation: true,
};

function valid(cfg: Partial<RumConfig> | undefined): cfg is RumConfig {
  const s = cfg?.service;
  return !!s && typeof s.name === 'string' && typeof s.version === 'string' && typeof s.environment === 'string';
}

function withDefaults(cfg: RumConfig): RumConfig {
  const rum = { ...DEFAULTS, ...cfg.rum };
  // A match-all origin would send trace context (and the API's CORS preflights) to every host.
  rum.propagate_trace_origins = rum.propagate_trace_origins.filter((o) => /^https?:\/\/[^*]+$/.test(o));
  rum.session_sample_rate = Math.min(1, Math.max(0, Number(rum.session_sample_rate) || 0));
  if (!rum.endpoint) rum.enabled = false;
  return { ...cfg, rum };
}

// Single-page app: a JSON file the deployment mounts. RUM stays off on any failure, and on
// anything that isn't JSON (an SPA fallback answers index.html with 200).
export async function fetchRumConfig(path = '/observability.json'): Promise<RumConfig | null> {
  try {
    const res = await fetch(path, { cache: 'no-store', credentials: 'omit' });
    if (!res.ok || !(res.headers.get('content-type') ?? '').includes('application/json')) return null;
    const cfg = (await res.json()) as Partial<RumConfig>;
    return valid(cfg) ? withDefaults(cfg) : null;
  } catch {
    return null;
  }
}

// Fullstack app: the server renders <script type="application/json" id="rum-config">…</script>.
export function readRumConfig(elementId = 'rum-config'): RumConfig | null {
  try {
    const text = document.getElementById(elementId)?.textContent;
    const cfg = text ? (JSON.parse(text) as Partial<RumConfig>) : undefined;
    return valid(cfg) ? withDefaults(cfg) : null;
  } catch {
    return null;
  }
}
