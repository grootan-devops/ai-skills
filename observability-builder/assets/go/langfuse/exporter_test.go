package langfuse

import (
	"bytes"
	"compress/gzip"
	"context"
	"encoding/base64"
	"io"
	"net/http"
	"net/http/httptest"
	"strconv"
	"strings"
	"sync"
	"sync/atomic"
	"testing"
	"time"
	"unicode/utf8"

	"go.opentelemetry.io/otel/attribute"
	"go.opentelemetry.io/otel/codes"
	sdktrace "go.opentelemetry.io/otel/sdk/trace"
	"go.opentelemetry.io/otel/trace"
	coltracepb "go.opentelemetry.io/proto/otlp/collector/trace/v1"
	"google.golang.org/protobuf/proto"
)

// standIn records what a Langfuse OTLP endpoint receives.
type standIn struct {
	mu      sync.Mutex
	raw     [][]byte
	spans   map[string]map[string]string // span name -> string attributes
	status  map[string]string            // span name -> status description
	headers []http.Header
}

func newStandIn(t *testing.T) (*standIn, *httptest.Server) {
	s := &standIn{spans: map[string]map[string]string{}, status: map[string]string{}}
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.URL.Path != TracesPath {
			t.Errorf("unexpected path %s", r.URL.Path)
			w.WriteHeader(http.StatusNotFound)
			return
		}
		var body io.Reader = r.Body
		if r.Header.Get("Content-Encoding") == "gzip" {
			gz, err := gzip.NewReader(r.Body)
			if err != nil {
				t.Fatal(err)
			}
			body = gz
		}
		raw, _ := io.ReadAll(body)
		req := &coltracepb.ExportTraceServiceRequest{}
		if err := proto.Unmarshal(raw, req); err != nil {
			t.Errorf("decode: %v", err)
		}
		s.mu.Lock()
		s.raw, s.headers = append(s.raw, raw), append(s.headers, r.Header.Clone())
		for _, rs := range req.ResourceSpans {
			for _, ss := range rs.ScopeSpans {
				for _, span := range ss.Spans {
					attrs := map[string]string{}
					for _, kv := range span.Attributes {
						attrs[kv.Key] = kv.Value.GetStringValue()
					}
					s.spans[span.Name], s.status[span.Name] = attrs, span.GetStatus().GetMessage()
				}
			}
		}
		s.mu.Unlock()
		w.Header().Set("Content-Type", "application/x-protobuf")
		w.WriteHeader(http.StatusOK)
	}))
	t.Cleanup(server.Close)
	return s, server
}

// decide: feature "quiet" may hold no content, "masked" is redacted, everything else is full.
func decide(set attribute.Set, _ trace.TraceID) (bool, func(string) string) {
	name, _ := set.Value("langfuse.trace.name")
	switch name.AsString() {
	case "quiet":
		return false, nil
	case "masked":
		return true, func(text string) string { return strings.ReplaceAll(text, "someone@example.com", "[email]") }
	}
	return true, nil
}

