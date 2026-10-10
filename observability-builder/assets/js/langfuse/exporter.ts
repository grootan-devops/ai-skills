// Template from the observability-builder skill: copy into the observability module (tested with the versions in README.md).
// The OTLP transports to Langfuse (references/backends/langfuse.md). LangfuseExporter is the otel mode's way, with no
// Langfuse SDK (the TypeScript form of the Go and Java assets' exporter): OTLP over HTTP to
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
import { ExportResultCode } from '@opentelemetry/core';
import type { ExportResult } from '@opentelemetry/core';
import { OTLPExporterBase } from '@opentelemetry/otlp-exporter-base';
import { createOtlpHttpExportDelegate, httpAgentFactoryFromOptions } from '@opentelemetry/otlp-exporter-base/node-http';
import { JsonTraceSerializer, TraceExporterMetricsHelper } from '@opentelemetry/otlp-transformer';
import type { ReadableSpan, SpanExporter } from '@opentelemetry/sdk-trace';

import { guardedCopy } from './content.js';
import type { ContentGuard } from './content.js';
import { exportScope } from './scope.js';
import type { Scope } from './scope.js';

// Langfuse's OTLP traces path; Langfuse takes OTLP over HTTP only (protobuf or JSON).
export const TRACES_PATH = '/api/public/otel/v1/traces';

export interface LangfuseExporterConfig {
  baseUrl: string; // the origin, e.g. https://cloud.langfuse.com
  publicKey: string; // from a mounted file, never the environment
  secretKey: string;
  timeoutMs?: number; // per export request; 10 s when unset
  scope?: Scope; // ai_context (default) or ai_only
}

interface Transport {
  url: string;
  headers: Record<string, string>;
  compression: 'gzip' | 'none';
  timeoutMs: number;
}

// OTLP/JSON over HTTP from explicit settings only: OTLPTraceExporter's delegate (retries on 429/502/503/504, the
// concurrency limit, the response size cap) without its environment fallback.
function transport(settings: Transport): SpanExporter {
  const headers = { ...settings.headers, 'Content-Type': 'application/json' };
  return new OTLPExporterBase<ReadableSpan[]>(createOtlpHttpExportDelegate(
    {
      url: settings.url,
      headers: async () => ({ ...headers }), // a fresh object: the transport adds User-Agent to it
      compression: settings.compression,
      timeoutMillis: settings.timeoutMs,
      concurrencyLimit: 30,
      agentFactory: httpAgentFactoryFromOptions({ keepAlive: true }),
    },
    JsonTraceSerializer,
    'otlp_http_span_exporter',
    TraceExporterMetricsHelper,
    undefined,
  ));
}

function tracesUrl(baseUrl: string): string {
  return `${baseUrl.replace(/\/+$/, '')}${TRACES_PATH}`;
}

function basic(config: LangfuseExporterConfig): string {
  return `Basic ${Buffer.from(`${config.publicKey}:${config.secretKey}`).toString('base64')}`;
}

// For LangfuseSpanProcessor({ exporter }): the request the processor builds for itself (@langfuse/otel 5.11.1: its SDK
// headers, no compression), minus the environment. sdk is LANGFUSE_SDK_NAME and LANGFUSE_SDK_VERSION from
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
