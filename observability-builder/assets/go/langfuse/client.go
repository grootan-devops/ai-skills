package langfuse

import (
	"bytes"
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"net/http"
	"net/url"
	"strings"
	"sync"
	"time"
)

// Client is the process's one Langfuse API client: the module's scores (mode hybrid), the app's prompts and an
// experiment runner's datasets.
type Client struct {
	base, auth string
	http       *http.Client
	timeout    time.Duration // per score send
	ttl, retry time.Duration
	mu         sync.Mutex
	prompts    map[string]*cachedPrompt
}

// Score is one score, sent by the server under a stable id, so a repeat replaces it.
type Score struct {
	ID            string  `json:"id,omitempty"`
	Name          string  `json:"name"`
	Value         float64 `json:"value"`
	DataType      string  `json:"dataType,omitempty"` // NUMERIC, BOOLEAN, CATEGORICAL
	TraceID       string  `json:"traceId,omitempty"`
	ObservationID string  `json:"observationId,omitempty"`
	Comment       string  `json:"comment,omitempty"` // what a user wrote: only where the feature's content mode allows
	Environment   string  `json:"environment,omitempty"`
}

// Prompt is the prompt app code uses and hands to the model-call helper for the generation's link.
type Prompt struct {
	Text     string
	Name     string
	Version  int
	Config   json.RawMessage // the version's config (e.g. model parameters), nil when it has none
	Fallback bool            // the text kept in code: the generation is WARNING and unlinked
}

type cachedPrompt struct {
	prompt Prompt    // the last good copy; Version 0 when there is none
	next   time.Time // no fetch before this: the copy is fresh, or the last fetch failed
	busy   bool      // a background refresh is running
}

// NewClient builds the client. Prompts stay fresh for a minute, as in the SDKs; a failed fetch is retried after
// 30 s, and the fallback (or the stale copy) is served meanwhile.
func NewClient(cfg Config) *Client {
	timeout := cfg.Timeout
	if timeout <= 0 {
		timeout = 10 * time.Second
	}
	client := cfg.HTTPClient
	if client == nil {
		client = &http.Client{Timeout: timeout, Transport: httpTransport(cfg.TLS)}
	}
	return &Client{base: strings.TrimRight(cfg.BaseURL, "/"), auth: "Basic " + basic(cfg), http: client,
		timeout: timeout, ttl: time.Minute, retry: 30 * time.Second, prompts: map[string]*cachedPrompt{}}
}

// CreateScore sends one score (POST /api/public/scores). Call it off the request path: Scores queues them.
func (c *Client) CreateScore(ctx context.Context, score Score) error {
	body, err := json.Marshal(score)
	if err != nil {
		return err
	}
	req, err := http.NewRequestWithContext(ctx, http.MethodPost, c.base+"/api/public/scores", bytes.NewReader(body))
	if err != nil {
		return err
	}
	req.Header.Set("Content-Type", "application/json")
	return c.do(req, nil)
}

// Prompt returns a text prompt by label without ever holding a request up for long:
//   - a fresh copy from the cache;
//   - a stale copy at once, refreshed in the background;
//   - with no copy, a fetch within 2 s, then the fallback on failure, served without fetching for the next 30 s;
//     requests that arrive while that first fetch runs get the fallback too (warm the cache at start).
//
// A prompt of another type is a fallback too: a prompt name keeps its type, and the API returns whatever type is
// stored.
func (c *Client) Prompt(ctx context.Context, name, label, fallback string) Prompt {
	key := name + "\x00" + label
	fallbackPrompt := Prompt{Text: fallback, Name: name, Fallback: true}
	c.mu.Lock()
	entry := c.prompts[key]
	if entry == nil {
		entry = &cachedPrompt{}
		c.prompts[key] = entry
	}
	now := time.Now()
	if now.Before(entry.next) || (entry.busy && entry.prompt.Version == 0) {
		prompt := entry.prompt
		c.mu.Unlock()
		if prompt.Version == 0 {
			return fallbackPrompt
		}
		return prompt
	}
	if entry.prompt.Version > 0 {
		stale := entry.prompt
		if !entry.busy {
			entry.busy = true
			go c.refresh(context.Background(), key, name, label) // not the request's: it may end first
		}
		c.mu.Unlock()
		return stale
	}
	entry.busy = true
	c.mu.Unlock()
	if prompt, ok := c.refresh(ctx, key, name, label); ok {
		return prompt
	}
	return fallbackPrompt
}

