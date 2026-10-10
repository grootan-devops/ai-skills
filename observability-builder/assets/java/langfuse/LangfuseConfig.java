// Template from the observability-builder skill: copy into the observability module and set the package.
// What the yml's genai.langfuse section gives the exporter and the client (references/backends/langfuse.md).

package observability.langfuse;

import java.nio.charset.StandardCharsets;
import java.time.Duration;
import java.util.Base64;

/**
 * The Langfuse settings: the origin (the OTLP and API paths are added in code), the keys read from mounted files
 * (never the environment), the per-request timeout (10 s when unset) and the export scope (ai_context or ai_only).
 */
public record LangfuseConfig(String baseUrl, String publicKey, String secretKey, Duration timeout, String scope) {
    public LangfuseConfig {
        baseUrl = baseUrl.replaceAll("/+$", "");
        timeout = timeout == null || timeout.isZero() || timeout.isNegative() ? Duration.ofSeconds(10) : timeout;
        scope = scope == null || scope.isBlank() ? "ai_context" : scope;
    }

    /** The Basic credentials built from the keys. */
    String basic() {
        return Base64.getEncoder().encodeToString((publicKey + ":" + secretKey).getBytes(StandardCharsets.UTF_8));
    }

    /** Without the keys, so a logged config never carries them. */
    @Override
    public String toString() {
        return "LangfuseConfig[baseUrl=" + baseUrl + ", timeout=" + timeout + ", scope=" + scope + "]";
    }
}