func TestExporterScopeGuardAndHeaders(t *testing.T) {
	stand, server := newStandIn(t)
	ctx := context.Background()
	exporter, err := NewExporter(ctx, Config{BaseURL: server.URL + "/", PublicKey: "pk-lf-1", SecretKey: "sk-lf-2"}, NewGuard(decide, 40))
	if err != nil {
		t.Fatal(err)
	}
	provider := sdktrace.NewTracerProvider(sdktrace.WithSyncer(exporter))
	tracer := provider.Tracer("test")
	start := func(name string, kind trace.SpanKind, attrs ...attribute.KeyValue) trace.Span {
		_, span := tracer.Start(ctx, name, trace.WithSpanKind(kind), trace.WithAttributes(attrs...))
		return span
	}
	feature := func(name string) attribute.KeyValue { return attribute.String("langfuse.trace.name", name) }
	chat := attribute.String("gen_ai.operation.name", "chat")
	long := `[{"role":"user","content":"first question that is long"},{"role":"user","content":"newest"}]`

	start("plain", trace.SpanKindInternal).End()
	start("POST /api/chat", trace.SpanKindServer, feature("assistant")).End()
	start("SELECT orders", trace.SpanKindClient, feature("assistant"), attribute.String("db.system.name", "postgresql")).End()
	start("chat full", trace.SpanKindClient, feature("assistant"), chat, attribute.String("gen_ai.input.messages", long)).End()
	start("chat quiet", trace.SpanKindClient, feature("quiet"), chat, attribute.String("gen_ai.input.messages", "CANARY-1")).End()
	start("chat masked", trace.SpanKindClient, feature("masked"), chat, attribute.String("gen_ai.input.messages", "mail someone@example.com")).End()
	failed := start("embeddings failed", trace.SpanKindClient, feature("quiet"), attribute.String("gen_ai.operation.name", "embeddings"),
		attribute.String("error.type", "APIError"))
	failed.AddEvent("gen_ai.client.inference.operation.details", trace.WithAttributes(attribute.String("detail", "CANARY-2")))
	failed.SetStatus(codes.Error, "provider said: input began CANARY-3")
	failed.End()
	start("experiment orders-assistant", trace.SpanKindInternal, attribute.String("langfuse.experiment.id", "e-1")).End()
	if err := provider.Shutdown(ctx); err != nil {
		t.Fatal(err)
	}

	names := []string{}
	for name := range stand.spans {
		names = append(names, name)
	}
	for _, gone := range []string{"plain", "SELECT orders"} {
		if _, ok := stand.spans[gone]; ok {
			t.Errorf("%q reached Langfuse: outside the scope", gone)
		}
	}
	for _, kept := range []string{"POST /api/chat", "chat full", "chat quiet", "chat masked", "embeddings failed", "experiment orders-assistant"} {
		if _, ok := stand.spans[kept]; !ok {
			t.Errorf("%q missing; got %v", kept, names)
		}
	}
	if got := stand.spans["chat full"]["gen_ai.input.messages"]; got != `[{"role":"user","content":"newest"}]` {
		t.Errorf("capped list = %q", got)
	}
	if _, ok := stand.spans["chat quiet"]["gen_ai.input.messages"]; ok {
		t.Error("content kept where the feature allows none")
	}
	if got := stand.spans["chat masked"]["gen_ai.input.messages"]; got != "mail [email]" {
		t.Errorf("redacted = %q", got)
	}
	if got := stand.spans["embeddings failed"][string(StatusMessageKey)]; got != "APIError" {
		t.Errorf("status message = %q", got)
	}
	if got := stand.status["embeddings failed"]; got != "APIError" {
		t.Errorf("status description = %q", got)
	}
	want := "Basic " + base64.StdEncoding.EncodeToString([]byte("pk-lf-1:sk-lf-2"))
	for _, header := range stand.headers {
		if header.Get("Authorization") != want || header.Get("X-Langfuse-Ingestion-Version") != "4" || header.Get("Content-Encoding") != "gzip" {
			t.Errorf("headers: auth ok %v, ingestion %q, encoding %q", header.Get("Authorization") == want,
				header.Get("X-Langfuse-Ingestion-Version"), header.Get("Content-Encoding"))
		}
	}
	for _, raw := range stand.raw {
		if bytes.Contains(raw, []byte("CANARY")) {
			t.Error("content reached Langfuse where it isn't allowed")
		}
	}
}

func TestCap(t *testing.T) {
	if got := Cap("short", 10); got != "short" {
		t.Errorf("Cap = %q", got)
	}
	if got := Cap("abcdefghij", 5); got != "abcd…" {
		t.Errorf("Cap = %q", got)
	}
	if got := Cap("ééééééééééééééé", 20); got != "ééééééééééééééé" {
		t.Errorf("characters, not bytes: Cap = %q", got)
	}
	if got := Cap(`[{"a":"<é>"},{"b":"<ü>"}]`, 15); got != `[{"b":"<ü>"}]` {
		t.Errorf("a list keeps its newest items, unescaped: Cap = %q", got)
	}
}

