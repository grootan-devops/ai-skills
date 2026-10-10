// Template from the observability-builder skill: copy into the observability module and set the package.
// The content guard for the Langfuse copy of a span (references/signals/genai.md, "Content").

package observability.langfuse;

import com.fasterxml.jackson.databind.DeserializationFeature;
import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;
import com.fasterxml.jackson.databind.node.ArrayNode;
import io.opentelemetry.api.common.Attributes;
import java.util.LinkedHashMap;
import java.util.LinkedHashSet;
import java.util.Map;
import java.util.Set;
import java.util.function.UnaryOperator;
import java.util.regex.Pattern;

/** What the Langfuse copy of a span keeps: the keys to drop, the keys to set, and whether it may hold content. */
public interface ContentGuard {
    String STATUS_MESSAGE = "langfuse.observation.status_message";

    Result apply(Attributes attributes, String traceId);

    record Result(Set<String> drop, Map<String, String> set, boolean allowed) {}

    /** Whether a span's copy may hold content, and with which redactor (null: none), from the feature's mode. */
    interface Decide {
        Decision decide(Attributes attributes, String traceId);
    }

    record Decision(boolean allowed, UnaryOperator<String> redact) {}

    Set<String> CONTENT_KEYS = Set.of(
            "gen_ai.input.messages", "gen_ai.output.messages", "gen_ai.system_instructions", "gen_ai.tool.definitions",
            "gen_ai.tool.call.arguments", "gen_ai.tool.call.result", "gen_ai.retrieval.documents",
            "gen_ai.retrieval.query.text", "gen_ai.memory.query.text", "gen_ai.memory.records",
            "gen_ai.evaluation.explanation", "gen_ai.prompt", "gen_ai.completion", "langfuse.observation.input",
            "langfuse.observation.output", "langfuse.trace.input", "langfuse.trace.output",
            "langfuse.experiment.item.expected_output", "traceloop.entity.input", "traceloop.entity.output",
            "input.value", "output.value", "llm.prompt_template.variables", "ai.response.text", "ai.response.object",
            "ai.response.toolCalls", "ai.toolCall.args", "ai.toolCall.result");
    Pattern CONTENT_PATTERN = Pattern.compile("^(gen_ai\\.prompt\\.\\d+\\.|gen_ai\\.completion\\.|gen_ai\\.prompt\\.variable|"
            + "llm\\.(input|output)_messages\\.|llm\\.prompts\\.|llm\\.tools\\.|llm\\.prompt_template\\.template|"
            + "retrieval\\.documents\\.|reranker\\.|embedding\\.embeddings\\.|ai\\.prompt)");

    static boolean isContentKey(String key) {
        return CONTENT_KEYS.contains(key) || CONTENT_PATTERN.matcher(key).find();
    }

    /**
     * Where content isn't allowed, every content key goes and a failed AI span's status message becomes its error
     * type; where it is, string values are redacted (a feature in redacted mode) and capped at maxChars, a JSON list
     * keeping its newest items. The retriever's query and documents become its input and output.
     */
    static ContentGuard of(Decide decide, int maxChars) {
        return (attributes, traceId) -> {
            Map<String, String> content = new LinkedHashMap<>();
            attributes.forEach((key, value) -> {
                if (isContentKey(key.getKey())) {
                    content.put(key.getKey(), value instanceof String text ? text : null);
                }
            });
            String failed = failedStatus(attributes);
            Decision decision = decide.decide(attributes, traceId);
            Map<String, String> set = new LinkedHashMap<>();
            if (!decision.allowed()) {
                if (failed != null) {
                    set.put(STATUS_MESSAGE, failed);
                }
                return new Result(new LinkedHashSet<>(content.keySet()), set, false);
            }
            if (decision.redact() != null && failed != null) {
                set.put(STATUS_MESSAGE, failed);
            }
            boolean retriever = "retriever".equals(ExportScope.text(attributes, "langfuse.observation.type"));
            content.forEach((key, text) -> {
                if (text == null) {
                    return;
                }
                String value = cap(decision.redact() != null ? decision.redact().apply(text) : text, maxChars);
                set.put(key, value);
                if (retriever && key.equals("gen_ai.retrieval.query.text")) {
                    set.put("langfuse.observation.input", value);
                }
                if (retriever && key.equals("gen_ai.retrieval.documents")) {
                    set.put("langfuse.observation.output", value);
                }
            });
            return new Result(Set.of(), set, true);
        };
    }

    /** A failed AI span's status message: its error type, never the provider's message, which can quote the input. */
    static String failedStatus(Attributes attributes) {
        if (!ExportScope.isAiSpan(attributes)) {
            return null;
        }
        String type = ExportScope.text(attributes, "error.type");
        String level = ExportScope.text(attributes, "langfuse.observation.level");
        if (type == null && !"ERROR".equalsIgnoreCase(level)) {
            return null;
        }
        String message = type != null ? type : "error";
        return message.length() > 128 ? message.substring(0, 128) : message;
    }

    /** At most limit characters (code points); a JSON list keeps its newest items and stays valid JSON. */
    static String cap(String text, int limit) {
        if (limit <= 0 || text.codePointCount(0, text.length()) <= limit) {
            return text;
        }
        try {
            JsonNode node = Json.MAPPER.readTree(text);
            if (node instanceof ArrayNode items) {
                while (!items.isEmpty()) {
                    String encoded = Json.MAPPER.writeValueAsString(items);
                    if (encoded.codePointCount(0, encoded.length()) <= limit) {
                        return encoded;
                    }
                    items.remove(0);
                }
            }
        } catch (Exception notJson) {
            // plain text: cut below
        }
        return text.substring(0, text.offsetByCodePoints(0, limit - 1)) + "…";
    }

    /** The shared JSON mapper; text after a JSON value makes it plain text. */
    final class Json {
        static final ObjectMapper MAPPER =
                new ObjectMapper().enable(DeserializationFeature.FAIL_ON_TRAILING_TOKENS);

        private Json() {}
    }
}
