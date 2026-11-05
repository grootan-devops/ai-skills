// Template from the observability-builder skill: copy into the observability module (tested with the versions in README.md).
// The OTLP transports to Langfuse (references/backends/langfuse.md). LangfuseExporter is the otel mode's way, with no
// Langfuse SDK (the TypeScript form of the Go and Java assets' exporter): OTLP/protobuf over HTTP to
// {base_url}/api/public/otel/v1/traces with Basic auth built from the keys, x-langfuse-ingestion-version: 4 (real-time
// ingestion in Langfuse 4) and gzip; the export scope and the content guard work on a copy of each span. Add it to the
// module's tracer provider in a BatchSpanProcessor, next to the ops processor. sdkTransport is the same transport for
// LangfuseSpanProcessor's `exporter` option in the hybrid and sdk modes, with the headers the processor sends itself.
//
// Both read no OTEL_EXPORTER_OTLP_* variable. OTLPTraceExporter 0.222 merges OTEL_EXPORTER_OTLP_[TRACES_]HEADERS into
// its own headers, and takes its TLS CA, client certificate and key from OTEL_EXPORTER_OTLP_[TRACES_]CERTIFICATE,
// _CLIENT_CERTIFICATE and _CLIENT_KEY (and the compression, where none is given); the loader writes those for the ops
// exporter, and none of them may reach Langfuse. TLS to Langfuse uses the process trust store (NODE_EXTRA_CA_CERTS),
// as the Langfuse client does.
import { diag } from '@opentelemetry/api';
import type { Attributes, Exception } from '@opentelemetry/api';
import { ExportResultCode, setGlobalErrorHandler } from '@opentelemetry/core';
import type { ExportResult } from '@opentelemetry/core';
import { OTLPExporterBase } from '@opentelemetry/otlp-exporter-base';
import { createOtlpHttpExportDelegate, httpAgentFactoryFromOptions } from '@opentelemetry/otlp-exporter-base/node-http';
import { ProtobufTraceSerializer, TraceExporterMetricsHelper } from '@opentelemetry/otlp-transformer';
import type { IExportTraceServiceResponse, ISerializer } from '@opentelemetry/otlp-transformer';
import type { ReadableSpan, SpanExporter } from '@opentelemetry/sdk-trace';

import { guardedCopy } from './content.js';
import type { ContentGuard } from './content.js';
import { exportScope } from './scope.js';
import type { Scope } from './scope.js';

// Langfuse's OTLP traces path; Langfuse takes OTLP over HTTP only (protobuf or JSON).
export const TRACES_PATH = '/api/public/otel/v1/traces';

// One request's body before gzip, at most: a batch is split to fit, a span that alone exceeds it is dropped.
export const DEFAULT_MAX_REQUEST_BYTES = 3 * 1024 * 1024;

export interface LangfuseExporterConfig {
  baseUrl: string; // the origin, e.g. https://cloud.langfuse.com
  publicKey: string; // from a mounted file, never the environment
  secretKey: string;
  timeoutMs?: number; // per export request; 10 s when unset
  maxRequestBytes?: number; // per request body before gzip; DEFAULT_MAX_REQUEST_BYTES when unset
  scope?: Scope; // ai_context (default) or ai_only
}

interface Transport {
  url: string;
  headers: Record<string, string>;
  compression: 'gzip' | 'none';
  timeoutMs: number;
  maxRequestBytes: number;
}

// OTLP/protobuf, whose responses Langfuse answers as JSON (`{}`): an accepted export's body that isn't protobuf is read
// as no partial success, instead of the delegate's "could not deserialize response" warning.
const serializer: ISerializer<ReadableSpan[], IExportTraceServiceResponse> = {
  serializeRequest: (spans) => ProtobufTraceSerializer.serializeRequest(spans),
  deserializeResponse: (data) => {
    try {
      return ProtobufTraceSerializer.deserializeResponse(data);
    } catch {
      return {};
    }
  },
};

// OTLP/protobuf over HTTP from explicit settings only: OTLPTraceExporter's delegate (retries on 429/502/503/504, the
// concurrency limit, the response size cap) without its environment fallback. Protobuf costs less CPU to encode than
// JSON and makes a smaller body.
function transport(settings: Transport): SpanExporter {
  const headers = { ...settings.headers, 'Content-Type': 'application/x-protobuf' };
  return new SizeCapped(new OTLPExporterBase<ReadableSpan[]>(createOtlpHttpExportDelegate(
    {
      url: settings.url,
      headers: async () => ({ ...headers }), // a fresh object: the transport adds User-Agent to it
      compression: settings.compression,
      timeoutMillis: settings.timeoutMs,
      concurrencyLimit: 30,
      agentFactory: httpAgentFactoryFromOptions({ keepAlive: true }),
    },
    serializer,
    'otlp_http_span_exporter',
    TraceExporterMetricsHelper,
    undefined,
  )), settings.maxRequestBytes);
}

