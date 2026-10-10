// Template from the observability-builder skill: copy into the observability module (tested with the versions in README.md).
// The content guard for the Langfuse copy of a span (references/signals/genai.md, "Content"), the TypeScript form of
// the Go and Java assets' guard. Self-contained: the module supplies `decide` (the feature's content mode, the
// content sample, the redactor); this file applies it to a span.
//
// Where content isn't allowed, every content key goes, the gen_ai.* span events go, and a failed AI span's status
// message (and status description) becomes its error type: instrumentations and Langfuse's OpenAI wrapper put the
// provider's message there, and providers and gateways can quote the input in it. Where content is allowed, string
// values are redacted (a feature in redacted mode) and capped at maxChars, a JSON list keeping its newest items. The
// retriever's query and documents become its input and output, which Langfuse doesn't map itself. A guard that fails
// keeps no content: the copy loses every content key.
import { SpanStatusCode } from '@opentelemetry/api';
import type { Attributes, AttributeValue, SpanStatus } from '@opentelemetry/api';
import type { ReadableSpan, TimedEvent } from '@opentelemetry/sdk-trace';

import { isAiSpan } from './scope.js';

export const STATUS_MESSAGE_KEY = 'langfuse.observation.status_message';

// The content keys: prompts, completions, tool arguments and results, retrieved documents and other model input and
// output (references/signals/genai.md, "Content keys"); one list for the module, the receiver and the collector.
const CONTENT_EXACT = new Set([
  'gen_ai.input.messages', 'gen_ai.output.messages', 'gen_ai.system_instructions', 'gen_ai.tool.definitions',
  'gen_ai.tool.call.arguments', 'gen_ai.tool.call.result', 'gen_ai.retrieval.documents', 'gen_ai.retrieval.query.text',
  'gen_ai.memory.query.text', 'gen_ai.memory.records', 'gen_ai.evaluation.explanation', 'gen_ai.prompt', 'gen_ai.completion',
  'langfuse.observation.input', 'langfuse.observation.output', 'langfuse.trace.input', 'langfuse.trace.output',
  'langfuse.experiment.item.expected_output', 'traceloop.entity.input', 'traceloop.entity.output', 'input.value',
  'output.value', 'llm.prompt_template.variables', 'ai.response.text', 'ai.response.object', 'ai.response.toolCalls',
  'ai.toolCall.args', 'ai.toolCall.result',
]);
const CONTENT_PATTERN = new RegExp(
  '^(gen_ai\\.prompt\\.\\d+\\.|gen_ai\\.completion\\.|gen_ai\\.prompt\\.variable|llm\\.(input|output)_messages\\.|'
  + 'llm\\.prompts\\.|llm\\.tools\\.|llm\\.prompt_template\\.template|retrieval\\.documents\\.|reranker\\.|'
  + 'embedding\\.embeddings\\.|ai\\.prompt)',
);

export function isContentKey(key: string): boolean {
  return CONTENT_EXACT.has(key) || CONTENT_PATTERN.test(key);
}

// A failed AI span's status message where content isn't allowed: its error type, never the provider's message.
export function failedStatus(attributes: Attributes): string | undefined {
  if (!isAiSpan(attributes)) return undefined;
  const errorType = attributes['error.type'];
  const level = String(attributes['langfuse.observation.level'] ?? '').toUpperCase();
  if (errorType === undefined && level !== 'ERROR') return undefined;
  return String(errorType ?? 'error').slice(0, 128);
}

// At most limit characters (code points); a JSON list keeps its newest items and stays valid JSON, other text is cut.
export function cap(text: string, limit: number): string {
  if (limit <= 0 || chars(text) <= limit) return text;
  let items: unknown;
  try {
    items = JSON.parse(text);
  } catch {
    items = undefined;
  }
  if (Array.isArray(items)) {
    const kept = [...items];
    while (kept.length > 0) {
      const encoded = JSON.stringify(kept);
      if (chars(encoded) <= limit) return encoded;
      kept.shift();
    }
  }
  return `${Array.from(text).slice(0, limit - 1).join('')}…`;
}

function chars(text: string): number {
  return Array.from(text).length;
}

export interface Decision {
  allowed: boolean;
  redact?: (text: string) => string; // set for a feature in redacted mode
}

// Whether a span's Langfuse copy may hold content, from the span's attributes and trace id (lowercase hex).
export type Decide = (attributes: Attributes, traceId: string) => Decision;

