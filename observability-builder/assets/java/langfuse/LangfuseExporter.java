// Template from the observability-builder skill: copy into the observability module and set the package.
// Sends a service's AI spans to Langfuse where no Langfuse SDK exists (references/backends/langfuse.md, "App route").

package observability.langfuse;

import io.opentelemetry.api.common.AttributeKey;
import io.opentelemetry.api.common.Attributes;
import io.opentelemetry.api.common.AttributesBuilder;
import io.opentelemetry.api.trace.StatusCode;
import io.opentelemetry.exporter.otlp.http.trace.OtlpHttpSpanExporter;
import io.opentelemetry.sdk.common.CompletableResultCode;
import io.opentelemetry.sdk.trace.data.EventData;
import io.opentelemetry.sdk.trace.data.LinkData;
import io.opentelemetry.sdk.trace.data.SpanData;
import io.opentelemetry.sdk.trace.data.StatusData;
import io.opentelemetry.sdk.trace.export.SpanExporter;
import java.nio.charset.StandardCharsets;
import java.util.ArrayList;
import java.util.Collection;
import java.util.HashSet;
import java.util.List;
import java.util.Map;
import java.util.Set;
import java.util.concurrent.atomic.AtomicLong;
import java.util.function.Function;
import java.util.logging.Level;
import java.util.logging.Logger;

/**
 * The spans the export scope selects, each through the content guard, over OTLP/HTTP to
 * {base_url}/api/public/otel/v1/traces. Add it to the module's tracer provider in a batch span processor (modes
 * hybrid and otel, export app); the ops exporter stays as it is.
 */
public final class LangfuseExporter implements SpanExporter {
    /** Langfuse's OTLP traces path; Langfuse takes OTLP over HTTP only. */
    public static final String TRACES_PATH = "/api/public/otel/v1/traces";

    private static final Logger LOG = Logger.getLogger(LangfuseExporter.class.getName());

    private final SpanExporter next;
    private final String scope;
    private final ContentGuard guard;
    private final int maxRequestBytes;
    private final AtomicLong dropped = new AtomicLong();
    private long pendingDrops; // guarded by this
    private long warnedAt; // guarded by this
    private Function<SpanData, Attributes> translate;

    LangfuseExporter(SpanExporter next, String scope, ContentGuard guard) {
        this(next, scope, guard, LangfuseConfig.DEFAULT_MAX_REQUEST_BYTES);
    }

    LangfuseExporter(SpanExporter next, String scope, ContentGuard guard, int maxRequestBytes) {
        this.next = next;
        this.scope = scope;
        this.guard = guard;
        this.maxRequestBytes = maxRequestBytes > 0 ? maxRequestBytes : LangfuseConfig.DEFAULT_MAX_REQUEST_BYTES;
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
        return new LangfuseExporter(next, config.scope(), guard, config.maxRequestBytes());
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
        if (kept.isEmpty()) {
            return CompletableResultCode.ofSuccess();
        }
        List<CompletableResultCode> results = new ArrayList<>();
        for (List<SpanData> part : split(kept)) {
            results.add(next.export(part));
        }
        return results.size() == 1 ? results.get(0) : CompletableResultCode.ofAll(results);
    }

    /** Spans too large for one request, dropped since the exporter started. */
    public long droppedSpans() {
        return dropped.get();
    }

