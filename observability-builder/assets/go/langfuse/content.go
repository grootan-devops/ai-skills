package langfuse

import (
	"bytes"
	"encoding/json"
	"regexp"
	"slices"
	"strings"
	"unicode/utf8"

	"go.opentelemetry.io/otel/attribute"
	"go.opentelemetry.io/otel/trace"
)

// StatusMessageKey is the observation status message, which Langfuse shows instead of the span's status description.
const StatusMessageKey = attribute.Key("langfuse.observation.status_message")

// The content keys: prompts, completions, tool arguments and results, retrieved documents and other model input and
// output (references/signals/genai.md, "Content keys"); one list for the module, the receiver and the collector.
var contentExact = map[string]bool{
	"gen_ai.input.messages": true, "gen_ai.output.messages": true, "gen_ai.system_instructions": true,
	"gen_ai.tool.definitions": true, "gen_ai.tool.call.arguments": true, "gen_ai.tool.call.result": true,
	"gen_ai.retrieval.documents": true, "gen_ai.retrieval.query.text": true, "gen_ai.memory.query.text": true,
	"gen_ai.memory.records": true, "gen_ai.evaluation.explanation": true, "gen_ai.prompt": true, "gen_ai.completion": true,
	"langfuse.observation.input": true, "langfuse.observation.output": true, "langfuse.trace.input": true,
	"langfuse.trace.output": true, "langfuse.experiment.item.expected_output": true,
	"traceloop.entity.input": true, "traceloop.entity.output": true,
	"input.value": true, "output.value": true, "llm.prompt_template.variables": true,
	"ai.response.text": true, "ai.response.object": true, "ai.response.toolCalls": true, "ai.toolCall.args": true,
	"ai.toolCall.result": true,
}

var contentPattern = regexp.MustCompile(`^(gen_ai\.prompt\.\d+\.|gen_ai\.completion\.|gen_ai\.prompt\.variable|` +
	`llm\.(input|output)_messages\.|llm\.prompts\.|llm\.tools\.|llm\.prompt_template\.template|retrieval\.documents\.|` +
	`reranker\.|embedding\.embeddings\.|ai\.prompt)`)

// IsContentKey reports an attribute that carries model input or output.
func IsContentKey(key attribute.Key) bool {
	return contentExact[string(key)] || contentPattern.MatchString(string(key))
}

// FailedStatus is the status message of a failed AI span where content isn't allowed: its error type, never the
// provider's message, which can quote the input.
func FailedStatus(set attribute.Set) (attribute.KeyValue, bool) {
	if !IsAISpan(set) {
		return attribute.KeyValue{}, false
	}
	errorType, typed := set.Value("error.type")
	level, _ := set.Value("langfuse.observation.level")
	if !typed && !strings.EqualFold(level.AsString(), "ERROR") {
		return attribute.KeyValue{}, false
	}
	message := "error"
	if typed {
		message = errorType.Emit()
	}
	if len(message) > 128 {
		message = message[:128]
	}
	return StatusMessageKey.String(message), true
}

// Decide says whether a span's Langfuse copy may hold content, and with which redactor (nil: none). The module
// answers from the feature's content mode and the trace's content sample (an experiment item: whenever the ceiling
// is full).
type Decide func(set attribute.Set, traceID trace.TraceID) (allowed bool, redact func(string) string)

// Guard decides what the Langfuse copy of a span keeps: the keys to drop, the keys to set, and whether it may hold
// content.
type Guard func(attrs []attribute.KeyValue, traceID trace.TraceID) (drop []attribute.Key, set []attribute.KeyValue, allowed bool)

// NewGuard is the content guard: where content isn't allowed, every content key goes and a failed AI span's status
// message becomes its error type; where it is, string values are redacted (when the feature is in redacted mode)
// and capped at maxChars, a JSON list keeping its newest items. The retriever's query and documents become its
// input and output.
func NewGuard(decide Decide, maxChars int) Guard {
	return func(attrs []attribute.KeyValue, traceID trace.TraceID) ([]attribute.Key, []attribute.KeyValue, bool) {
		set := attribute.NewSet(slices.Clone(attrs)...) // a clone: attrs is the span's own, shared slice
		var content []attribute.KeyValue
		for _, kv := range attrs {
			if IsContentKey(kv.Key) {
				content = append(content, kv)
			}
		}
		failed, isFailed := FailedStatus(set)
		allowed, redact := decide(set, traceID)
		if !allowed {
			drop := make([]attribute.Key, 0, len(content))
			for _, kv := range content {
				drop = append(drop, kv.Key)
			}
			var updates []attribute.KeyValue
			if isFailed {
				updates = append(updates, failed)
			}
			return drop, updates, false
		}
		var updates []attribute.KeyValue
		if redact != nil && isFailed {
			updates = append(updates, failed)
		}
		retriever := set.HasValue("langfuse.observation.type") && func() bool {
			value, _ := set.Value("langfuse.observation.type")
			return value.AsString() == "retriever"
		}()
		for _, kv := range content {
			if kv.Value.Type() != attribute.STRING {
				continue
			}
			text := kv.Value.AsString()
			if redact != nil {
				text = redact(text)
			}
			text = Cap(text, maxChars)
			updates = append(updates, attribute.String(string(kv.Key), text))
			if retriever && kv.Key == "gen_ai.retrieval.query.text" {
				updates = append(updates, attribute.String("langfuse.observation.input", text))
			}
			if retriever && kv.Key == "gen_ai.retrieval.documents" {
				updates = append(updates, attribute.String("langfuse.observation.output", text))
			}
		}
		return nil, updates, true
	}
}

// Cap shortens text to at most limit characters (not bytes); a JSON list keeps its newest items and stays valid JSON.
func Cap(text string, limit int) string {
	if limit <= 0 || utf8.RuneCountInString(text) <= limit {
		return text
	}
	var items []json.RawMessage // each item as written: keys keep their order
	if err := json.Unmarshal([]byte(text), &items); err == nil {
		for len(items) > 0 {
			if encoded := encodeList(items); utf8.RuneCountInString(encoded) <= limit {
				return encoded
			}
			items = items[1:]
		}
	}
	return string([]rune(text)[:limit-1]) + "…"
}

// encodeList writes the items back without escaping <, > and &, which json.Marshal would turn into \u003c and the like.
func encodeList(items []json.RawMessage) string {
	var buf bytes.Buffer
	encoder := json.NewEncoder(&buf)
	encoder.SetEscapeHTML(false)
	_ = encoder.Encode(items)
	return strings.TrimSuffix(buf.String(), "\n")
}
