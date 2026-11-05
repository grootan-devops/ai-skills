// Template from the observability-builder skill: copy into the observability module and set the package.
// What the yml's genai.langfuse section gives the exporter and the client (references/backends/langfuse.md).

package observability.langfuse;

import java.nio.charset.StandardCharsets;
import java.time.Duration;
import java.util.Base64;

/**
 * The Langfuse settings: the origin (the OTLP and API paths are added in code), the keys read from mounted files
 * (never the environment), the per-request timeout (10 s when unset), the export scope (ai_context or ai_only) and
 * the cap on one export request's body before gzip (3 MiB when zero or less).
 */
public record LangfuseConfig(String baseUrl, String publicKey, String secretKey, Duration timeout, String scope,
        int maxRequestBytes) {
    /** One export request's body before gzip, at most, when maxRequestBytes isn't set. */
    public static final int DEFAULT_MAX_REQUEST_BYTES = 3 * 1024 * 1024;

    public LangfuseConfig {
        baseUrl = baseUrl.replaceAll("/+$", "");
        timeout = timeout == null || timeout.isZero() || timeout.isNegative() ? Duration.ofSeconds(10) : timeout;
        scope = scope == null || scope.isBlank() ? "ai_context" : scope;
        maxRequestBytes = maxRequestBytes > 0 ? maxRequestBytes : DEFAULT_MAX_REQUEST_BYTES;
    }

    public LangfuseConfig(String baseUrl, String publicKey, String secretKey, Duration timeout, String scope) {
        this(baseUrl, publicKey, secretKey, timeout, scope, 0);
    }

    /** The Basic credentials built from the keys. */
    String basic() {
        return Base64.getEncoder().encodeToString((publicKey + ":" + secretKey).getBytes(StandardCharsets.UTF_8));
    }

    /** Without the keys, so a logged config never carries them. */
    @Override
    public String toString() {
        return "LangfuseConfig[baseUrl=" + baseUrl + ", timeout=" + timeout + ", scope=" + scope
                + ", maxRequestBytes=" + maxRequestBytes + "]";
    }
}