    /**
     * Parts whose estimated OTLP body (before gzip) stays within the cap. The estimate errs high: each span's strings
     * in UTF-8 plus a fixed allowance per span, attribute, event and link, and each part's resource and scope. A span
     * that alone exceeds the cap is dropped and counted; the warning comes at most once a minute.
     */
    List<List<SpanData>> split(List<SpanData> spans) {
        List<List<SpanData>> parts = new ArrayList<>();
        List<SpanData> part = new ArrayList<>();
        Set<Object> envelopes = new HashSet<>(); // resources and scopes already counted in this part
        long size = 0;
        int drops = 0;
        for (SpanData span : spans) {
            long spanSize = estimate(span);
            long envelope = estimate(span.getResource().getAttributes()) + span.getInstrumentationScopeInfo().getName().length() + 64;
            if (spanSize + envelope > maxRequestBytes) {
                drops++;
                continue;
            }
            boolean newEnvelope = !envelopes.contains(span.getResource()) || !envelopes.contains(span.getInstrumentationScopeInfo());
            long extra = spanSize + (newEnvelope ? envelope : 0);
            if (!part.isEmpty() && size + extra > maxRequestBytes) {
                parts.add(part);
                part = new ArrayList<>();
                envelopes.clear();
                size = 0;
                extra = spanSize + envelope;
            }
            envelopes.add(span.getResource());
            envelopes.add(span.getInstrumentationScopeInfo());
            part.add(span);
            size += extra;
        }
        if (!part.isEmpty()) {
            parts.add(part);
        }
        if (drops > 0) {
            warnDropped(drops);
        }
        return parts;
    }

    private synchronized void warnDropped(int count) {
        long total = dropped.addAndGet(count);
        pendingDrops += count;
        long now = System.currentTimeMillis();
        if (now - warnedAt < 60_000) {
            return;
        }
        LOG.log(Level.WARNING, "langfuse traces: dropped {0} span(s) larger than the request size cap ({1} in all)",
                new Object[] {pendingDrops, total});
        warnedAt = now;
        pendingDrops = 0;
    }

    static long estimate(SpanData span) {
        long size = 96 + utf8(span.getName()) + utf8(span.getStatus().getDescription()) + estimate(span.getAttributes());
        for (EventData event : span.getEvents()) {
            size += 32 + utf8(event.getName()) + estimate(event.getAttributes());
        }
        for (LinkData link : span.getLinks()) {
            size += 64 + estimate(link.getAttributes());
        }
        return size;
    }

    static long estimate(Attributes attributes) {
        long[] size = {0};
        attributes.forEach((AttributeKey<?> key, Object value) -> {
            size[0] += 16 + utf8(key.getKey());
            if (value instanceof List<?> list) {
                for (Object item : list) {
                    size[0] += 12 + utf8(String.valueOf(item));
                }
            } else {
                size[0] += utf8(String.valueOf(value));
            }
        });
        return size[0];
    }

    private static long utf8(String text) {
        return text == null ? 0 : text.getBytes(StandardCharsets.UTF_8).length;
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
        try {
            ContentGuard.Result result = guard != null
                    ? guard.apply(span.getAttributes(), span.getTraceId())
                    : new ContentGuard.Result(Set.of(), Map.of(), true);
            Attributes translated = translate != null ? translate.apply(span) : Attributes.empty();
            // Request context and HTTP detail never reach Langfuse, whatever the guard or the translation says.
            Attributes extra = translated.toBuilder()
                    .removeIf(key -> ContentGuard.isRequestContextKey(key.getKey()))
                    .build();
            boolean requestContext = span.getAttributes().asMap().keySet().stream()
                    .anyMatch(key -> ContentGuard.isRequestContextKey(key.getKey()));
            List<EventData> events = span.getEvents();
            if (!result.allowed()) {
                events = events.stream().filter(event -> !event.getName().startsWith("gen_ai.")).toList();
            }
            if (result.drop().isEmpty() && result.set().isEmpty() && extra.isEmpty() && !requestContext
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
                    || replaced.contains(key.getKey()) || ContentGuard.isRequestContextKey(key.getKey()));
            result.set().forEach((key, value) -> {
                if (!ContentGuard.isRequestContextKey(key)) {
                    attributes.put(key, value);
                }
            });
            attributes.putAll(extra);
            return new GuardedSpanData(span, attributes.build(), status, events);
        } catch (RuntimeException failed) {
            return null;
        }
    }
}
