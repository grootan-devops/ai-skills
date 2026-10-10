// Template from the observability-builder skill: copy into the observability module and set the package.
// Needs io.pyroscope:agent 2.x (JFR format) and the jdk.jfr module at runtime (a full JDK or a jlink image
// that includes it). Wire it as the agent's exporter: QueuedExporter for the intervals, a direct instance for
// the last one (references/sdk-notes.md, Java).
package observability;

import java.io.ByteArrayOutputStream;
import java.io.IOException;
import java.net.URI;
import java.net.URLEncoder;
import java.net.http.HttpClient;
import java.net.http.HttpRequest;
import java.net.http.HttpResponse;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.time.Duration;
import java.util.ArrayList;
import java.util.EnumMap;
import java.util.HashMap;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.stream.Collectors;
import java.util.zip.GZIPOutputStream;

import io.pyroscope.javaagent.Snapshot;
import io.pyroscope.javaagent.api.Exporter;
import io.pyroscope.javaagent.api.Logger;
import io.pyroscope.labels.pb.JfrLabels;
import jdk.jfr.consumer.RecordedEvent;
import jdk.jfr.consumer.RecordedFrame;
import jdk.jfr.consumer.RecordedStackTrace;
import jdk.jfr.consumer.RecordingFile;

/**
 * Uploads each profiling interval as pprof to the Pyroscope ingest API, one upload per event type: CPU, allocated
 * bytes and lock wait time. Some backends (OneUptime) reject JFR and keep one sample type per pprof upload, so the
 * agent's JFR snapshot is converted in-process with the JDK's own JFR reader. Samples keep their labels: the agent's
 * context labels ({@code controller}), and {@code span_id} and {@code trace_id} when the agent recorded them.
 */
final class PprofExporter implements Exporter {

    private enum Kind {
        CPU("cpu", "nanoseconds"), ALLOC("alloc_space", "bytes"), LOCK("delay", "nanoseconds");

        final String type;
        final String unit;

        Kind(String type, String unit) {
            this.type = type;
            this.unit = unit;
        }
    }

    private final HttpClient client;
    private final String ingestUrl;
    private final String name;
    private final Map<String, String> headers;
    private final Duration timeout;
    private final long cpuPeriodNanos;
    private final Logger logger;

    PprofExporter(String endpoint, String application, Map<String, String> labels, Map<String, String> headers,
            Duration timeout, int sampleRate, Logger logger) {
        this.client = HttpClient.newBuilder().connectTimeout(timeout).build();
        this.ingestUrl = endpoint.replaceAll("/+$", "") + "/ingest";
        this.name = application + labels.entrySet().stream()
                .map(e -> e.getKey() + "=" + e.getValue())
                .collect(Collectors.joining(",", "{", "}"));
        this.headers = Map.copyOf(headers);
        this.timeout = timeout;
        this.cpuPeriodNanos = 1_000_000_000L / Math.max(1, sampleRate);
        this.logger = logger;
    }

    @Override
    public void export(Snapshot snapshot) {
        Map<Kind, Profile> profiles;
        try {
            profiles = convert(snapshot);
        } catch (IOException | RuntimeException ex) {
            logger.log(Logger.Level.WARN, "profile conversion failed: %s", ex.getClass().getName());
            return;
        }
        for (Profile profile : profiles.values()) {
            try {
                upload(profile.encode(snapshot), snapshot);
            } catch (IOException ex) {
                logger.log(Logger.Level.WARN, "profile upload failed: %s", ex.getClass().getName());
            } catch (InterruptedException ex) {
                Thread.currentThread().interrupt();
                return;
            }
        }
    }

    @Override
    public void stop() {
    }

