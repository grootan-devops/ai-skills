// Template from the observability-builder skill: copy into the observability module and set the package.

package observability.langfuse;

import io.opentelemetry.api.common.Attributes;
import io.opentelemetry.sdk.trace.data.DelegatingSpanData;
import io.opentelemetry.sdk.trace.data.EventData;
import io.opentelemetry.sdk.trace.data.SpanData;
import io.opentelemetry.sdk.trace.data.StatusData;
import java.util.List;

/** A span's Langfuse copy: its own attributes, status and events, everything else from the original. */
final class GuardedSpanData extends DelegatingSpanData {
    private final SpanData original;
    private final Attributes attributes;
    private final StatusData status;
    private final List<EventData> events;

    GuardedSpanData(SpanData original, Attributes attributes, StatusData status, List<EventData> events) {
        super(original);
        this.original = original;
        this.attributes = attributes;
        this.status = status;
        this.events = events;
    }

    @Override
    public Attributes getAttributes() {
        return attributes;
    }

    @Override
    public StatusData getStatus() {
        return status;
    }

    @Override
    public List<EventData> getEvents() {
        return events;
    }

    // What the original dropped at its limits stays counted; what the guard removed isn't reported as dropped.
    @Override
    public int getTotalAttributeCount() {
        return original.getTotalAttributeCount() - original.getAttributes().size() + attributes.size();
    }

    @Override
    public int getTotalRecordedEvents() {
        return original.getTotalRecordedEvents() - original.getEvents().size() + events.size();
    }
}
