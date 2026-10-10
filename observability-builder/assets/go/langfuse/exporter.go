package langfuse

import (
	"bytes"
	"compress/gzip"
	"context"
	"crypto/tls"
	"encoding/base64"
	"errors"
	"fmt"
	"io"
	"net/http"
	"net/url"
	"strconv"
	"strings"
	"time"

	"go.opentelemetry.io/otel/attribute"
	"go.opentelemetry.io/otel/codes"
	"go.opentelemetry.io/otel/exporters/otlp/otlptrace"
	sdktrace "go.opentelemetry.io/otel/sdk/trace"
	coltracepb "go.opentelemetry.io/proto/otlp/collector/trace/v1"
	tracepb "go.opentelemetry.io/proto/otlp/trace/v1"
	"google.golang.org/protobuf/proto"
)

// TracesPath is Langfuse's OTLP traces path; Langfuse takes OTLP over HTTP only.
const TracesPath = "/api/public/otel/v1/traces"

// Config is what the yml's genai.langfuse section gives the exporter and the client.
type Config struct {
	BaseURL   string        // the origin, e.g. https://cloud.langfuse.com
	PublicKey string        // from a mounted file, never the environment
	SecretKey string        // from a mounted file, never the environment
	Timeout   time.Duration // per export or API request; 10s when zero
	Scope     string        // ai_context (default) or ai_only
	// HTTPClient, when set, is the REST client's (the module's traced client, so a prompt fetch inside a request
	// gets its CLIENT span); the exporter always uses its own.
	HTTPClient *http.Client
	// TLS, when set, is the exporter's (and the default REST client's) TLS configuration, e.g. a private CA's pool
	// for a self-hosted Langfuse; nil uses the system pool, which also reads SSL_CERT_FILE and SSL_CERT_DIR.
	TLS *tls.Config
}

// Exporter sends the spans the export scope selects to Langfuse, each one through the content guard.
type Exporter struct {
	next      sdktrace.SpanExporter
	scope     string
	guard     Guard
	translate Translate
}

// Translate returns the Langfuse-only keys for a span's Langfuse copy, from the module's translation table (the
// prompt link, the completion start time, explicit types), so those keys never sit on the live span the ops exporter
// shares. It runs after the guard, on keys the guard leaves alone; its keys win over the span's own.
type Translate func(span sdktrace.ReadOnlySpan) []attribute.KeyValue

// WithTranslation sets the translation applied to each Langfuse copy; call it before the exporter is in use.
func (e *Exporter) WithTranslation(translate Translate) *Exporter {
	e.translate = translate
	return e
}

// NewExporter builds the exporter: OTLP over HTTP with Basic auth from the keys, the ingestion header (spans show up
// at once in Langfuse 4) and gzip. Add it to the module's tracer provider in a batch span processor.
//
// It has a transport of its own: otlptracehttp reads the OTEL_EXPORTER_OTLP_* variables the loader writes for the
// ops exporter (its protocol, headers, certificates), and none of them may reach Langfuse.
func NewExporter(ctx context.Context, cfg Config, guard Guard) (*Exporter, error) {
	timeout := cfg.Timeout
	if timeout <= 0 {
		timeout = 10 * time.Second
	}
	next, err := otlptrace.New(ctx, &transport{
		url:  strings.TrimRight(cfg.BaseURL, "/") + TracesPath,
		auth: "Basic " + basic(cfg),
		http: &http.Client{Timeout: timeout, Transport: httpTransport(cfg.TLS)},
	})
	if err != nil {
		return nil, err
	}
	return &Exporter{next: next, scope: cfg.Scope, guard: guard}, nil
}

// transport posts each batch as gzip OTLP/protobuf. A batch that meets a 429, 502, 503 or 504 or a network error is
// tried again, at most three times in all, after the server's Retry-After (at most 5 s) or a short backoff, within
// the batch processor's export timeout; other failures drop it. Its errors carry the status code, never the URL.
type transport struct {
	url, auth string
	http      *http.Client
}

func (t *transport) Start(context.Context) error { return nil }

func (t *transport) Stop(context.Context) error {
	t.http.CloseIdleConnections()
	return nil
}

func (t *transport) UploadTraces(ctx context.Context, spans []*tracepb.ResourceSpans) error {
	raw, err := proto.Marshal(&coltracepb.ExportTraceServiceRequest{ResourceSpans: spans})
	if err != nil {
		return err
	}
	var body bytes.Buffer
	gz := gzip.NewWriter(&body)
	if _, err := gz.Write(raw); err != nil {
		return err
	}
	if err := gz.Close(); err != nil {
		return err
	}
	for attempt := 1; ; attempt++ {
		wait, err := t.post(ctx, body.Bytes())
		if err == nil || wait < 0 || attempt == 3 {
			return err
		}
		if wait == 0 {
			wait = time.Duration(attempt) * 500 * time.Millisecond
		}
		select {
		case <-ctx.Done():
			return err
		case <-time.After(wait):
		}
	}
}

