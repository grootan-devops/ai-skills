// Template from the observability-builder skill: copy into the observability module and set the package.
// The process's one Langfuse API client where no Langfuse SDK exists (references/backends/langfuse.md, "Scores").

package observability.langfuse;

import com.fasterxml.jackson.annotation.JsonInclude;
import com.fasterxml.jackson.databind.JsonNode;
import java.io.IOException;
import java.net.URI;
import java.net.URLEncoder;
import java.net.http.HttpClient;
import java.net.http.HttpRequest;
import java.net.http.HttpResponse;
import java.nio.charset.StandardCharsets;
import java.time.Duration;
import java.time.Instant;
import java.util.ArrayList;
import java.util.HashMap;
import java.util.List;
import java.util.Map;
import java.util.concurrent.ArrayBlockingQueue;
import java.util.concurrent.BlockingQueue;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;
import java.util.function.Consumer;

/** The module's scores (mode hybrid), the app's prompts and an experiment runner's datasets. */
public final class LangfuseClient {
    private static final Duration PROMPT_TIMEOUT = Duration.ofSeconds(2);
    private static final Duration TTL = Duration.ofMinutes(1);
    private static final Duration RETRY = Duration.ofSeconds(30);

    /** One score, sent by the server under a stable id, so a repeat replaces it. */
    @JsonInclude(JsonInclude.Include.NON_NULL)
    public record Score(
            String id,
            String name,
            double value,
            String dataType, // NUMERIC, BOOLEAN, CATEGORICAL
            String traceId,
            String observationId,
            String comment, // what a user wrote: only where the feature's content mode allows
            String environment) {}

    /**
     * The prompt app code uses and hands to the model-call helper for the generation's link. config: the version's
     * config (e.g. model parameters), null when it has none; fallback: the text kept in code (WARNING, unlinked).
     */
    public record Prompt(String text, String name, int version, JsonNode config, boolean fallback) {}

    /** A Langfuse dataset with its items, as an experiment runner reads it. */
    public record Dataset(String id, String name, List<DatasetItem> items) {}

    /** One item: its input, expected output and metadata are what the dataset holds (content). */
    public record DatasetItem(String id, String status, JsonNode input, JsonNode expectedOutput, JsonNode metadata) {}

    private static final class Cached { // guarded by the client
        Prompt prompt; // the last good copy, or null
        Instant next = Instant.MIN; // no fetch before this: the copy is fresh, or the last fetch failed
        boolean busy; // a fetch is running
    }

    private final String base;
    private final String auth;
    private final Duration timeout;
    private final HttpClient http;
    private final Map<String, Cached> prompts = new HashMap<>();
    private final ExecutorService refresher = Executors.newSingleThreadExecutor(daemon("langfuse-prompts"));

    public LangfuseClient(LangfuseConfig config) {
        this(config, null);
    }

    /**
     * http: the module's traced client, so a prompt fetch inside a request gets its CLIENT span; null builds one.
     * Either way use HTTP/1.1: on plain http the JDK client's HTTP/2 upgrade request makes a self-hosted Langfuse
     * (Node) close the connection without an answer.
     */
    public LangfuseClient(LangfuseConfig config, HttpClient http) {
        base = config.baseUrl();
        auth = "Basic " + config.basic();
        timeout = config.timeout();
        this.http = http != null ? http : HttpClient.newBuilder()
                .version(HttpClient.Version.HTTP_1_1)
                .connectTimeout(PROMPT_TIMEOUT)
                .build();
    }

    /** Sends one score (POST /api/public/scores). Call it off the request path: {@link #scores} queues them. */
    public void createScore(Score score) throws IOException, InterruptedException {
        send(HttpRequest.newBuilder(URI.create(base + "/api/public/scores"))
                .timeout(timeout)
                .header("Content-Type", "application/json")
                .POST(HttpRequest.BodyPublishers.ofByteArray(ContentGuard.Json.MAPPER.writeValueAsBytes(score)))
                .build());
    }

    /**
     * A text prompt by label, never holding a request up for long: a fresh copy from the cache; a stale copy at
     * once, refreshed in the background; with no copy, a fetch within 2 s, then the fallback on failure, served
     * without fetching for the next 30 s. A prompt of another type is a fallback too: a prompt name keeps its type,
     * and the API returns whatever type is stored.
     */
    public Prompt prompt(String name, String label, String fallback) {
        String key = name + "\0" + label;
        Prompt fallbackPrompt = new Prompt(fallback, name, 0, null, true);
        synchronized (this) {
            Cached entry = prompts.computeIfAbsent(key, k -> new Cached());
            if (Instant.now().isBefore(entry.next) || (entry.busy && entry.prompt == null)) {
                return entry.prompt != null ? entry.prompt : fallbackPrompt;
            }
            if (entry.prompt != null) {
                if (!entry.busy) {
                    entry.busy = true;
                    refresher.execute(() -> refresh(key, name, label));
                }
                return entry.prompt;
            }
            entry.busy = true;
        }
        Prompt fetched = refresh(key, name, label);
        return fetched != null ? fetched : fallbackPrompt;
    }

    private Prompt refresh(String key, String name, String label) {
        Prompt fetched = null;
        try {
            fetched = fetchPrompt(name, label);
        } catch (InterruptedException interrupted) {
            Thread.currentThread().interrupt();
        } catch (IOException | RuntimeException failed) {
            // the stale copy, else the fallback, until the retry time
        }
        synchronized (this) {
            Cached entry = prompts.get(key);
            entry.busy = false;
            if (fetched == null) {
                entry.next = Instant.now().plus(RETRY);
                return entry.prompt;
            }
            entry.prompt = fetched;
            entry.next = Instant.now().plus(TTL);
            return fetched;
        }
    }