// The ops exporter's variables (protocol, headers, certificates) must not reach the Langfuse exporter.
func TestExporterIgnoresTheOpsExporterVariables(t *testing.T) {
	t.Setenv("OTEL_EXPORTER_OTLP_PROTOCOL", "grpc")
	t.Setenv("OTEL_EXPORTER_OTLP_TRACES_PROTOCOL", "grpc")
	t.Setenv("OTEL_EXPORTER_OTLP_HEADERS", "x-ops-token=CANARY-OPS")
	t.Setenv("OTEL_EXPORTER_OTLP_TRACES_HEADERS", "x-ops-token=CANARY-OPS")
	t.Setenv("OTEL_EXPORTER_OTLP_CERTIFICATE", "/nonexistent/ca.pem")
	t.Setenv("OTEL_EXPORTER_OTLP_TRACES_ENDPOINT", "http://127.0.0.1:1/v1/traces")
	stand, server := newStandIn(t)
	ctx := context.Background()
	exporter, err := NewExporter(ctx, Config{BaseURL: server.URL, PublicKey: "pk-lf-1", SecretKey: "sk-lf-2"}, NewGuard(decide, 100))
	if err != nil {
		t.Fatal(err)
	}
	provider := sdktrace.NewTracerProvider(sdktrace.WithSyncer(exporter))
	_, span := provider.Tracer("test").Start(ctx, "chat stub", trace.WithSpanKind(trace.SpanKindClient),
		trace.WithAttributes(attribute.String("langfuse.trace.name", "assistant"), attribute.String("gen_ai.operation.name", "chat")))
	span.End()
	if err := provider.Shutdown(ctx); err != nil {
		t.Fatal(err)
	}
	if len(stand.headers) != 1 {
		t.Fatalf("%d exports reached the stand-in", len(stand.headers))
	}
	if stand.headers[0].Get("x-ops-token") != "" {
		t.Error("the ops exporter's header reached Langfuse")
	}
}

// A 503 with Retry-After is tried again; a 400 isn't.
func TestExporterRetries(t *testing.T) {
	var calls atomic.Int32
	var status atomic.Int32
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		calls.Add(1)
		if code := int(status.Load()); code != 0 {
			if code == http.StatusServiceUnavailable {
				status.Store(0) // the next attempt succeeds
				w.Header().Set("Retry-After", "1")
			}
			w.WriteHeader(code)
			return
		}
		w.WriteHeader(http.StatusOK)
	}))
	defer server.Close()
	ctx := context.Background()
	send := func() error {
		exporter, err := NewExporter(ctx, Config{BaseURL: server.URL, PublicKey: "pk-lf-1", SecretKey: "sk-lf-2"}, nil)
		if err != nil {
			t.Fatal(err)
		}
		provider := sdktrace.NewTracerProvider()
		_, span := provider.Tracer("test").Start(ctx, "chat stub", trace.WithAttributes(
			attribute.String("langfuse.trace.name", "assistant"), attribute.String("gen_ai.operation.name", "chat")))
		span.End()
		stub := span.(sdktrace.ReadOnlySpan)
		return exporter.ExportSpans(ctx, []sdktrace.ReadOnlySpan{stub})
	}
	status.Store(http.StatusServiceUnavailable)
	if err := send(); err != nil || calls.Load() != 2 {
		t.Errorf("a 503 should be retried once and then succeed: err %v, %d calls", err, calls.Load())
	}
	calls.Store(0)
	status.Store(http.StatusBadRequest)
	err := send()
	if err == nil || calls.Load() != 1 || strings.Contains(err.Error(), server.URL) {
		t.Errorf("a 400 is not retried and the error has no URL: err %v, %d calls", err, calls.Load())
	}
}

