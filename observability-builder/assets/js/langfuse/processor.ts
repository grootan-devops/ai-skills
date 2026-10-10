// Template from the observability-builder skill: copy into the observability module (tested with the versions in README.md).
// The guarded processor proxy (references/backends/langfuse.md, "App route", JS row): wraps Langfuse's
// LangfuseSpanProcessor (modes hybrid and sdk) so that it exports the content guard's copy of each ended span. The
// SDK's own `mask` can't do this: it sees only the langfuse.* input, output and metadata keys and edits the live span,
// which every other processor (the ops exporter included) shares, so third-party gen_ai.* content would pass.
//
// onStart hands the live span through: the SDK marks the trace's app root there (and stamps the environment and
// release when it is configured with them).
import type { Context } from '@opentelemetry/api';
import type { ReadableSpan, Span, SpanProcessor } from '@opentelemetry/sdk-trace';

import { guardedCopy } from './content.js';
import type { ContentGuard } from './content.js';

export function guarded(inner: SpanProcessor, guard: ContentGuard): SpanProcessor {
  return {
    onStart(span: Span, parentContext: Context): void {
      inner.onStart(span, parentContext);
    },
    onEnd(span: ReadableSpan): void {
      const copy = guardedCopy(span, guard);
      if (copy !== undefined) inner.onEnd(copy);
    },
    forceFlush(): Promise<void> {
      return inner.forceFlush();
    },
    shutdown(): Promise<void> {
      return inner.shutdown();
    },
  };
}
