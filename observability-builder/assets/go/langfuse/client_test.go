package langfuse

import (
	"context"
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"sync"
	"sync/atomic"
	"testing"
	"time"
)

func TestClientScoresAndPrompts(t *testing.T) {
	var fetches atomic.Int32
	var mu sync.Mutex
	var scores []map[string]any
	var auth string
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		mu.Lock()
		auth = r.Header.Get("Authorization")
		mu.Unlock()
		switch r.URL.Path {
		case "/api/public/scores":
			var score map[string]any
			_ = json.NewDecoder(r.Body).Decode(&score)
			mu.Lock()
			scores = append(scores, score)
			mu.Unlock()
			_, _ = w.Write([]byte(`{"id":"m-1:user_feedback"}`))
		case "/api/public/v2/prompts/support-chat":
			fetches.Add(1)
			if r.URL.Query().Get("label") != "production" {
				t.Errorf("label %q", r.URL.Query().Get("label"))
			}
			_, _ = w.Write([]byte(`{"name":"support-chat","version":3,"type":"text","prompt":"You help with orders.","config":{"temperature":0.2}}`))
		case "/api/public/v2/prompts/chatty":
			_, _ = w.Write([]byte(`{"name":"chatty","version":1,"type":"chat","prompt":[{"role":"system","content":"x"}]}`))
		default:
			w.WriteHeader(http.StatusInternalServerError)
		}
	}))
	defer server.Close()
	client := NewClient(Config{BaseURL: server.URL, PublicKey: "pk-lf-1", SecretKey: "sk-lf-2"})
	ctx := context.Background()

	if err := client.CreateScore(ctx, Score{ID: "m-1:user_feedback", Name: "user_feedback", Value: 1, DataType: "BOOLEAN",
		TraceID: "t-1", ObservationID: "o-1", Environment: "dev"}); err != nil {
		t.Fatal(err)
	}
	if score := scores[0]; score["id"] != "m-1:user_feedback" || score["dataType"] != "BOOLEAN" || score["environment"] != "dev" || score["comment"] != nil {
		t.Errorf("score body %v", score)
	}
	if auth != "Basic "+basic(Config{PublicKey: "pk-lf-1", SecretKey: "sk-lf-2"}) {
		t.Error("score sent without Basic auth from the keys")
	}
	for range 2 {
		prompt := client.Prompt(ctx, "support-chat", "production", "fallback")
		if prompt.Version != 3 || prompt.Fallback || prompt.Text != "You help with orders." || string(prompt.Config) != `{"temperature":0.2}` {
			t.Errorf("prompt %+v", prompt)
		}
	}
	if fetches.Load() != 1 {
		t.Errorf("fetched %d times: the cache should answer the second call", fetches.Load())
	}
	if prompt := client.Prompt(ctx, "chatty", "production", "fallback"); !prompt.Fallback || prompt.Text != "fallback" {
		t.Errorf("a chat prompt should be a fallback, got %+v", prompt)
	}
	if prompt := client.Prompt(ctx, "broken", "production", "fallback"); !prompt.Fallback {
		t.Errorf("a failing fetch should be a fallback, got %+v", prompt)
	}

	queue := client.Scores(4, nil)
	for _, id := range []string{"m-2:user_feedback", "m-2:user_feedback"} {
		if !queue.Send(Score{ID: id, Name: "user_feedback", Value: 0, DataType: "BOOLEAN", TraceID: "t-2"}) {
			t.Error("the queue refused a score")
		}
	}
	closeCtx, cancel := context.WithTimeout(ctx, 5*time.Second)
	defer cancel()
	if err := queue.Close(closeCtx); err != nil {
		t.Fatal(err)
	}
	if queue.Send(Score{Name: "late"}) {
		t.Error("a closed queue took a score")
	}
	mu.Lock()
	defer mu.Unlock()
	if len(scores) != 3 {
		t.Errorf("Close should send the queued scores: %d sent", len(scores))
	}
}