    private Map<Kind, Profile> convert(Snapshot snapshot) throws IOException {
        Map<Kind, Profile> profiles = new EnumMap<>(Kind.class);
        Path file = Files.createTempFile("profile", ".jfr");
        try {
            Files.write(file, snapshot.data);
            try (RecordingFile recording = new RecordingFile(file)) {
                while (recording.hasMoreEvents()) {
                    RecordedEvent event = recording.readEvent();
                    Kind kind = kindOf(event.getEventType().getName());
                    RecordedStackTrace stack = event.getStackTrace();
                    if (kind == null || stack == null || stack.getFrames().isEmpty()) {
                        continue;
                    }
                    long value = switch (kind) {
                        case CPU -> cpuPeriodNanos;
                        case ALLOC -> event.hasField("tlabSize") ? event.getLong("tlabSize") : event.getLong("allocationSize");
                        case LOCK -> event.getDuration().toNanos();
                    };
                    if (value > 0) {
                        profiles.computeIfAbsent(kind, Profile::new).add(stack, value, labels(event, snapshot.labels));
                    }
                }
            }
        } finally {
            Files.deleteIfExists(file);
        }
        return profiles;
    }

    private static Kind kindOf(String event) {
        return switch (event) {
            case "jdk.ExecutionSample" -> Kind.CPU;
            case "jdk.ObjectAllocationInNewTLAB", "jdk.ObjectAllocationOutsideTLAB" -> Kind.ALLOC;
            case "jdk.JavaMonitorEnter", "jdk.ThreadPark" -> Kind.LOCK;
            default -> null;
        };
    }

    /** The context labels the agent recorded for this sample, plus span_id and trace_id when present. */
    private static Map<String, String> labels(RecordedEvent event, JfrLabels.LabelsSnapshot snapshot) {
        Map<String, String> labels = new LinkedHashMap<>();
        if (snapshot != null && event.hasField("contextId")) {
            JfrLabels.Context context = snapshot.getContextsMap().get(event.getLong("contextId"));
            if (context != null) {
                context.getLabelsMap().forEach((key, value) -> labels.put(
                        snapshot.getStringsOrDefault(key, ""), snapshot.getStringsOrDefault(value, "")));
            }
        }
        if (event.hasField("spanId") && event.getLong("spanId") != 0) {
            labels.put("span_id", String.format("%016x", event.getLong("spanId")));
        }
        if (event.hasField("traceIdHi") && (event.getLong("traceIdHi") != 0 || event.getLong("traceIdLo") != 0)) {
            labels.put("trace_id", String.format("%016x%016x", event.getLong("traceIdHi"), event.getLong("traceIdLo")));
        }
        labels.remove("");
        return labels;
    }

    private void upload(byte[] pprof, Snapshot snapshot) throws IOException, InterruptedException {
        String query = "name=" + URLEncoder.encode(name, StandardCharsets.UTF_8)
                + "&from=" + snapshot.started.getEpochSecond()
                + "&until=" + snapshot.ended.getEpochSecond()
                + "&format=pprof&spyName=javaspy";
        HttpRequest.Builder request = HttpRequest.newBuilder(URI.create(ingestUrl + "?" + query))
                .timeout(timeout)
                .header("Content-Type", "application/octet-stream")
                .POST(HttpRequest.BodyPublishers.ofByteArray(pprof));
        headers.forEach(request::header);
        HttpResponse<Void> response = client.send(request.build(), HttpResponse.BodyHandlers.discarding());
        if (response.statusCode() / 100 != 2) {
            logger.log(Logger.Level.WARN, "profile upload rejected: HTTP %d", response.statusCode());
        }
    }

    /** One pprof profile of a single sample type, built from JFR stacks (profile.proto, gzipped on encode). */
    private final class Profile {
        private final Kind kind;
        private final List<String> strings = new ArrayList<>(List.of(""));
        private final Map<String, Long> stringIds = new HashMap<>(Map.of("", 0L));
        private final Map<String, Long> functions = new LinkedHashMap<>();
        private final Map<String, long[]> locations = new LinkedHashMap<>();   // "functionId:line" -> {id, function, line}
        private final Map<String, Sample> samples = new LinkedHashMap<>();

        private record Sample(long[] locations, Map<String, String> labels, long[] value) {
        }