    /** A text prompt fetched once, within 2 s, with no cache: for app code that keeps its own. Another type throws. */
    public Prompt fetchPrompt(String name, String label) throws IOException, InterruptedException {
        JsonNode body = ContentGuard.Json.MAPPER.readTree(send(get(
                "/api/public/v2/prompts/" + path(name) + "?label=" + query(label), PROMPT_TIMEOUT)));
        int version = body.path("version").asInt();
        if (!"text".equals(body.path("type").asText()) || !body.path("prompt").isTextual() || version <= 0) {
            throw new IOException("not a text prompt");
        }
        JsonNode config = body.get("config");
        if (config == null || config.isNull() || (config.isObject() && config.isEmpty())) {
            config = null;
        }
        return new Prompt(body.get("prompt").asText(), name, version, config, false);
    }

    /**
     * A dataset and all its items (GET /api/public/v2/datasets/{name}, then GET /api/public/dataset-items page by
     * page, as the SDKs do); version, an ISO 8601 time or null, pins the items as they were then. For an experiment
     * runner, never a request path.
     */
    public Dataset dataset(String name, String version) throws IOException, InterruptedException {
        JsonNode head = ContentGuard.Json.MAPPER.readTree(send(get("/api/public/v2/datasets/" + path(name), timeout)));
        List<DatasetItem> items = new ArrayList<>();
        for (int page = 1; page <= 1000; page++) {
            String pageQuery = "datasetName=" + query(name) + "&page=" + page + "&limit=50"
                    + (version == null || version.isEmpty() ? "" : "&version=" + query(version));
            JsonNode body = ContentGuard.Json.MAPPER.readTree(send(get("/api/public/dataset-items?" + pageQuery, timeout)));
            for (JsonNode item : body.path("data")) {
                items.add(new DatasetItem(item.path("id").asText(), item.path("status").asText(), item.get("input"),
                        item.get("expectedOutput"), item.get("metadata")));
            }
            if (page >= body.path("meta").path("totalPages").asInt() || body.path("data").isEmpty()) {
                return new Dataset(head.path("id").asText(), head.path("name").asText(), List.copyOf(items));
            }
        }
        throw new IOException("langfuse dataset " + name + ": more than 1000 pages");
    }

    /**
     * Starts the score queue: at most size scores wait (256 when 0), and onError, if set, hears about each failed
     * send (rate-limit what it logs; the message carries the status code only).
     */
    public Scores scores(int size, Consumer<Exception> onError) {
        return new Scores(this, size <= 0 ? 256 : size, onError);
    }

    /** Sends scores off the request path, one at a time, from a bounded queue. */
    public static final class Scores {
        private static final Score STOP = new Score(null, "", 0, null, null, null, null, null);

        private final LangfuseClient client;
        private final BlockingQueue<Score> queue;
        private final Consumer<Exception> onError;
        private final Thread worker;
        private boolean closed; // guarded by this

        private Scores(LangfuseClient client, int size, Consumer<Exception> onError) {
            this.client = client;
            this.queue = new ArrayBlockingQueue<>(size + 1);
            this.onError = onError;
            this.worker = daemon("langfuse-scores").newThread(this::run);
            worker.start();
        }

        /** Queues a score; false when the queue is full or closed, and the score is dropped. */
        public synchronized boolean send(Score score) {
            return !closed && queue.remainingCapacity() > 1 && queue.offer(score);
        }

        /** Stops taking scores and sends the queued ones within grace (the shutdown grace period). */
        public boolean close(Duration grace) throws InterruptedException {
            synchronized (this) {
                if (!closed) {
                    closed = true;
                    queue.put(STOP); // the slot kept free for it
                }
            }
            worker.join(Math.max(1, grace.toMillis()));
            return !worker.isAlive();
        }

        private void run() {
            while (true) {
                Score score;
                try {
                    score = queue.take();
                } catch (InterruptedException interrupted) {
                    return;
                }
                if (score == STOP) {
                    return;
                }
                try {
                    client.createScore(score);
                } catch (InterruptedException interrupted) {
                    return;
                } catch (IOException | RuntimeException failed) {
                    if (onError != null) {
                        onError.accept(failed);
                    }
                }
            }
        }
    }

    private HttpRequest get(String pathAndQuery, Duration requestTimeout) {
        return HttpRequest.newBuilder(URI.create(base + pathAndQuery)).timeout(requestTimeout).GET().build();
    }

    private byte[] send(HttpRequest request) throws IOException, InterruptedException {
        HttpRequest authorised = HttpRequest.newBuilder(request, (name, value) -> true)
                .header("Authorization", auth)
                .build();
        HttpResponse<byte[]> response = http.send(authorised, HttpResponse.BodyHandlers.ofByteArray());
        if (response.statusCode() >= 300) {
            throw new IOException("langfuse " + request.method() + " " + request.uri().getPath() + ": status "
                    + response.statusCode());
        }
        return response.body();
    }

    private static String path(String segment) {
        return URLEncoder.encode(segment, StandardCharsets.UTF_8).replace("+", "%20");
    }

    private static String query(String value) {
        return URLEncoder.encode(value, StandardCharsets.UTF_8);
    }

    private static java.util.concurrent.ThreadFactory daemon(String name) {
        return runnable -> {
            Thread thread = new Thread(runnable, name);
            thread.setDaemon(true);
            return thread;
        };
    }
}