func TestPromptStaleAndFailedFetches(t *testing.T) {
	var fetches atomic.Int32
	var down atomic.Bool
	release := make(chan struct{}, 8)
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		fetches.Add(1)
		if down.Load() {
			w.WriteHeader(http.StatusServiceUnavailable)
			release <- struct{}{}
			return
		}
		_, _ = w.Write([]byte(`{"name":"support-chat","version":4,"type":"text","prompt":"v4"}`))
		release <- struct{}{}
	}))
	defer server.Close()
	client := NewClient(Config{BaseURL: server.URL, PublicKey: "pk-lf-1", SecretKey: "sk-lf-2"})
	ctx := context.Background()
	wait := func() {
		select {
		case <-release:
		case <-time.After(5 * time.Second):
			t.Fatal("no fetch")
		}
	}

	// A failed first fetch: the fallback, and no fetch again until the retry time.
	down.Store(true)
	if prompt := client.Prompt(ctx, "support-chat", "production", "fallback"); !prompt.Fallback {
		t.Fatalf("got %+v", prompt)
	}
	wait()
	start := time.Now()
	if prompt := client.Prompt(ctx, "support-chat", "production", "fallback"); !prompt.Fallback || fetches.Load() != 1 || time.Since(start) > 100*time.Millisecond {
		t.Errorf("a recent failure should give the fallback at once without fetching: %d fetches", fetches.Load())
	}

	// After the retry time a fetch succeeds; once stale, the copy is served at once and refreshed in the background.
	down.Store(false)
	client.mu.Lock()
	client.prompts["support-chat\x00production"].next = time.Time{}
	client.mu.Unlock()
	if prompt := client.Prompt(ctx, "support-chat", "production", "fallback"); prompt.Version != 4 {
		t.Fatalf("got %+v", prompt)
	}
	wait()
	down.Store(true)
	client.mu.Lock()
	client.prompts["support-chat\x00production"].next = time.Time{}
	client.mu.Unlock()
	if prompt := client.Prompt(ctx, "support-chat", "production", "fallback"); prompt.Version != 4 || prompt.Fallback {
		t.Errorf("a stale copy should be served while Langfuse is down, got %+v", prompt)
	}
	wait() // the background refresh, which fails
	time.Sleep(50 * time.Millisecond)
	if prompt := client.Prompt(ctx, "support-chat", "production", "fallback"); prompt.Version != 4 || fetches.Load() != 3 {
		t.Errorf("after a failed refresh the copy stays and no fetch happens before the retry time: %+v, %d fetches", prompt, fetches.Load())
	}
}

func TestDataset(t *testing.T) {
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		switch r.URL.Path {
		case "/api/public/v2/datasets/support-chat-golden":
			_, _ = w.Write([]byte(`{"id":"ds-1","name":"support-chat-golden"}`))
		case "/api/public/dataset-items":
			if r.URL.Query().Get("datasetName") != "support-chat-golden" || r.URL.Query().Get("version") != "2026-10-01T00:00:00Z" {
				t.Errorf("query %s", r.URL.RawQuery)
			}
			if r.URL.Query().Get("page") == "1" {
				_, _ = w.Write([]byte(`{"data":[{"id":"i-1","status":"ACTIVE","input":{"q":"a"},"expectedOutput":"x"}],"meta":{"totalPages":2}}`))
				return
			}
			_, _ = w.Write([]byte(`{"data":[{"id":"i-2","status":"ARCHIVED","input":{"q":"b"}}],"meta":{"totalPages":2}}`))
		default:
			w.WriteHeader(http.StatusNotFound)
		}
	}))
	defer server.Close()
	client := NewClient(Config{BaseURL: server.URL, PublicKey: "pk-lf-1", SecretKey: "sk-lf-2"})
	dataset, err := client.Dataset(context.Background(), "support-chat-golden", "2026-10-01T00:00:00Z")
	if err != nil {
		t.Fatal(err)
	}
	if dataset.ID != "ds-1" || len(dataset.Items) != 2 || dataset.Items[1].Status != "ARCHIVED" || string(dataset.Items[0].ExpectedOutput) != `"x"` {
		t.Errorf("dataset %+v", dataset)
	}
	if _, err := client.Dataset(context.Background(), "missing", ""); err == nil {
		t.Error("a missing dataset should be an error")
	}
}
