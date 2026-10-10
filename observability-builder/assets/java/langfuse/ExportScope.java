// Template from the observability-builder skill: copy into the observability module and set the package.
// The export scope for Langfuse (references/signals/genai.md, "Export scope").

package observability.langfuse;

import io.opentelemetry.api.common.AttributeKey;
import io.opentelemetry.api.common.Attributes;
import io.opentelemetry.api.trace.SpanKind;
import io.opentelemetry.sdk.trace.data.SpanData;
import java.util.Set;

/**
 * Decided when the span has ended: only the spans of an AI feature or an experiment item go to Langfuse; ai_context
 * leaves out non-AI client and producer spans (databases, HTTP, queues), ai_only keeps AI spans and experiment item
 * roots.
 */
public final class ExportScope {
    private static final Set<String> STAMPS = Set.of("langfuse.trace.name", "langfuse.experiment.id");
    private static final Set<String> AI_KEYS =
            Set.of("gen_ai.operation.name", "langfuse.observation.type", "openinference.span.kind");

    private ExportScope() {}

    public static boolean shouldExport(SpanData span, String scope) {
        Attributes attributes = span.getAttributes();
        if (!has(attributes, STAMPS)) {
            return false;
        }
        boolean ai = isAiSpan(attributes);
        if ("ai_only".equals(scope)) {
            return ai || has(attributes, Set.of("langfuse.experiment.id"));
        }
        return ai || (span.getKind() != SpanKind.CLIENT && span.getKind() != SpanKind.PRODUCER);
    }

    /** A span that describes AI work: a GenAI operation, a Langfuse observation type or an OpenInference span kind. */
    public static boolean isAiSpan(Attributes attributes) {
        return has(attributes, AI_KEYS);
    }

    static boolean has(Attributes attributes, Set<String> names) {
        for (AttributeKey<?> key : attributes.asMap().keySet()) {
            if (names.contains(key.getKey())) {
                return true;
            }
        }
        return false;
    }

    static String text(Attributes attributes, String name) {
        for (var entry : attributes.asMap().entrySet()) {
            if (entry.getKey().getKey().equals(name)) {
                return String.valueOf(entry.getValue());
            }
        }
        return null;
    }
}