export interface GuardResult {
  drop: string[];
  set: Record<string, AttributeValue>;
  allowed: boolean;
}

export type ContentGuard = (attributes: Attributes, traceId: string) => GuardResult;

export function newContentGuard(decide: Decide, maxChars: number): ContentGuard {
  return (attributes, traceId) => {
    const content = Object.keys(attributes).filter(isContentKey);
    const failed = failedStatus(attributes);
    const { allowed, redact } = decide(attributes, traceId);
    if (!allowed) {
      const set: Record<string, AttributeValue> = {};
      if (failed !== undefined) set[STATUS_MESSAGE_KEY] = failed;
      return { drop: content, set, allowed: false };
    }
    // Content the module recorded is redacted already; an SDK's own capture (Langfuse's OpenAI wrapper records every
    // input and output) is redacted here, and the provider's message gives way to the error type.
    const set: Record<string, AttributeValue> = redact !== undefined && failed !== undefined ? { [STATUS_MESSAGE_KEY]: failed } : {};
    const retriever = attributes['langfuse.observation.type'] === 'retriever';
    for (const key of content) {
      const value = attributes[key];
      if (typeof value !== 'string') continue;
      const guarded = cap(redact !== undefined ? redact(value) : value, maxChars);
      set[key] = guarded;
      if (retriever && key === 'gen_ai.retrieval.query.text') set['langfuse.observation.input'] = guarded;
      if (retriever && key === 'gen_ai.retrieval.documents') set['langfuse.observation.output'] = guarded;
    }
    return { drop: [], set, allowed: true };
  };
}

export interface SpanChanges {
  attributes?: Attributes;
  status?: SpanStatus;
  events?: TimedEvent[];
}

// A copy of an ended span with its own attributes, status and events; everything else comes from the original. The
// copy owns its attributes object, so a processor that edits attributes in place (Langfuse's mask and media
// handling) never touches the span other processors export.
export function copySpan(span: ReadableSpan, { attributes, status, events }: SpanChanges = {}): ReadableSpan {
  const spanContext = span.spanContext();
  return {
    name: span.name,
    kind: span.kind,
    spanContext: () => spanContext,
    parentSpanContext: span.parentSpanContext,
    startTime: span.startTime,
    endTime: span.endTime,
    status: status ?? span.status,
    attributes: { ...(attributes ?? span.attributes) },
    links: span.links,
    events: events ?? span.events,
    duration: span.duration,
    ended: span.ended,
    resource: span.resource,
    instrumentationScope: span.instrumentationScope,
    droppedAttributesCount: span.droppedAttributesCount,
    droppedEventsCount: span.droppedEventsCount,
    droppedLinksCount: span.droppedLinksCount,
  };
}

function withoutContentEvents(events: TimedEvent[]): TimedEvent[] {
  return events.filter((event) => !event.name.startsWith('gen_ai.'));
}

function failClosed(span: ReadableSpan): ReadableSpan | undefined {
  try {
    const attributes: Attributes = {};
    for (const [key, value] of Object.entries(span.attributes)) if (!isContentKey(key)) attributes[key] = value;
    const failed = failedStatus(span.attributes);
    if (failed !== undefined) attributes[STATUS_MESSAGE_KEY] = failed;
    const status = failed !== undefined && span.status.code === SpanStatusCode.ERROR ? { code: SpanStatusCode.ERROR, message: failed } : span.status;
    return copySpan(span, { attributes, status, events: withoutContentEvents(span.events) });
  } catch {
    return undefined; // nothing safe to send
  }
}

// The span's Langfuse copy after the guard; undefined when even the fail-closed copy can't be made.
export function guardedCopy(span: ReadableSpan, guard: ContentGuard): ReadableSpan | undefined {
  try {
    const { drop, set, allowed } = guard(span.attributes, span.spanContext().traceId);
    const attributes: Attributes = { ...span.attributes };
    for (const key of drop) delete attributes[key];
    Object.assign(attributes, set);
    // The guard sets the status message only where the provider's text must not survive: the description goes too.
    let status = span.status;
    const message = set[STATUS_MESSAGE_KEY];
    if (typeof message === 'string' && status.code === SpanStatusCode.ERROR) status = { code: SpanStatusCode.ERROR, message };
    return copySpan(span, { attributes, status, events: allowed ? span.events : withoutContentEvents(span.events) });
  } catch {
    return failClosed(span);
  }
}
