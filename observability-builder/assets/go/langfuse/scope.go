package langfuse

import (
	"slices"

	"go.opentelemetry.io/otel/attribute"
	sdktrace "go.opentelemetry.io/otel/sdk/trace"
	"go.opentelemetry.io/otel/trace"
)

// The stamps that put a span in Langfuse's scope: an AI feature's trace name, or an experiment item's id.
var stamps = []attribute.Key{"langfuse.trace.name", "langfuse.experiment.id"}

// ShouldExport is the export scope, decided when the span has ended: only the spans of an AI feature or an
// experiment item; ai_context leaves out non-AI client and producer spans (databases, HTTP, queues), ai_only keeps
// AI spans and experiment item roots.
func ShouldExport(span sdktrace.ReadOnlySpan, scope string) bool {
	// A clone: the span's processors share its attribute slice, and NewSet sorts what it is given.
	set := attribute.NewSet(slices.Clone(span.Attributes())...)
	stamped := false
	for _, key := range stamps {
		stamped = stamped || set.HasValue(key)
	}
	if !stamped {
		return false
	}
	ai := IsAISpan(set)
	if scope == "ai_only" {
		return ai || set.HasValue("langfuse.experiment.id")
	}
	kind := span.SpanKind()
	return ai || (kind != trace.SpanKindClient && kind != trace.SpanKindProducer)
}

// IsAISpan reports a span that describes AI work: a GenAI operation, a Langfuse observation type or an OpenInference
// span kind.
func IsAISpan(set attribute.Set) bool {
	for _, key := range []attribute.Key{"gen_ai.operation.name", "langfuse.observation.type", "openinference.span.kind"} {
		if set.HasValue(key) {
			return true
		}
	}
	return false
}