// refresh fetches a prompt within 2 s and records the result; a failure keeps any copy and sets the retry time.
func (c *Client) refresh(ctx context.Context, key, name, label string) (Prompt, bool) {
	prompt, err := c.FetchPrompt(ctx, name, label)
	c.mu.Lock()
	defer c.mu.Unlock()
	entry := c.prompts[key]
	entry.busy = false
	if err != nil {
		entry.next = time.Now().Add(c.retry)
		return entry.prompt, entry.prompt.Version > 0
	}
	entry.prompt, entry.next = prompt, time.Now().Add(c.ttl)
	return prompt, true
}

// FetchPrompt fetches a text prompt once, within 2 s, with no cache: for app code that keeps its own cache. A
// prompt of another type is an error.
func (c *Client) FetchPrompt(ctx context.Context, name, label string) (Prompt, error) {
	ctx, cancel := context.WithTimeout(ctx, 2*time.Second)
	defer cancel()
	address := fmt.Sprintf("%s/api/public/v2/prompts/%s?label=%s", c.base, url.PathEscape(name), url.QueryEscape(label))
	req, err := http.NewRequestWithContext(ctx, http.MethodGet, address, nil)
	if err != nil {
		return Prompt{}, err
	}
	var body struct {
		Version int             `json:"version"`
		Type    string          `json:"type"`
		Prompt  json.RawMessage `json:"prompt"`
		Config  json.RawMessage `json:"config"`
	}
	if err := c.do(req, &body); err != nil {
		return Prompt{}, err
	}
	var text string
	if body.Type != "text" || json.Unmarshal(body.Prompt, &text) != nil || body.Version <= 0 {
		return Prompt{}, errors.New("not a text prompt")
	}
	config := body.Config
	if string(config) == "null" || string(config) == "{}" {
		config = nil
	}
	return Prompt{Text: text, Name: name, Version: body.Version, Config: config}, nil
}

// Scores sends scores off the request path, one at a time, from a bounded queue.
type Scores struct {
	client  *Client
	onError func(error)
	mu      sync.Mutex
	queue   chan Score
	done    chan struct{}
	closed  bool
}

// Scores starts the queue: size scores at most wait (256 when 0), and onError, if set, hears about each failed send
// (rate-limit what it logs; the error carries the status code only).
func (c *Client) Scores(size int, onError func(error)) *Scores {
	if size <= 0 {
		size = 256
	}
	s := &Scores{client: c, onError: onError, queue: make(chan Score, size), done: make(chan struct{})}
	go s.run()
	return s
}

// Send queues a score; false when the queue is full or closed, and the score is dropped.
func (s *Scores) Send(score Score) bool {
	s.mu.Lock()
	defer s.mu.Unlock()
	if s.closed {
		return false
	}
	select {
	case s.queue <- score:
		return true
	default:
		return false
	}
}

// Close stops taking scores and sends the queued ones within ctx's deadline (the shutdown grace period).
func (s *Scores) Close(ctx context.Context) error {
	s.mu.Lock()
	if !s.closed {
		s.closed = true
		close(s.queue)
	}
	s.mu.Unlock()
	select {
	case <-s.done:
		return nil
	case <-ctx.Done():
		return ctx.Err()
	}
}

func (s *Scores) run() {
	defer close(s.done)
	for score := range s.queue {
		ctx, cancel := context.WithTimeout(context.Background(), s.client.timeout)
		if err := s.client.CreateScore(ctx, score); err != nil && s.onError != nil {
			s.onError(err)
		}
		cancel()
	}
}

func (c *Client) do(req *http.Request, out any) error {
	req.Header.Set("Authorization", c.auth)
	resp, err := c.http.Do(req)
	if err != nil {
		var urlErr *url.Error
		if errors.As(err, &urlErr) {
			err = urlErr.Err // without the URL and its query
		}
		return fmt.Errorf("langfuse %s %s: %w", req.Method, req.URL.Path, err)
	}
	defer resp.Body.Close()
	if resp.StatusCode >= 300 {
		_, _ = io.Copy(io.Discard, io.LimitReader(resp.Body, 1<<16))
		return fmt.Errorf("langfuse %s %s: status %d", req.Method, req.URL.Path, resp.StatusCode)
	}
	if out == nil {
		return nil
	}
	return json.NewDecoder(resp.Body).Decode(out)
}
