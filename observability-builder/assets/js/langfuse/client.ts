// Template from the observability-builder skill: copy into the observability module (tested with the versions in README.md).
// The process's one Langfuse API client (references/backends/langfuse.md, "One client per process"): scores in the
// hybrid and sdk modes, and app code's own Langfuse features (prompts, datasets) in every mode. The Langfuse SDKs keep
// state per process, so nothing outside the observability module constructs a client; app code takes this one.
//
// Scores are queued by the SDK and sent in the background (`score.create` batches; the client flushes them at
// shutdown). They are created with tracing suppressed and outside any request's context: the SDK's batch timer would
// otherwise inherit the request's context, and its call to Langfuse would show up as a CLIENT span of that request
// and in the client metrics, which never count the telemetry pipeline's own calls.
import { LangfuseClient } from '@langfuse/client';
import { ROOT_CONTEXT, context } from '@opentelemetry/api';
import { suppressTracing } from '@opentelemetry/core';

export interface ClientConfig {
  baseUrl: string;
  publicKey: string; // from a mounted file, never the environment
  secretKey: string;
  timeoutMs?: number;
}

export function createClient({ baseUrl, publicKey, secretKey, timeoutMs }: ClientConfig): LangfuseClient {
  return new LangfuseClient({ baseUrl, publicKey, secretKey, timeout: Math.max(1, Math.round((timeoutMs ?? 10_000) / 1000)) });
}

export interface Score {
  id: string; // stable, e.g. {message id}:user_feedback, so a repeat replaces the score
  name: string;
  value: number | string;
  dataType: 'NUMERIC' | 'BOOLEAN' | 'CATEGORICAL';
  traceId: string;
  observationId?: string;
  comment?: string; // what a user wrote: only where the feature's content mode allows it
  environment: string; // a score's environment is `default` unless it is sent
}

export function sendScore(client: LangfuseClient, score: Score): void {
  context.with(suppressTracing(ROOT_CONTEXT), () => client.score.create(score));
}

// Pending scores, after the tracer provider has flushed.
export async function closeClient(client: LangfuseClient): Promise<void> {
  await context.with(suppressTracing(ROOT_CONTEXT), () => client.shutdown());
}
