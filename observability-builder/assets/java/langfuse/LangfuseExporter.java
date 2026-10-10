// Template from the observability-builder skill: copy into the observability module and set the package.
// Sends a service's AI spans to Langfuse where no Langfuse SDK exists (references/backends/langfuse.md, "App route").

package observability.langfuse;

import io.opentelemetry.api.common.Attributes;
import io.opentelemetry.api.common.AttributesBuilder;
import io.opentelemetry.api.trace.StatusCode;
import io.opentelemetry.exporter.otlp.http.trace.OtlpHttpSpanExporter;
import io.opentelemetry.sdk.common.CompletableResultCode;
import io.opentelemetry.sdk.trace.data.EventData;
import io.opentelemetry.sdk.trace.data.SpanData;
import io.opentelemetry.sdk.trace.data.StatusData;
import io.opentelemetry.sdk.trace.export.SpanExporter;
import java.util.ArrayList;
import java.util.Collection;
import java.util.HashSet;
import java.util.List;
import java.util.Map;
import java.util.Set;
import java.util.function.Function;

/**
 * The spans the export scope selects, each through the content guard, over OTLP/HTTP to
 * {base_url}/api/public/otel/v1/traces. Add it to the module's tracer provider in a batch span processor (modes
 * hybrid and otel, export app); the ops exporter stays as it is.
 */
public final class LangfuseExporter implements SpanExporter {
    /** Langfuse's OTLP traces path; Langfuse takes OTLP over HTTP only. */
    public static final String TRACES_PATH = "/api/public/otel/v1/traces";

    private final SpanExporter next;
    private final String scope;
    private final ContentGuard guard;
    private Function<SpanData, Attributes> translate;

    LangfuseExporter(SpanExporter next, String scope, ContentGuard guard) {
        this.next = next;
        this.scope = scope;
        this.guard = guard;
    }

    /** Basic auth from the keys, the ingestion header (spans show up at once in Langfuse 4) and gzip. */
    public static LangfuseExporter create(LangfuseConfig config, ContentGuard guard) {
        SpanExporter next = OtlpHttpSpanExporter.builder()
                .setEndpoint(config.baseUrl() + TRACES_PATH)
                .addHeader("Authorization", "Basic " + config.basic())
                .addHeader("x-langfuse-ingestion-version", "4")
                .setCompression("gzip")
                .setTimeout(config.timeout())
                .build();
        return new LangfuseExporter(next, config.scope(), guard);
    }

    /**
     * The Langfuse-only keys for each Langfuse copy, from the module's translation table (the prompt link with its
     * integer version, the completion start time), so they never sit on the span the ops exporter shares. Runs after
     * the guard; its keys win over the span's own. Set it before the exporter is in use.
     */
    public LangfuseExporter withTranslation(Function<SpanData, Attributes> translate) {
        this.translate = translate;
        return this;
    }

    @Override
    public CompletableResultCode export(Collection<SpanData> spans) {
        List<SpanData> kept = new ArrayList<>(spans.size());
        for (SpanData span : spans) {
            if (!ExportScope.shouldExport(span, scope)) {
                continue;
            }
            SpanData copy = guarded(span);
            if (copy != null) {
                kept.add(copy);
            }
        }
        return kept.isEmpty() ? CompletableResultCode.ofSuccess() : next.export(kept);
    }

    @Override
    public CompletableResultCode flush() {
        return next.flush();
    }

    @Override
    public CompletableResultCode shutdown() {
        return next.shutdown();
    }

    /** The span's Langfuse copy; a guard that throws keeps the span off the Langfuse path (fail closed). */
    SpanData guarded(SpanData span) {
        if (guard == null && translate == null) {
            return span;
        }
        try {
            ContentGuard.Result result = guard != null
                    ? guard.apply(span.getAttributes(), span.getTraceId())
                    : new ContentGuard.Result(Set.of(), Map.of(), true);
            Attributes extra = translate != null ? translate.apply(span) : Attributes.empty();
            List<EventData> events = span.getEvents();
            if (!result.allowed()) {
                events = events.stream().filter(event -> !event.getName().startsWith("gen_ai.")).toList();
            }
            if (result.drop().isEmpty() && result.set().isEmpty() && extra.isEmpty()
                    && events.size() == span.getEvents().size()) {
                return span;
            }
            StatusData status = span.getStatus();
            String message = result.set().get(ContentGuard.STATUS_MESSAGE);
            if (!result.allowed() && message != null && status.getStatusCode() == StatusCode.ERROR) {
                status = StatusData.create(StatusCode.ERROR, message);
            }
            Set<String> replaced = new HashSet<>();
            extra.forEach((key, value) -> replaced.add(key.getKey()));
            AttributesBuilder attributes = span.getAttributes().toBuilder();
            attributes.removeIf(key -> result.drop().contains(key.getKey()) || result.set().containsKey(key.getKey())
                    || replaced.contains(key.getKey()));
            result.set().forEach(attributes::put);
            attributes.putAll(extra);
            return new GuardedSpanData(span, attributes.build(), status, events);
        } catch (RuntimeException failed) {
            return null;
        }
    }
}
