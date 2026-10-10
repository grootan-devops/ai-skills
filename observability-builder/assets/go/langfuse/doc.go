// Package langfuse sends a service's AI spans to Langfuse where no Langfuse SDK exists (Go): an OTLP/HTTP exporter to
// {base_url}/api/public/otel/v1/traces with Basic auth and the ingestion header, behind the export scope and a content
// guard, and a small REST client for scores and the app's prompts (references/backends/langfuse.md, "App route").
//
// Copy the package into the service's observability module. The module builds the guard from its feature registry
// and content modes (NewGuard), adds the exporter to its tracer provider in a batch span processor (modes hybrid
// and otel, export app), and keeps the one Client of the process (mode hybrid: scores; app code: prompts).
package langfuse