        Profile(Kind kind) {
            this.kind = kind;
        }

        void add(RecordedStackTrace stack, long value, Map<String, String> labels) {
            List<RecordedFrame> frames = stack.getFrames();
            long[] ids = new long[frames.size()];
            for (int i = 0; i < frames.size(); i++) {   // leaf first, as pprof expects
                RecordedFrame frame = frames.get(i);
                String function = frame.getMethod().getType().getName() + "." + frame.getMethod().getName();
                long functionId = functions.computeIfAbsent(function, f -> (long) functions.size() + 1);
                int line = Math.max(frame.getLineNumber(), 0);
                ids[i] = locations.computeIfAbsent(functionId + ":" + line,
                        k -> new long[] {locations.size() + 1, functionId, line})[0];
            }
            String key = java.util.Arrays.toString(ids) + labels;
            samples.computeIfAbsent(key, k -> new Sample(ids, labels, new long[1])).value()[0] += value;
        }

        byte[] encode(Snapshot snapshot) throws IOException {
            Proto profile = new Proto();
            profile.message(1, new Proto().varint(1, string(kind.type)).varint(2, string(kind.unit)));
            for (Sample sample : samples.values()) {
                Proto s = new Proto().packed(1, sample.locations()).packed(2, sample.value());
                sample.labels().forEach((k, v) -> s.message(3, new Proto().varint(1, string(k)).varint(2, string(v))));
                profile.message(2, s);
            }
            for (long[] location : locations.values()) {
                profile.message(4, new Proto().varint(1, location[0])
                        .message(4, new Proto().varint(1, location[1]).varint(2, location[2])));
            }
            for (Map.Entry<String, Long> function : functions.entrySet()) {
                long nameId = string(function.getKey());
                profile.message(5, new Proto().varint(1, function.getValue()).varint(2, nameId).varint(3, nameId));
            }
            long start = snapshot.started.getEpochSecond() * 1_000_000_000L + snapshot.started.getNano();
            long end = snapshot.ended.getEpochSecond() * 1_000_000_000L + snapshot.ended.getNano();
            profile.varint(9, start).varint(10, Math.max(0, end - start));
            profile.message(11, new Proto().varint(1, string(kind.type)).varint(2, string(kind.unit)));
            if (kind == Kind.CPU) {
                profile.varint(12, cpuPeriodNanos);
            }
            for (String s : strings) {   // last, after every string has an id
                profile.bytes(6, s.getBytes(StandardCharsets.UTF_8));
            }
            ByteArrayOutputStream gzipped = new ByteArrayOutputStream();
            try (GZIPOutputStream gzip = new GZIPOutputStream(gzipped)) {
                gzip.write(profile.toByteArray());
            }
            return gzipped.toByteArray();
        }

        private long string(String s) {
            return stringIds.computeIfAbsent(s, k -> {
                strings.add(k);
                return (long) strings.size() - 1;
            });
        }
    }

    /** A minimal protobuf writer: varints, packed varints, bytes and nested messages. */
    private static final class Proto {
        private final ByteArrayOutputStream out = new ByteArrayOutputStream();

        Proto varint(int field, long value) {
            tag(field, 0);
            raw(value);
            return this;
        }

        Proto packed(int field, long[] values) {
            Proto body = new Proto();
            for (long v : values) {
                body.raw(v);
            }
            return bytes(field, body.toByteArray());
        }

        Proto message(int field, Proto message) {
            return bytes(field, message.toByteArray());
        }

        Proto bytes(int field, byte[] value) {
            tag(field, 2);
            raw(value.length);
            out.writeBytes(value);
            return this;
        }

        byte[] toByteArray() {
            return out.toByteArray();
        }

        private void tag(int field, int wireType) {
            raw(((long) field << 3) | wireType);
        }

        private void raw(long value) {
            while ((value & ~0x7FL) != 0) {
                out.write((int) ((value & 0x7F) | 0x80));
                value >>>= 7;
            }
            out.write((int) value);
        }
    }
}