// The translation's keys reach the Langfuse copy only, after the guard.
func TestExporterTranslation(t *testing.T) {
	stand, server := newStandIn(t)
	ctx := context.Background()
	exporter, err := NewExporter(ctx, Config{BaseURL: server.URL, PublicKey: "pk-lf-1", SecretKey: "sk-lf-2"}, NewGuard(decide, 100))
	if err != nil {
		t.Fatal(err)
	}
	exporter.WithTranslation(func(span sdktrace.ReadOnlySpan) []attribute.KeyValue {
		for _, kv := range span.Attributes() {
			if kv.Key == "gen_ai.prompt.name" {
				return []attribute.KeyValue{attribute.String("langfuse.observation.prompt.name", kv.Value.AsString())}
			}
		}
		return nil
	})
	provider := sdktrace.NewTracerProvider(sdktrace.WithSyncer(exporter))
	_, span := provider.Tracer("test").Start(ctx, "chat linked", trace.WithSpanKind(trace.SpanKindClient), trace.WithAttributes(
		attribute.String("langfuse.trace.name", "quiet"), attribute.String("gen_ai.operation.name", "chat"),
		attribute.String("gen_ai.prompt.name", "support-chat"), attribute.String("gen_ai.input.messages", "CANARY-T")))
	span.End()
	if err := provider.Shutdown(ctx); err != nil {
		t.Fatal(err)
	}
	attrs := stand.spans["chat linked"]
	if attrs["langfuse.observation.prompt.name"] != "support-chat" {
		t.Errorf("translation missing: %v", attrs)
	}
	if _, ok := attrs["gen_ai.input.messages"]; ok {
		t.Error("the guard still applies with a translation")
	}
}

// A batch is split so each request stays under the cap; a span that alone exceeds it is dropped and counted.
func TestExporterSplitsRequestsUnderTheSizeCap(t *testing.T) {
	stand, server := newStandIn(t)
	ctx := context.Background()
	const limit = 4096
	exporter, err := NewExporter(ctx, Config{BaseURL: server.URL, PublicKey: "pk-lf-1", SecretKey: "sk-lf-2",
		MaxRequestBytes: limit}, nil)
	if err != nil {
		t.Fatal(err)
	}
	provider := sdktrace.NewTracerProvider()
	tracer := provider.Tracer("test")
	var spans []sdktrace.ReadOnlySpan
	add := func(name string, size int) {
		_, span := tracer.Start(ctx, name, trace.WithAttributes(attribute.String("langfuse.trace.name", "assistant"),
			attribute.String("gen_ai.operation.name", "chat"), attribute.String("gen_ai.input.messages", strings.Repeat("x", size))))
		span.End()
		spans = append(spans, span.(sdktrace.ReadOnlySpan))
	}
	for i := 0; i < 12; i++ {
		add("chat "+strconv.Itoa(i), 900)
	}
	add("chat huge", 2*limit)
	if err := exporter.ExportSpans(ctx, spans); err != nil {
		t.Fatal(err)
	}
	if len(stand.raw) < 3 {
		t.Errorf("expected the batch split into several requests, got %d", len(stand.raw))
	}
	for _, raw := range stand.raw {
		if len(raw) > limit {
			t.Errorf("request body %d bytes > cap %d", len(raw), limit)
		}
	}
	if len(stand.spans) != 12 {
		t.Errorf("%d spans arrived, want 12", len(stand.spans))
	}
	if _, ok := stand.spans["chat huge"]; ok {
		t.Error("the oversized span was sent")
	}
}

// Capping a long list is linear: thousands of items cap quickly.
func TestCapLongListIsLinear(t *testing.T) {
	items := make([]string, 20000)
	for i := range items {
		items[i] = `{"role":"user","content":"message ` + strconv.Itoa(i) + `"}`
	}
	start := time.Now()
	got := Cap("["+strings.Join(items, ",")+"]", 200)
	if time.Since(start) > 2*time.Second {
		t.Errorf("Cap took %v", time.Since(start))
	}
	if !strings.HasSuffix(got, `"message 19999"}]`) || utf8.RuneCountInString(got) > 200 {
		t.Errorf("Cap = %q", got)
	}
}

