package langfuse

import (
	"context"
	"testing"
	"time"

	"go.opentelemetry.io/otel/attribute"
	sdktrace "go.opentelemetry.io/otel/sdk/trace"
	"go.opentelemetry.io/otel/trace"
)

// reader stands in for the ops exporter: it reads every span's attributes on its own goroutine.
type reader struct{}

func (reader) ExportSpans(_ context.Context, spans []sdktrace.ReadOnlySpan) error {
	for _, span := range spans {
		for _, kv := range span.Attributes() {
			_ = kv.Key + attribute.Key(kv.Value.Emit())
		}
	}
	return nil
}

func (reader) Shutdown(context.Context) error { return nil }

// A span's processors share its attribute slice, and attribute.NewSet sorts what it is given: the Langfuse
// exporter must never sort the shared slice (run with -race).
func TestExporterLeavesSharedAttributesAlone(t *testing.T) {
	_, server := newStandIn(t)
	ctx := context.Background()
	exporter, err := NewExporter(ctx, Config{BaseURL: server.URL, PublicKey: "pk-lf-1", SecretKey: "sk-lf-2"}, NewGuard(decide, 100))
	if err != nil {
		t.Fatal(err)
	}
	// Short batch timeouts, so the two processors export while spans keep ending, as in a running service.
	provider := sdktrace.NewTracerProvider(
		sdktrace.WithBatcher(reader{}, sdktrace.WithBatchTimeout(time.Millisecond), sdktrace.WithMaxExportBatchSize(1)),
		sdktrace.WithBatcher(exporter, sdktrace.WithBatchTimeout(time.Millisecond), sdktrace.WithMaxExportBatchSize(1)))
	tracer := provider.Tracer("test")
	for i := 0; i < 200; i++ {
		_, span := tracer.Start(ctx, "chat stub", trace.WithSpanKind(trace.SpanKindClient), trace.WithAttributes(
			attribute.String("z.last", "z"), attribute.String("gen_ai.operation.name", "chat"),
			attribute.String("langfuse.trace.name", "assistant"), attribute.String("a.first", "a"),
			attribute.String("gen_ai.input.messages", "hello")))
		span.End()
		time.Sleep(time.Millisecond)
	}
	if err := provider.Shutdown(ctx); err != nil {
		t.Fatal(err)
	}
}