// Splits each batch into requests whose protobuf body stays within maxBytes. A batch whose estimate (a high-side bound
// from the strings' UTF-8 lengths and a fixed allowance per field, nothing serialized) fits goes out whole, which is
// the usual case. Only a batch near the cap is measured: each span serialized once on its own (its resource and scope
// included, so the measure errs high). A span that alone exceeds the cap is dropped, counted, and reported at most
// once a minute. Parts go out one after another; the result fails when any part fails.
export class SizeCapped implements SpanExporter {
  readonly #next: SpanExporter;
  readonly #maxBytes: number;
  #dropped = 0;
  #pending = 0;
  #warnedAt = 0;

  constructor(next: SpanExporter, maxBytes: number = DEFAULT_MAX_REQUEST_BYTES) {
    this.#next = next;
    this.#maxBytes = maxBytes > 0 ? maxBytes : DEFAULT_MAX_REQUEST_BYTES;
  }

  get dropped(): number {
    return this.#dropped;
  }

  export(spans: ReadableSpan[], resultCallback: (result: ExportResult) => void): void {
    if (estimateRequest(spans) <= this.#maxBytes) {
      this.#send([spans], resultCallback);
      return;
    }
    const parts: ReadableSpan[][] = [];
    let part: ReadableSpan[] = [];
    let size = 0;
    let dropped = 0;
    for (const span of spans) {
      const spanSize = ProtobufTraceSerializer.serializeRequest([span])?.byteLength ?? 0;
      if (spanSize > this.#maxBytes) {
        dropped += 1;
        continue;
      }
      if (part.length > 0 && size + spanSize > this.#maxBytes) {
        parts.push(part);
        part = [];
        size = 0;
      }
      part.push(span);
      size += spanSize;
    }
    if (part.length > 0) parts.push(part);
    if (dropped > 0) this.#warn(dropped);
    this.#send(parts, resultCallback);
  }

  #send(parts: ReadableSpan[][], resultCallback: (result: ExportResult) => void): void {
    let failed: ExportResult | undefined;
    const next = (index: number): void => {
      if (index >= parts.length) {
        resultCallback(failed ?? { code: ExportResultCode.SUCCESS });
        return;
      }
      this.#next.export(parts[index], (result) => {
        if (result.code !== ExportResultCode.SUCCESS) failed ??= result;
        next(index + 1);
      });
    };
    next(0);
  }

  #warn(count: number): void {
    this.#dropped += count;
    this.#pending += count;
    const now = Date.now();
    if (now - this.#warnedAt < 60_000) return;
    diag.warn(`langfuse traces: dropped ${this.#pending} span(s) larger than the request size cap (${this.#dropped} in all)`);
    this.#warnedAt = now;
    this.#pending = 0;
  }

  shutdown(): Promise<void> {
    return this.#next.shutdown();
  }

  forceFlush(): Promise<void> {
    return this.#next.forceFlush?.() ?? Promise.resolve();
  }
}

// A high-side bound on a request's protobuf size, nothing serialized: every string at its UTF-8 length plus its tag and
// the largest length prefix, every number at its largest varint, every message wrapper at its largest prefix, and
// each resource and each scope under it once.
const FIELD = 6; // a length-delimited field's tag and length prefix, at most
const NUMBER = 11; // a number's tag and largest encoding
const SPAN_FIXED = 128; // ids, kind, times, dropped counts, flags, status code and the span's own wrappers

export function estimateRequest(spans: ReadableSpan[]): number {
  const resources = new Map<unknown, Set<unknown>>();
  let size = 0;
  for (const span of spans) {
    size += estimateSpan(span);
    let scopes = resources.get(span.resource);
    if (scopes === undefined) {
      scopes = new Set();
      resources.set(span.resource, scopes);
      size += 4 * FIELD + estimateAttributes(span.resource.attributes) + text(span.resource.schemaUrl);
    }
    const scope = span.instrumentationScope;
    if (!scopes.has(scope)) {
      scopes.add(scope);
      size += 4 * FIELD + text(scope.name) + text(scope.version) + text(scope.schemaUrl);
    }
  }
  return size;
}

export function estimateSpan(span: ReadableSpan): number {
  let size = SPAN_FIXED + text(span.name) + text(span.status.message) + text(span.spanContext().traceState?.serialize())
    + estimateAttributes(span.attributes);
  for (const event of span.events) size += 2 * FIELD + 2 * NUMBER + text(event.name) + estimateAttributes(event.attributes ?? {});
  for (const link of span.links) {
    size += 64 + text(link.context.traceState?.serialize()) + estimateAttributes(link.attributes ?? {});
  }
  return size;
}