// post sends one attempt; wait is how long to wait before another (0: the backoff), or -1 when it mustn't be retried.
func (t *transport) post(ctx context.Context, payload []byte) (time.Duration, error) {
	req, err := http.NewRequestWithContext(ctx, http.MethodPost, t.url, bytes.NewReader(payload))
	if err != nil {
		return -1, err
	}
	req.Header.Set("Content-Type", "application/x-protobuf")
	req.Header.Set("Content-Encoding", "gzip")
	req.Header.Set("Authorization", t.auth)
	req.Header.Set("x-langfuse-ingestion-version", "4")
	resp, err := t.http.Do(req)
	if err != nil {
		var urlErr *url.Error
		if errors.As(err, &urlErr) {
			err = urlErr.Err
		}
		return 0, fmt.Errorf("langfuse traces: %w", err)
	}
	defer resp.Body.Close()
	_, _ = io.Copy(io.Discard, io.LimitReader(resp.Body, 1<<20))
	switch {
	case resp.StatusCode/100 == 2:
		return 0, nil
	case resp.StatusCode == http.StatusTooManyRequests || resp.StatusCode == http.StatusBadGateway ||
		resp.StatusCode == http.StatusServiceUnavailable || resp.StatusCode == http.StatusGatewayTimeout:
		wait := time.Duration(0)
		if seconds, err := strconv.Atoi(resp.Header.Get("Retry-After")); err == nil && seconds > 0 {
			wait = min(time.Duration(seconds)*time.Second, 5*time.Second)
		}
		return wait, fmt.Errorf("langfuse traces: status %d", resp.StatusCode)
	default:
		return -1, fmt.Errorf("langfuse traces: status %d", resp.StatusCode)
	}
}

// httpTransport is a copy of the default transport, with tlsConfig when set.
func httpTransport(tlsConfig *tls.Config) http.RoundTripper {
	transport := http.DefaultTransport.(*http.Transport).Clone()
	if tlsConfig != nil {
		transport.TLSClientConfig = tlsConfig.Clone()
	}
	return transport
}

// ExportSpans implements sdktrace.SpanExporter.
func (e *Exporter) ExportSpans(ctx context.Context, spans []sdktrace.ReadOnlySpan) error {
	kept := make([]sdktrace.ReadOnlySpan, 0, len(spans))
	for _, span := range spans {
		if !ShouldExport(span, e.scope) {
			continue
		}
		if guarded, ok := e.guarded(span); ok {
			kept = append(kept, guarded)
		}
	}
	if len(kept) == 0 {
		return nil
	}
	return e.next.ExportSpans(ctx, kept)
}

// Shutdown implements sdktrace.SpanExporter.
func (e *Exporter) Shutdown(ctx context.Context) error { return e.next.Shutdown(ctx) }

// guarded is the span's Langfuse copy; a guard that panics keeps the span off the Langfuse path (fail closed).
func (e *Exporter) guarded(span sdktrace.ReadOnlySpan) (out sdktrace.ReadOnlySpan, ok bool) {
	defer func() {
		if recover() != nil {
			out, ok = nil, false
		}
	}()
	if e.guard == nil && e.translate == nil {
		return span, true
	}
	var drop []attribute.Key
	var set []attribute.KeyValue
	allowed := true
	if e.guard != nil {
		drop, set, allowed = e.guard(span.Attributes(), span.SpanContext().TraceID())
	}
	if e.translate != nil {
		set = append(set, e.translate(span)...)
	}
	events := span.Events()
	if !allowed {
		events = events[:0:0]
		for _, event := range span.Events() {
			if !strings.HasPrefix(event.Name, "gen_ai.") {
				events = append(events, event)
			}
		}
	}
	if len(drop) == 0 && len(set) == 0 && len(events) == len(span.Events()) {
		return span, true
	}
	status := span.Status()
	if !allowed && status.Code == codes.Error {
		for _, kv := range set {
			if kv.Key == StatusMessageKey {
				status.Description = kv.Value.AsString()
			}
		}
	}
	return guardedSpan{ReadOnlySpan: span, attrs: merge(span.Attributes(), drop, set), status: status, events: events}, true
}

// guardedSpan is a span's Langfuse copy: its own attributes, status and events, everything else from the original.
type guardedSpan struct {
	sdktrace.ReadOnlySpan
	attrs  []attribute.KeyValue
	status sdktrace.Status
	events []sdktrace.Event
}

func (s guardedSpan) Attributes() []attribute.KeyValue { return s.attrs }
func (s guardedSpan) Status() sdktrace.Status          { return s.status }
func (s guardedSpan) Events() []sdktrace.Event         { return s.events }

func merge(attrs []attribute.KeyValue, drop []attribute.Key, set []attribute.KeyValue) []attribute.KeyValue {
	skip := make(map[attribute.Key]bool, len(drop)+len(set))
	for _, key := range drop {
		skip[key] = true
	}
	for _, kv := range set {
		skip[kv.Key] = true
	}
	out := make([]attribute.KeyValue, 0, len(attrs)+len(set))
	for _, kv := range attrs {
		if !skip[kv.Key] {
			out = append(out, kv)
		}
	}
	return append(out, set...)
}

func basic(cfg Config) string {
	return base64.StdEncoding.EncodeToString([]byte(cfg.PublicKey + ":" + cfg.SecretKey))
}
