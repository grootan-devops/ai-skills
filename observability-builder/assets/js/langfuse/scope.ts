// Template from the observability-builder skill: copy into the observability module (tested with the versions in README.md).
// The export scope for Langfuse (references/signals/genai.md, "Export scope"): which spans go to Langfuse, where
// each one is a billed observation. Self-contained: it depends on the OpenTelemetry API types only, so it works with
// the Langfuse SDK's span processor (`shouldExportSpan`) and with the module's own OTLP exporter alike.
//
//   ai_context  the spans of an AI feature or experiment item (stamped with langfuse.trace.name or
//               langfuse.experiment.id), minus non-AI CLIENT and PRODUCER spans (databases, HTTP, queues)
//   ai_only     AI spans only, plus an experiment item's root
//
// The Langfuse SDKs ask the filter when a span starts as well as when it ends, and mark the first span they expect
// to export under a parent they don't as the trace's app root. The entry span starts before the feature is declared
// and is stamped later, so at start every span that could still join a feature counts; the stamp decides at the end.
import { SpanKind } from '@opentelemetry/api';
import type { Attributes } from '@opentelemetry/api';
import type { ReadableSpan } from '@opentelemetry/sdk-trace';

export type Scope = 'ai_context' | 'ai_only';

// A feature's spans carry its trace name; an experiment item's (Langfuse's own runner included) its experiment id.
export const STAMPS = ['langfuse.trace.name', 'langfuse.experiment.id'] as const;

const AI_KEYS = ['gen_ai.operation.name', 'langfuse.observation.type', 'openinference.span.kind'] as const;

// A span that describes AI work: a GenAI operation, a Langfuse observation type or an OpenInference span kind.
export function isAiSpan(attributes: Attributes | undefined): boolean {
  return attributes !== undefined && AI_KEYS.some((key) => attributes[key] !== undefined);
}

export interface ScopeOptions {
  // The SDK's own idea of an AI span (`isDefaultExportSpan` from @langfuse/otel); none in the otel mode.
  isDefaultExportSpan?: (span: ReadableSpan) => boolean;
}

export function exportScope(scope: Scope, { isDefaultExportSpan }: ScopeOptions = {}): (span: ReadableSpan) => boolean {
  return (span) => {
    const attributes = span.attributes ?? {};
    if (span.ended && !STAMPS.some((key) => attributes[key] !== undefined)) return false; // outside every feature
    const ai = isAiSpan(attributes) || (isDefaultExportSpan?.(span) ?? false);
    if (scope === 'ai_only') return ai || attributes['langfuse.experiment.id'] !== undefined;
    return ai || (span.kind !== SpanKind.CLIENT && span.kind !== SpanKind.PRODUCER);
  };
}