function estimateAttributes(attributes: Attributes): number {
  let size = 0;
  for (const [key, value] of Object.entries(attributes)) {
    size += 3 * FIELD + text(key); // the key-value, the any-value and an array's wrapper
    if (Array.isArray(value)) for (const item of value) size += FIELD + (typeof item === 'string' ? text(item) : NUMBER);
    else size += typeof value === 'string' ? text(value) : NUMBER;
  }
  return size;
}

function text(value: string | undefined): number {
  return value === undefined ? 0 : FIELD + Buffer.byteLength(value, 'utf8');
}

// Telemetry export failures are WARN, at most one line per distinct message a minute, never a stack or a body
// (references/backends/langfuse.md, "Export failures"). BatchSpanProcessor, Langfuse's span processor included, reports a
// failed export through OpenTelemetry's global error handler, whose default writes it with diag.error; this handler
// writes diag.warn instead. Call it once in the preload, after diag.setLogger(logger, DiagLogLevel.WARN).
export function routeExportErrors(): void {
  const seen = new Map<string, number>();
  setGlobalErrorHandler((error: Exception) => {
    const message = errorMessage(error).slice(0, 256);
    const now = Date.now();
    if (now - (seen.get(message) ?? -Infinity) < 60_000) return;
    if (seen.size >= 100) seen.clear(); // bounded: a flood of distinct messages restarts the window
    seen.set(message, now);
    diag.warn(`telemetry export failed: ${message}`);
  });
}

function errorMessage(error: Exception): string {
  if (typeof error === 'string') {
    try {
      const parsed: unknown = JSON.parse(error); // the SDK can pass an error serialised as {"message": …, "stack": …}
      if (typeof parsed === 'object' && parsed !== null && typeof (parsed as { message?: unknown }).message === 'string') {
        return (parsed as { message: string }).message;
      }
    } catch {
      // plain text
    }
    return error.split('\n')[0];
  }
  const { message, name, code } = error as { message?: unknown; name?: unknown; code?: unknown };
  return String(message ?? name ?? code ?? 'error').split('\n')[0];
}

function tracesUrl(baseUrl: string): string {
  return `${baseUrl.replace(/\/+$/, '')}${TRACES_PATH}`;
}

function basic(config: LangfuseExporterConfig): string {
  return `Basic ${Buffer.from(`${config.publicKey}:${config.secretKey}`).toString('base64')}`;
}

// For LangfuseSpanProcessor({ exporter }): the request the processor builds for itself (@langfuse/otel 5.11.1: its SDK
// headers, no compression), minus the environment, and in protobuf where the SDK's own sends JSON. sdk is LANGFUSE_SDK_NAME and LANGFUSE_SDK_VERSION from
// @langfuse/core, passed in so the otel mode's copy of this file needs no Langfuse package.
export function sdkTransport(config: LangfuseExporterConfig, sdk: { name: string; version: string }): SpanExporter {
  return transport({
    url: tracesUrl(config.baseUrl),
    headers: {
      Authorization: basic(config),
      'x-langfuse-sdk-name': sdk.name,
      'x-langfuse-sdk-version': sdk.version,
      'x-langfuse-public-key': config.publicKey,
    },
    compression: 'none',
    timeoutMs: config.timeoutMs ?? 10_000,
    maxRequestBytes: config.maxRequestBytes ?? DEFAULT_MAX_REQUEST_BYTES,
  });
}

export class LangfuseExporter implements SpanExporter {
  readonly #next: SpanExporter;
  readonly #shouldExport: (span: ReadableSpan) => boolean;
  readonly #guard: ContentGuard;

  constructor(config: LangfuseExporterConfig, guard: ContentGuard, next?: SpanExporter) {
    this.#next = next ?? transport({
      url: tracesUrl(config.baseUrl),
      headers: { Authorization: basic(config), 'x-langfuse-ingestion-version': '4' },
      compression: 'gzip',
      timeoutMs: config.timeoutMs ?? 10_000,
      maxRequestBytes: config.maxRequestBytes ?? DEFAULT_MAX_REQUEST_BYTES,
    });
    this.#shouldExport = exportScope(config.scope ?? 'ai_context');
    this.#guard = guard;
  }

  export(spans: ReadableSpan[], resultCallback: (result: ExportResult) => void): void {
    const kept: ReadableSpan[] = [];
    for (const span of spans) {
      try {
        if (!this.#shouldExport(span)) continue;
        const copy = guardedCopy(span, this.#guard);
        if (copy !== undefined) kept.push(copy);
      } catch {
        // fail closed: a span the scope or the guard can't read stays off the Langfuse path
      }
    }
    if (kept.length === 0) {
      resultCallback({ code: ExportResultCode.SUCCESS });
      return;
    }
    this.#next.export(kept, resultCallback);
  }

  shutdown(): Promise<void> {
    return this.#next.shutdown();
  }

  forceFlush(): Promise<void> {
    return this.#next.forceFlush?.() ?? Promise.resolve();
  }
}