// Request context and HTTP detail never reach Langfuse, in any content mode and without a guard; the feature context,
// the GenAI attributes and allowed content stay.
func TestExporterDropsRequestContext(t *testing.T) {
	requestContext := []attribute.KeyValue{
		attribute.String("client.address", "203.0.113.7"), attribute.Int("client.port", 51000),
		attribute.String("user_agent.original", "curl/8.0"), attribute.String("url.query", "token=CANARY-Q"),
		attribute.String("url.full", "https://shop.example/api/chat?token=CANARY-Q"),
		attribute.StringSlice("http.request.header.x-forwarded-for", []string{"198.51.100.1"}),
		attribute.StringSlice("http.response.header.set-cookie", []string{"sid=CANARY-C"}),
		attribute.String("http.request.cookie.sid", "CANARY-C"), attribute.String("network.peer.address", "10.0.0.9"),
		attribute.String("http.user_agent", "curl/8.0"),
	}
	for _, guard := range []Guard{NewGuard(decide, 100), nil} {
		stand, server := newStandIn(t)
		ctx := context.Background()
		exporter, err := NewExporter(ctx, Config{BaseURL: server.URL, PublicKey: "pk-lf-1", SecretKey: "sk-lf-2"}, guard)
		if err != nil {
			t.Fatal(err)
		}
		provider := sdktrace.NewTracerProvider(sdktrace.WithSyncer(exporter))
		tracer := provider.Tracer("test")
		for _, feature := range []string{"assistant", "quiet", "masked"} {
			attrs := append([]attribute.KeyValue{attribute.String("langfuse.trace.name", feature),
				attribute.String("langfuse.user.id", "u-1"), attribute.String("http.route", "/api/chat"),
				attribute.String("gen_ai.operation.name", "chat"), attribute.String("gen_ai.input.messages", "hello")},
				requestContext...)
			_, span := tracer.Start(ctx, "POST "+feature, trace.WithSpanKind(trace.SpanKindServer), trace.WithAttributes(attrs...))
			span.End()
		}
		if err := provider.Shutdown(ctx); err != nil {
			t.Fatal(err)
		}
		for _, feature := range []string{"assistant", "quiet", "masked"} {
			got, ok := stand.spans["POST "+feature]
			if !ok {
				t.Fatalf("guard %v: %q missing", guard != nil, feature)
			}
			for _, kv := range requestContext {
				if _, leaked := got[string(kv.Key)]; leaked {
					t.Errorf("guard %v, %s: %s reached Langfuse", guard != nil, feature, kv.Key)
				}
			}
			for _, key := range []string{"langfuse.trace.name", "langfuse.user.id", "http.route", "gen_ai.operation.name"} {
				if _, kept := got[key]; !kept {
					t.Errorf("guard %v, %s: %s missing", guard != nil, feature, key)
				}
			}
			if _, kept := got["gen_ai.input.messages"]; kept != (guard == nil || feature != "quiet") {
				t.Errorf("guard %v, %s: content kept = %v", guard != nil, feature, kept)
			}
		}
		for _, raw := range stand.raw {
			if bytes.Contains(raw, []byte("CANARY")) || bytes.Contains(raw, []byte("203.0.113.7")) {
				t.Error("request context reached Langfuse")
			}
		}
	}
}

func TestIsRequestContextKey(t *testing.T) {
	for _, key := range []string{"client.address", "user_agent.original", "http.request.header.authorization",
		"http.response.header.set-cookie", "http.request.cookie.sid", "url.query", "url.full", "network.peer.port"} {
		if !IsRequestContextKey(attribute.Key(key)) {
			t.Errorf("%s not request context", key)
		}
	}
	for _, key := range []string{"http.route", "http.request.method", "http.response.status_code", "url.path", "url.scheme",
		"server.address", "network.protocol.version", "langfuse.user.id", "gen_ai.request.model", "clientele.id"} {
		if IsRequestContextKey(attribute.Key(key)) {
			t.Errorf("%s dropped", key)
		}
	}
}
