import contextlib
import io
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import audit  # noqa: E402


def write(root: Path, rel: str, text: str) -> None:
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


class AuditTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def rules(self, modules=None, include_tests=False):
        return [(f.severity, f.rule, f.location) for f in audit.audit(self.root, modules or ["src/observability"], include_tests)]

    def test_secret_capture_is_p0(self):
        write(self.root, "src/a.ts", 'span.setAttribute("auth", req.headers["authorization"]);\n')
        write(self.root, "src/b.py", 'logger.info("login %s", request.headers.get("cookie"))\n')
        found = self.rules()
        self.assertIn(("P0", "secret-capture", "src/a.ts:1"), found)
        self.assertIn(("P0", "secret-capture", "src/b.py:1"), found)

    def secrets(self):
        return [loc for sev, rule, loc in self.rules() if rule == "secret-capture"]

    def test_secret_read_into_a_variable_then_logged(self):
        write(self.root, "src/auth.js", "export function requireAuth(req, res, next) {\n"
              "  const header = req.get('authorization');\n"
              "  console.log(`auth: authorization=${header}`)\n}\n")
        write(self.root, "src/auth.go", "func auth(r *http.Request) {\n"
              "\theader := r.Header.Get(\"Authorization\")\n"
              "\tfmt.Printf(\"auth: %s\\n\", header)\n}\n")
        write(self.root, "src/Auth.java", "  public boolean preHandle(HttpServletRequest request) {\n"
              "    String header = request.getHeader(HttpHeaders.AUTHORIZATION);\n"
              "    System.out.println(\"auth: \" + header);\n  }\n")
        write(self.root, "src/auth.py", "def authenticate(request: Request, authorization: Annotated[str | None, Header()] = None) -> Principal:\n"
              "    print(f\"auth: authorization={authorization}\")\n")
        self.assertEqual(sorted(self.secrets()), ["src/Auth.java:3", "src/auth.go:3", "src/auth.js:3", "src/auth.py:2"])

    def test_secret_followed_through_string_operations(self):
        write(self.root, "src/a.ts", "function f(req) {\n  const header = req.get('authorization');\n"
              "  const token = header.split(' ')[1];\n  logger.debug('bearer', token);\n}\n")
        self.assertEqual(self.secrets(), ["src/a.ts:4"])

    def test_a_test_of_a_secret_is_not_logging_it(self):
        write(self.root, "src/guard.ts", "function canActivate(request) {\n"
              "  const header = request.headers.authorization;\n"
              "  this.logger.debug(header ? 'authorization header present' : 'authorization header missing');\n"
              "  logger.info('length', header.length, !!header);\n"
              "  logger.info('raw', header);\n}\n")
        self.assertEqual(self.secrets(), ["src/guard.ts:5"])

    def test_print_of_a_secret_header_on_one_line(self):
        write(self.root, "src/p.py", 'print(request.headers["Authorization"])\n')
        self.assertEqual(self.secrets(), ["src/p.py:1"])

    def test_no_secret_finding_for_words_calls_conditions_or_other_blocks(self):
        write(self.root, "src/ok.ts",
              "function a(req) {\n  const token = req.get('authorization');\n  console.error('No JWT token found');\n}\n"
              "async function b() {\n  const rows = await fetchCatalog(this.apiKey, TAG);\n  logger.info(`rows ${rows.length}`);\n"
              "  const label = config.apiKey ? 'set' : 'unset';\n  logger.info('key', label);\n}\n"
              "function c() {\n  logger.info('token count', token);\n}\n"
              "function d(req) {\n  let secret = req.get('authorization');\n  secret = 'redacted';\n  console.log(secret);\n}\n")
        self.assertEqual(self.secrets(), [])

    def test_injected_header_parameters_in_more_frameworks(self):
        write(self.root, "src/auth.controller.ts", "  @Get()\n  find(@Headers('authorization') auth: string) {\n"
              "    this.logger.log(`auth ${auth}`);\n  }\n")
        write(self.root, "src/Res.java", "    public void fail(\n            @HeaderParam(\"X-Debug-Token\") String debugToken) {\n"
              "        LOG.infof(\"debug %s\", debugToken);\n    }\n")
        write(self.root, "src/views.py", "def view(request):\n    auth = request.META.get(\"HTTP_AUTHORIZATION\")\n    print(f\"auth: {auth}\")\n")
        write(self.root, "src/gin.go", "func requireAuth(c *gin.Context) {\n\theader := c.GetHeader(\"Authorization\")\n"
              "\tlog.Printf(\"auth: %s\", header)\n}\n")
        write(self.root, "src/echo.go", "func requireAuth(c *echo.Context) error {\n\theader := c.Request().Header.Get(echo.HeaderAuthorization)\n"
              "\tfmt.Printf(\"auth: %s\\n\", header)\n}\n")
        self.assertEqual(sorted(self.secrets()), ["src/Res.java:3", "src/auth.controller.ts:3", "src/echo.go:3", "src/gin.go:3", "src/views.py:3"])

    def test_body_read_then_logged_across_lines_and_languages(self):
        write(self.root, "src/log.js", "export function requestLogger(req, res, next) {\n  res.on('finish', () => {\n"
              "    const body = req.body === undefined ? '' : JSON.stringify(req.body);\n"
              "    console.log(`${req.method} ${res.statusCode} body=${body}`);\n  });\n}\n")
        write(self.root, "src/log.py", "async def log_requests(request, call_next):\n    body = await request.body()\n"
              "    try:\n        return await call_next(request)\n    finally:\n"
              "        print(f\"{request.method} body={body.decode(errors='replace')}\")\n")
        write(self.root, "src/log.go", "func logRequests(next http.Handler) http.Handler {\n"
              "\treturn http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {\n"
              "\t\tbody, _ := io.ReadAll(r.Body)\n\t\tfmt.Printf(\"%s body=%s\\n\", r.Method, body)\n\t})\n}\n")
        write(self.root, "src/Log.java", "  protected void doFilterInternal(HttpServletRequest request) {\n"
              "    String body = new String(wrapped.getContentAsByteArray(), UTF_8);\n"
              "    System.out.printf(\"%s body=%s%n\",\n            request.getMethod(), body);\n  }\n")
        write(self.root, "src/direct.ts", 'logger.info("request received", {\n  tenantId: req.tenantId,\n  body: req.body,\n});\n')
        write(self.root, "src/Filter.java", "  void log(ContainerRequestContext request) {\n"
              "    Object body = request.getProperty(BODY);\n    System.out.printf(\"body=%s%n\", body);\n  }\n")
        write(self.root, "lib/request-logger.ts", "export function withLog(handler) {\n  return async (request) => {\n"
              "    const body = await readBody(request);\n    const response = await handler(request);\n"
              "    console.log(`${request.method} ${response.status} body=${body}`);\n    return response;\n  };\n}\n"
              "async function readBody(request) {\n  return request.clone().text();\n}\n")
        found = sorted(loc for sev, rule, loc in self.rules() if rule == "body-logged")
        self.assertEqual(found, ["lib/request-logger.ts:5", "src/Filter.java:3", "src/Log.java:3", "src/direct.ts:1", "src/log.go:4",
                                 "src/log.js:4", "src/log.py:6"])

    def test_fields_sizes_and_validation_errors_are_not_the_body(self):
        write(self.root, "src/ok.ts", "function a(req) {\n"
              "  const count = Number(req.body?.count);\n  logger.info('seats', { count });\n"
              "  const parsed = schema.safeParse(req.body);\n  logger.error('invalid', parsed.error.errors);\n"
              "  const data = schema.parse(req.body);\n  console.log('got', { approved: data.approved, size: data.length });\n"
              "  logger.info('bytes', Buffer.byteLength(data));\n  // console.log(data);\n"
              "  logger.info('received', { fields: Object.keys(req.body ?? {}) });\n}\n")
        self.assertEqual([r for r in self.rules() if r[1] in ("body-logged", "body-in-span")], [])

    def test_secrets_in_error_messages(self):
        write(self.root, "src/d.js", "export function fail(debugToken) {\n  throw new Error(`failed (debug token: ${debugToken})`);\n}\n")
        write(self.root, "src/d.py", "def fail(debug_token):\n    raise RuntimeError(f\"failed (debug token: {debug_token})\")\n")
        write(self.root, "src/d.go", "func fail(debugToken string) error {\n\treturn fmt.Errorf(\"failed (debug token: %s)\", debugToken)\n}\n")
        write(self.root, "src/D.java", "  void fail(String debugToken) {\n"
              "    throw new IllegalStateException(\"failed (debug token: \" + debugToken + \")\");\n  }\n")
        write(self.root, "src/codes.ts", "throw new AppError(ErrorCode.AUTH_INVALID_TOKEN, 'missing token');\n"
              "throw new AppError(ErrorCode.VALIDATION_FAILED, {\n  // the allowlist's token validator is a shape check\n  field: 'model',\n});\n")
        found = sorted(loc for sev, rule, loc in self.rules() if rule == "secret-in-error")
        self.assertEqual(found, ["src/D.java:2", "src/d.go:2", "src/d.js:2", "src/d.py:2"])

    def test_values_named_like_secrets_in_logs(self):
        write(self.root, "src/n.ts", "function a(apiKey, token) {\n"
              "  logger.info('calling pricing', apiKey);\n"
              "  logger.info('has token', !!token);\n"
              "  logger.info('token length', token.length);\n"
              "  logger.info('masked', redact(token));\n"
              "  logger.info('page', nextPageToken);\n"
              "  // logger.info('old', token);\n}\n")
        found = [loc for sev, rule, loc in self.rules() if rule == "secret-named-value"]
        self.assertEqual(found, ["src/n.ts:2"])

    def test_secrets_in_outbound_queries(self):
        write(self.root, "src/q.js", "url.searchParams.set('api_key', this.apiKey);\nconst form = new FormData(); form.append('password', pw);\n")
        write(self.root, "src/q.py", 'resp = await http.get("/prices", params={"sku": sku, "api_key": key})\n')
        write(self.root, "src/q.go", 'endpoint := fmt.Sprintf("%s/prices?sku=%s&api_key=%s", base, sku, key)\n')
        write(self.root, "src/Q.java", '.uri(u -> u.path("/prices").queryParam("api_key", apiKey).build())\n')
        write(self.root, "src/logout.ts", "logoutParams.append('id_token_hint', idToken);\n")
        write(self.root, "src/PricingApi.java", '    Price get(@QueryParam("sku") String sku, @QueryParam("api_key") String apiKey);\n')
        found = sorted((sev, loc) for sev, rule, loc in self.rules() if rule == "secret-in-query")
        self.assertEqual(found, [("P2", "src/PricingApi.java:1"), ("P2", "src/Q.java:1"), ("P2", "src/logout.ts:1"),
                                 ("P2", "src/q.go:1"), ("P2", "src/q.js:1"), ("P2", "src/q.py:1")])

    def test_browser_code_rules(self):
        write(self.root, "app/orders/form.tsx", "'use client';\nexport function Form() {\n"
              "  const headers = { Authorization: 'Api-Token ' + process.env.NEXT_PUBLIC_OTLP_TOKEN };\n}\n")
        write(self.root, "app/api/export/route.ts", "headers.Authorization = 'Api-Token ' + config.token;\n")
        found = [(rule, loc) for sev, rule, loc in self.rules() if rule.startswith("browser-")]
        self.assertEqual(found, [("browser-key", "app/orders/form.tsx:3")])

    def test_browser_rules_in_other_frontends(self):
        write(self.root, "src/routes/orders/new/+page.svelte",
              "<script>\n  const headers = { Authorization: 'Api-Token ' + token };\n</script>\n")
        write(self.root, "app/components/OrderForm.vue", "const key = useRuntimeConfig().public.otlpToken;\n")
        write(self.root, "src/app/order-form.component.ts",
              "import { Component } from '@angular/core';\nconst t = import.meta.env.NG_APP_OTLP_TOKEN;\n")
        write(self.root, "src/main.ts", "const key = import.meta.env.VITE_TELEMETRY_API_KEY;\n")
        for server in ("server/api/telemetry.post.ts", "server.ts", "src/lib/export.server.ts"):
            write(self.root, server, "headers.Authorization = 'Api-Token ' + config.token;\n")
        found = [(rule, loc) for sev, rule, loc in self.rules() if rule.startswith(("browser-", "public-"))]
        self.assertIn(("browser-key", "src/routes/orders/new/+page.svelte:2"), found)
        self.assertIn(("browser-key", "app/components/OrderForm.vue:1"), found)
        self.assertIn(("browser-key", "src/app/order-form.component.ts:2"), found)
        self.assertIn(("public-key-var", "src/main.ts:1"), found)
        write(self.root, "src/i18n.ts", "const tolgee = import.meta.env.VITE_APP_TOLGEE_API_KEY;\n")
        write(self.root, "src/app/pay.client.ts", "const stripe = process.env.NEXT_PUBLIC_STRIPE_KEY;\n")
        others = [(rule, loc) for sev, rule, loc in self.rules() if loc.startswith(("src/i18n.ts", "src/app/pay.client.ts"))]
        self.assertEqual(others, [], "only telemetry keys are this audit's concern")
        self.assertFalse([f for f in found if "server" in f[1]], "server-side code may hold the key")

    def test_body_in_span_and_logged_headers(self):
        write(self.root, "src/c.ts", 'span.setAttribute("payload", JSON.stringify(req.body));\nlogger.info("in", req.headers);\n')
        found = self.rules()
        self.assertIn(("P1", "body-in-span", "src/c.ts:1"), found)
        self.assertIn(("P1", "raw-headers-logged", "src/c.ts:2"), found)

    def test_id_labels_on_metrics_and_profiles(self):
        write(self.root, "src/d.ts", 'ordersCounter.add(1, { "user.id": user.id });\nPyroscope.wrapWithLabels({ userId: id }, fn);\n')
        found = self.rules()
        self.assertIn(("P1", "id-metric-label", "src/d.ts:1"), found)
        self.assertIn(("P1", "id-profile-label", "src/d.ts:2"), found)

    def genai(self, include_tests=False):
        return sorted((sev, rule, loc) for sev, rule, loc in self.rules(include_tests=include_tests)
                      if rule.startswith(("genai-", "langfuse-")) or rule in ("sdk-outside-module", "otel-env-outside-loader"))

    def test_genai_content_set_outside_the_module(self):
        write(self.root, "src/chat.py", 'span.set_attribute("gen_ai.input.messages", json.dumps(messages))\n'
                                        'span.set_attribute("gen_ai.usage.input_tokens", usage.prompt_tokens)\n')
        write(self.root, "src/chat.ts", "span.setAttributes({\n  [ATTR_GEN_AI_OUTPUT_MESSAGES]: out,\n});\n")
        write(self.root, "src/observability/genai.py", 'span.set_attribute("gen_ai.input.messages", content)\n')
        self.assertEqual(self.genai(), [("P1", "genai-content-outside-module", "src/chat.py:1"),
                                        ("P1", "genai-content-outside-module", "src/chat.ts:1")])

    def test_langfuse_input_and_output_outside_the_module(self):
        write(self.root, "src/agent.py", "from langfuse import get_client\nlangfuse = get_client()\n"
                                         'with langfuse.start_as_current_observation(as_type="span", name="plan", input=question) as span:\n'
                                         "    span.update(output=answer)\n")
        write(self.root, "src/plan.ts", 'import { startObservation } from "@langfuse/tracing";\n'
                                        'const obs = startObservation("plan", {\n  input: question,\n});\nobs.update({ output });\n')
        write(self.root, "src/form.py", "form.update(input=value)\n")
        write(self.root, "src/observability/genai_content.py", "from langfuse import get_client\nspan.update(input=redacted)\n")
        self.assertEqual(self.genai(), [("P1", "genai-content-unguarded", loc) for loc in
                                        ("src/agent.py:3", "src/agent.py:4", "src/plan.ts:2", "src/plan.ts:5")])

    def test_capture_switches_in_code_and_config(self):
        write(self.root, "src/settings.py", "\n".join([
            'os.environ["TRACELOOP_TRACE_CONTENT"] = "true"',
            'os.environ.setdefault("OTEL_INSTRUMENTATION_GENAI_CAPTURE_MESSAGE_CONTENT", "SPAN_ONLY")',
            'os.environ["TRACELOOP_TRACE_CONTENT"] = "false"',
            "@observe(capture_input=True)",
        ]) + "\n")
        write(self.root, "src/ai.ts", "\n".join([
            'process.env.OPENINFERENCE_HIDE_INPUTS = "false";',
            "await generateText({ model, prompt, experimental_telemetry: { isEnabled: true, recordInputs: true } });",
            "await generateText({",
            "  model, prompt,",
            "  experimental_telemetry: { isEnabled: true },",
            "});",
            "await streamText({ model, experimental_telemetry: { isEnabled: true, recordInputs: false, recordOutputs: false } });",
            "await streamText({",
            "  experimental_telemetry: {",
            "    isEnabled: true,",
            "    recordInputs: false,",
            "  },",
            "});",
            "await generateObject({",
            "  experimental_telemetry: {",
            "    isEnabled: true,",
            "  },",
            "});",
        ]) + "\n")
        write(self.root, "src/steps.ts", 'import { observe } from "@langfuse/tracing";\nexport const plan = observe(planImpl, { name: "plan" });\n'
                                         'export const act = observe(actImpl, { captureInput: false, captureOutput: false });\n')
        write(self.root, "src/observability/loader.py", 'os.environ["TRACELOOP_TRACE_CONTENT"] = "true" if full else "false"\n')
        write(self.root, "src/main/resources/application.yml", "spring:\n  ai:\n    chat:\n      observations:\n"
                                                                "        log-prompt: true\n        # log-completion: true\n")
        write(self.root, "src/main/resources/application-dev.properties", "spring.ai.chat.observations.include-completion=true\n")
        self.assertEqual(self.genai(), sorted([
            ("P1", "genai-capture-on", "src/settings.py:1"), ("P1", "genai-capture-on", "src/settings.py:2"),
            ("P2", "otel-env-outside-loader", "src/settings.py:3"), ("P1", "genai-capture-on", "src/settings.py:4"),
            ("P1", "genai-capture-on", "src/ai.ts:1"), ("P1", "genai-capture-on", "src/ai.ts:2"),
            ("P2", "genai-capture-default", "src/ai.ts:5"), ("P2", "genai-capture-default", "src/ai.ts:15"),
            ("P2", "genai-capture-default", "src/steps.ts:2"),
            ("P1", "genai-capture-on", "src/main/resources/application.yml:5"),
            ("P1", "genai-capture-on", "src/main/resources/application-dev.properties:1"),
        ]))

    def test_content_logged_in_ai_projects_only(self):
        lines = "\n".join([
            'logger.info("model call", extra={"prompt": prompt})',
            'logger.info("got %d messages", len(messages))',
            'print(f"answer {completion.choices[0].message.content}")',
            '_log.info("assistant request", extra={"app.genai.request": json.dumps(messages)})',
            'logger.info("tokens", extra={"prompt_tokens": usage.prompt_tokens, "count": len(self.messages)})',
            'logger.info("Sending the prompt to the model")',
        ]) + "\n"
        write(self.root, "src/chat.py", lines)
        self.assertFalse([f for f in self.genai() if f[1] == "genai-content-logged"])
        write(self.root, "requirements.txt", "fastapi\nopenai>=1\n")
        self.assertEqual([f for f in self.genai() if f[1] == "genai-content-logged"],
                         [("P1", "genai-content-logged", "src/chat.py:1"), ("P1", "genai-content-logged", "src/chat.py:3"),
                          ("P1", "genai-content-logged", "src/chat.py:4")])

    def test_langfuse_keys_and_browser_scoring(self):
        write(self.root, "src/config.ts", 'const sk = "sk-lf-1a2b3c4d-0000-1111-2222-333344445555";\n'
                                          'const placeholder = "sk-lf-your-secret-key";\n')
        write(self.root, "src/app/feedback.client.ts", 'import { LangfuseWeb } from "langfuse";\n'
                                                       "const lf = new LangfuseWeb({ publicKey });\n"
                                                       'lf.score({ traceId, name: "user_feedback", value: 1 });\n'
                                                       "const secretKey = runtime.langfuseSecret;\n")
        write(self.root, "src/app/rate.client.ts", 'import { LangfuseBrowser } from "@langfuse/browser";\n')
        self.assertEqual(self.genai(), [("P0", "langfuse-key", "src/app/feedback.client.ts:4"),
                                        ("P0", "langfuse-key", "src/config.ts:1"),
                                        ("P2", "genai-browser-score", "src/app/feedback.client.ts:2"),
                                        ("P2", "genai-browser-score", "src/app/rate.client.ts:1")])

    def test_ai_context_in_baggage_or_outside_the_module(self):
        write(self.root, "src/chat.py", "from langfuse import propagate_attributes\n"
                                        "with propagate_attributes(user_id=user.id, session_id=conv_id, as_baggage=True):\n")
        write(self.root, "src/chat.ts", 'import { propagateAttributes } from "@langfuse/tracing";\n'
                                        "propagateAttributes({ userId, asBaggage: true }, run);\n")
        write(self.root, "src/observability/genai.py", "from langfuse import propagate_attributes\n"
                                                       "with propagate_attributes(trace_name=name, as_baggage=True):\n")
        self.assertEqual(self.genai(), sorted([
            ("P1", "genai-baggage", "src/chat.py:2"), ("P2", "genai-context-outside-module", "src/chat.py:2"),
            ("P1", "genai-baggage", "src/chat.ts:2"), ("P2", "genai-context-outside-module", "src/chat.ts:2"),
            ("P1", "genai-baggage", "src/observability/genai.py:2")]))

    def test_langfuse_keys_in_opentelemetry_baggage(self):
        write(self.root, "src/evals.py", "from opentelemetry import baggage\n"
                                         'ctx = baggage.set_baggage("langfuse.experiment.id", run.id)\n'
                                         'ok = baggage.set_baggage("tenant.tier", tier)\n')
        write(self.root, "src/evals.ts", "const bag = propagation.createBaggage({\n"
                                         '  "langfuse.experiment.id": { value: run.id },\n'
                                         "});\n")
        write(self.root, "evals/main.go", 'member, _ := baggage.NewMember("langfuse.experiment.name", name)\n')
        self.assertEqual(self.genai(), sorted([("P1", "genai-baggage", "evals/main.go:1"),
                                               ("P1", "genai-baggage", "src/evals.py:2"),
                                               ("P1", "genai-baggage", "src/evals.ts:1")]))

    def test_one_ai_library_instrumented_twice(self):
        write(self.root, "requirements.txt", "openai\nopentelemetry-instrumentation-openai-v2\nopentelemetry-instrumentation-openai\n")
        write(self.root, "package.json", '{"dependencies": {"langchain": "1",\n  "@arizeai/openinference-instrumentation-langchain": "3"}}\n')
        write(self.root, "src/chain.ts", 'import { CallbackHandler } from "@langfuse/langchain";\n')
        found = [f for f in audit.audit(self.root, ["src/observability"]) if f.rule == "genai-duplicate-instrumentation"]
        self.assertEqual([(f.location, f.message) for f in found], [
            ("requirements.txt:3", "openai is instrumented by OpenTelemetry and OpenLLMetry; keep one, or every call is "
                                   "recorded twice"),
            ("src/chain.ts:1", "langchain is instrumented by OpenInference and the Langfuse callback handler; keep one, "
                               "or every call is recorded twice")])
        write(self.root, "requirements.txt", "openai\nopentelemetry-instrumentation-openai-v2\n")
        write(self.root, "src/chain.ts", "export {};\n")
        self.assertFalse([f for f in self.genai() if f[1] == "genai-duplicate-instrumentation"])
        # A comment naming another instrumentation is not a second one.
        write(self.root, "requirements.txt", "openai\n")
        write(self.root, "src/observability/genai.ts", "// @opentelemetry/instrumentation-openai covers openai below 7 only\n"
                                                       "export const wrap = (c: unknown) => observeOpenAI(c);\n")
        self.assertFalse([f for f in self.genai() if f[1] == "genai-duplicate-instrumentation"])

    def test_langfuse_and_genai_setup_outside_the_module(self):
        write(self.root, "src/main.py", "\n".join([
            "langfuse = Langfuse(public_key=pk, secret_key=sk)",
            "OpenAIInstrumentor().instrument()",
            'Traceloop.init(app_name="api")',
            'key = os.getenv("LANGFUSE_SECRET_KEY")',
            "client = get_client()",
        ]) + "\n")
        write(self.root, "src/otel.ts", "const lf = new LangfuseSpanProcessor({ publicKey });\nregisterInstrumentations({ instrumentations: [] });\n")
        write(self.root, "src/observability/langfuse.py", "langfuse = Langfuse(public_key=pk)\nOpenAIInstrumentor().instrument()\n")
        self.assertEqual(self.genai(), sorted([
            ("P1", "sdk-outside-module", "src/main.py:1"), ("P1", "sdk-outside-module", "src/main.py:2"),
            ("P1", "sdk-outside-module", "src/main.py:3"), ("P2", "otel-env-outside-loader", "src/main.py:4"),
            ("P1", "sdk-outside-module", "src/otel.ts:1"), ("P1", "sdk-outside-module", "src/otel.ts:2")]))

    def test_second_langfuse_span_sender(self):
        write(self.root, ".env", "OTEL_EXPORTER_OTLP_TRACES_ENDPOINT=https://cloud.langfuse.com/api/public/otel/v1/traces\n")
        write(self.root, "deploy/values.yaml", "env:\n  LANGFUSE_OTLP: https://langfuse.internal/api/public/otel\n"
                                               "  # https://cloud.langfuse.com/api/public/otel stays a comment\n")
        write(self.root, "src/tracing.go", 'exp, _ := otlptracehttp.New(ctx, otlptracehttp.WithURLPath("/api/public/otel/v1/traces"))\n')
        write(self.root, "src/observability/langfuse.go", 'const tracesPath = "/api/public/otel/v1/traces"\n')
        write(self.root, "collector/config.alloy", 'endpoint = "https://langfuse.internal/api/public/otel"\n')
        write(self.root, "docs/setup.md", "Langfuse takes OTLP at /api/public/otel.\n")
        write(self.root, "observability.yml", "genai:\n  langfuse:\n    base_url: https://cloud.langfuse.com\n")
        found = sorted(f.location for f in audit.audit(self.root, ["src/observability"])
                       if f.rule == "genai-langfuse-two-senders")
        self.assertEqual(found, [".env:1", "deploy/values.yaml:2", "src/tracing.go:1"])

    def test_sdk_and_env_outside_module(self):
        write(self.root, "src/server.ts", "const sdk = new NodeSDK({});\nconst e = process.env.OTEL_SERVICE_NAME;\n")
        write(self.root, "src/observability/register.ts", "const sdk = new NodeSDK({});\nprocess.env.OTEL_SERVICE_NAME = 'x';\n")
        found = self.rules()
        self.assertIn(("P1", "sdk-outside-module", "src/server.ts:1"), found)
        self.assertIn(("P2", "otel-env-outside-loader", "src/server.ts:2"), found)
        self.assertFalse([f for f in found if f[2].startswith("src/observability/")])

    def test_safe_lines_and_ignores(self):
        write(self.root, "src/e.ts", "\n".join([
            'span.setAttribute("http.request.body.size", Number(len));',
            'span.setAttribute("app.cart.items_count", items.length);',
            'histogram.record(ms, { "http.route": route, "http.response.status_code": 200 });',
            'span.setAttribute("token", t); // obs-audit: ignore',
            "const set = new Set(); set.add(userId);",
            "logger.info(`Disabling 'require.password.update.after.registration' flag`);",
        ]) + "\n")
        self.assertEqual([f for f in self.rules() if f[2].startswith("src/e.ts")], [])

    def test_tests_skipped_unless_included(self):
        write(self.root, "src/tests/x.test.ts", 'span.setAttribute("auth", req.headers.authorization);\n')
        self.assertEqual([f for f in self.rules() if "x.test.ts" in f[2]], [])
        self.assertTrue([f for f in self.rules(include_tests=True) if "x.test.ts" in f[2]])

    def test_exit_codes(self):
        write(self.root, "src/f.ts", 'logger.info("x", req.headers.cookie);\n')
        self.assertEqual(audit.main([str(self.root), "--module", "src/observability"]), 2)
        write(self.root, "src/f.ts", "const ok = 1;\n")
        self.assertEqual(audit.main([str(self.root), "--module", "src/observability", "--strict"]), 0)

    def test_no_module_note(self):
        write(self.root, "src/g.ts", "const ok = 1;\n")
        found = [(f.rule, f.location) for f in audit.audit(self.root, [])]
        self.assertIn(("no-module", "."), found)

    def located(self, rule, include_tests=False):
        return sorted(loc for sev, rule_, loc in self.rules(include_tests=include_tests) if rule_ == rule)

    def messages(self, rule):
        return {f.location: f.message for f in audit.audit(self.root, ["src/observability"]) if f.rule == rule}

    def test_logger_bypass_in_app_code(self):
        write(self.root, "src/utils/logger.ts", "export const logger = { info: (m) => console.log(m) };\n")
        write(self.root, "src/orders.ts", "import { logger } from './utils/logger';\nexport function place(err) {\n"
              "  console.log('placing order');\n  logger.info('placed');\n  console.error('failed', err);\n}\n")
        write(self.root, "app/service.py", "import logging\nlog = logging.getLogger(__name__)\n\ndef run():\n"
              "    print('starting')\n    log.info('pprint(x) is not a call')\n")
        write(self.root, "go.mod", "module example.com/x\n\nrequire go.uber.org/zap v1.27.0\n")
        write(self.root, "cmd/api/main.go", 'package main\n\nimport (\n\t"fmt"\n\t"log"\n)\n\nfunc main() {\n'
              '\tlog.Printf("listening on %s", addr)\n\tfmt.Println("ready")\n}\n')
        write(self.root, "internal/zl/zl.go", 'package zl\n\nimport "github.com/rs/zerolog/log"\n\nfunc f() {\n\tlog.Print("zerolog")\n}\n')
        write(self.root, "src/main/java/com/example/Job.java", "class Job {\n  private static final Logger log = LoggerFactory.getLogger(Job.class);\n"
              "  void run() {\n    System.out.println(\"run\");\n  }\n}\n")
        write(self.root, "src/main/kotlin/com/example/Server.kt", 'fun main() {\n    println("started")\n}\n')
        write(self.root, "Cargo.toml", '[dependencies]\ntracing = "0.1"\n')
        write(self.root, "src/main.rs", 'fn main() {\n    println!("up");\n}\n\n#[cfg(test)]\nmod tests {\n    #[test]\n'
              '    fn starts() {\n        println!("in a unit test");\n    }\n}\n')
        write(self.root, "build.rs", 'fn main() {\n    println!("cargo:rerun-if-changed=build.rs");\n}\n')
        self.assertEqual(self.located("logger-bypass"), [
            "app/service.py:5", "cmd/api/main.go:10", "cmd/api/main.go:9", "src/main.rs:2",
            "src/main/java/com/example/Job.java:4", "src/main/kotlin/com/example/Server.kt:2", "src/orders.ts:3", "src/orders.ts:5",
        ])
        self.assertIn("console.log writes around the app logger (src/utils/logger.ts)", self.messages("logger-bypass")["src/orders.ts:3"])

    def test_logger_bypass_skips_tests_scripts_module_comments_and_strings(self):
        write(self.root, "package.json", '{"dependencies": {"pino": "^10.0.0"}}')
        write(self.root, "src/tests/orders.test.ts", "console.log('in a test');\n")
        write(self.root, "src/orders.spec.ts", "console.log('in a spec');\n")
        write(self.root, "scripts/backfill.ts", "console.log('done');\n")
        write(self.root, "src/scripts/reencrypt.ts", "console.log('done');\n")
        write(self.root, "prisma/seed.ts", "console.log('seeded');\n")
        write(self.root, "src/migrate.ts", "console.log('migrated');\n")
        write(self.root, "bin/cli.js", "console.log('usage');\n")
        write(self.root, "vite.config.ts", "console.log('building');\n")
        write(self.root, "tools/report.js", "#!/usr/bin/env node\nconsole.log('report');\n")
        write(self.root, "samples/demo/app.ts", "console.log('demo');\n")
        write(self.root, "app/send-report.py", "print('sent')\n")
        write(self.root, "src/observability/config.ts", "console.error(JSON.stringify({ level: 'warn' }));\n")
        write(self.root, "src/components/Cart.tsx", "'use client';\nexport function Cart() { console.log('render'); }\n")
        write(self.root, "src/quiet.ts", "\n".join([
            "// console.log('old');",
            "/*",
            "console.log('disabled');",
            "*/",
            "const page = `",
            "  <script>console.log('runs in the browser');</script>",
            "`;",
            "logger.info('call console.log(x) to debug');",
            "console.log('accepted'); // obs-audit: ignore",
        ]) + "\n")
        write(self.root, "app/jobs.py", 'import logging\n\ndef run():\n    """Run the job.\n\n    print(result) shows it.\n    """\n    return 1\n')
        self.assertEqual(self.located("logger-bypass", include_tests=True), [])

    def test_logger_bypass_without_an_app_logger_of_the_same_language(self):
        write(self.root, "src/server.ts", "console.log('listening');\n")
        write(self.root, "src/request-logger.ts", "export const log = (m) => console.log(m);\n")
        write(self.root, "worker/main.py", "import logging\n")
        # A request logger or a Python logger is not the JS app logger: these lines have no logger at all.
        self.assertEqual(self.located("logger-bypass"), ["src/request-logger.ts:1", "src/server.ts:1"])
        self.assertIn("with no app logger in the project; add the app logger", self.messages("logger-bypass")["src/server.ts:1"])
        write(self.root, "src/app.ts", "const app = Fastify({ logger: true });\napp.log.info('ready');\n")
        self.assertEqual(self.located("logger-bypass"), ["src/request-logger.ts:1", "src/server.ts:1"])
        self.assertIn("(app.log)", self.messages("logger-bypass")["src/server.ts:1"])

    def test_logger_bypass_evidence_from_manifests(self):
        write(self.root, "setup.py", "setup(name='svc', install_requires=['structlog>=24'])\n")
        write(self.root, "svc/main.py", "def main():\n    print('up')\n")
        write(self.root, "examples/demo/package.json", '{"dependencies": {"pino": "^10.0.0"}}')
        write(self.root, "src/server.ts", "console.log('listening');\n")
        self.assertEqual(self.located("logger-bypass"), ["src/server.ts:1", "svc/main.py:2"])
        self.assertIn("(structlog)", self.messages("logger-bypass")["svc/main.py:2"])
        # The pino in examples/ is not this project's logger.
        self.assertIn("with no app logger in the project", self.messages("logger-bypass")["src/server.ts:1"])

    def test_logger_bypass_collapses_a_noisy_file_and_names_the_logger_others_import(self):
        write(self.root, "src/audit/logger.ts", "export const auditLog = {};\n")
        write(self.root, "src/utils/logger.ts", "export const logger = {};\n")
        write(self.root, "src/a.ts", "import { logger } from '../utils/logger';\n")
        write(self.root, "src/b.ts", "import { logger } from 'src/utils/logger';\n")
        write(self.root, "src/routes.ts", "".join(f"console.error('failure {n}');\n" for n in range(7)))
        write(self.root, "src/few.ts", "".join(f"console.warn('warning {n}');\n" for n in range(5)))
        self.assertEqual(self.located("logger-bypass"), [f"src/few.ts:{n}" for n in range(1, 6)] + ["src/routes.ts:1"])
        message = self.messages("logger-bypass")["src/routes.ts:1"]
        self.assertIn("(src/utils/logger.ts), and 6 more in this file", message)

    def test_scheduler_without_a_job_span(self):
        write(self.root, "src/jobs/cleanup.ts", "import cron from 'node-cron';\n\nexport function start() {\n"
              "  cron.schedule('0 * * * *', () => cleanup());\n}\n")
        write(self.root, "src/jobs/report.ts", "@Injectable()\nexport class Report {\n  @Cron('45 * * * * *')\n  run() {}\n}\n")
        write(self.root, "app/scheduler.py", "from apscheduler.schedulers.background import BackgroundScheduler\n"
              "scheduler = BackgroundScheduler()\nscheduler.add_job(sweep, 'interval', minutes=5)\n")
        write(self.root, "src/main/java/com/example/Reports.java", "class Reports {\n  @Scheduled(cron = \"0 0 * * * *\")\n"
              "  public void report() {}\n}\n")
        write(self.root, "internal/jobs/jobs.go", 'package jobs\n\nimport "github.com/robfig/cron/v3"\n\nfunc Start() {\n'
              '\tc := cron.New()\n\tc.AddFunc("@every 1m", sweep)\n\tc.Start()\n}\n')
        write(self.root, "src/jobs.rs", "use tokio_cron_scheduler::{Job, JobScheduler};\n\nasync fn start() {\n"
              "    let sched = JobScheduler::new().await.unwrap();\n"
              "    sched.add(Job::new_async(\"0 * * * * *\", |_, _| Box::pin(async {})).unwrap()).await.unwrap();\n}\n")
        write(self.root, "src/child.ts", "import cron from 'node-cron';\ncron.schedule('* * * * *', () => tracer.startActiveSpan('job tick', run));\n")
        self.assertEqual(self.located("scheduler-no-root-span"), [
            "app/scheduler.py:3", "internal/jobs/jobs.go:7", "src/child.ts:2", "src/jobs.rs:5", "src/jobs/cleanup.ts:4",
            "src/jobs/report.ts:3", "src/main/java/com/example/Reports.java:2",
        ])

    def test_scheduler_with_a_job_span_or_outside_application_code(self):
        write(self.root, "src/jobs/archive.ts", "import cron from 'node-cron';\nimport { withJobRun } from '../observability/jobs';\n"
              "cron.schedule(expr, () => withJobRun('archive', { trigger: 'cron' }, () => archive()));\n")
        write(self.root, "src/jobs/root.ts", "import cron from 'node-cron';\ncron.schedule(expr, () =>\n"
              "  tracer.startActiveSpan('job sweep', { root: true }, (span) => sweep(span)));\n")
        write(self.root, "app/jobs.py", "scheduler.add_job(sweep, 'interval', minutes=5)\n\ndef sweep():\n"
              "    with tracer.start_as_current_span('job sweep', context=Context()):\n        pass\n")
        write(self.root, "internal/jobs/jobs.go", 'package jobs\n\nimport "github.com/robfig/cron/v3"\n\nfunc Start(c *cron.Cron) {\n'
              '\tc.AddFunc("@every 1m", func() {\n\t\tctx, span := tracer.Start(context.Background(), "job sweep")\n'
              '\t\tdefer span.End()\n\t\tsweep(ctx)\n\t})\n}\n')
        write(self.root, "src/main/java/com/example/Reports.java", "class Reports {\n  @Scheduled(fixedRate = 60000)\n  public void report() {\n"
              "    Span span = tracer.spanBuilder(\"job report\")\n        .setNoParent()\n        .startSpan();\n  }\n}\n")
        write(self.root, "internal/store/store.go", 'package store\n\nfunc init() { registry.AddFunc("x", f) }\n')
        write(self.root, "src/poll.ts", "setInterval(() => poll(), 1000);\nconst ok = items.every((item) => item.ready);\n")
        write(self.root, "src/tests/jobs.test.ts", "cron.schedule('* * * * *', tick);\n")
        write(self.root, "src/observability/flush.ts", "cron.schedule('* * * * *', flush);\n")
        self.assertEqual(self.located("scheduler-no-root-span", include_tests=True), [])

    def test_queue_scheduled_jobs_need_a_job_span_somewhere(self):
        write(self.root, "src/queues.ts", "await reports.add('daily', {}, {\n  repeat: { pattern: '0 3 * * *' },\n});\n"
              "await agenda.every('5 minutes', 'cleanup');\n")
        write(self.root, "proj/celery.py", "app.conf.beat_schedule = {\n    'sweep': {'task': 'proj.tasks.sweep', 'schedule': 300.0},\n}\n")
        self.assertEqual(self.located("scheduler-no-root-span"), ["proj/celery.py:1", "src/queues.ts:2", "src/queues.ts:4"])
        write(self.root, "src/worker.ts", "new Worker('reports', (job) => withJobRun(job.name, { trigger: 'cron' }, () => build(job)));\n")
        self.assertEqual(self.located("scheduler-no-root-span"), [])

    def test_sdk_internal_patches(self):
        write(self.root, "src/observability/metrics.ts", "import { PrometheusSerializer } from '@opentelemetry/exporter-prometheus';\n"
              "PrometheusSerializer.prototype._serializeMetricData = function () { return ''; };\n")
        write(self.root, "src/http.ts", "import {\n  HttpInstrumentation,\n} from '@opentelemetry/instrumentation-http';\n"
              "(HttpInstrumentation.prototype as any)._getPatchIncomingRequestFunction = patched;\n")
        write(self.root, "src/provider.ts", "import { NodeTracerProvider } from '@opentelemetry/sdk-trace-node';\n"
              "const provider = new NodeTracerProvider();\n(provider as any)._config = {};\n")
        write(self.root, "src/registry.js", "const client = require('prom-client');\nclient.register._metrics = {};\n")
        write(self.root, "src/counter.ts", "import * as prom from 'prom-client';\nshimmer.wrap(prom.Counter.prototype, 'inc', wrapped);\n")
        write(self.root, "app/telemetry.py", "from opentelemetry.sdk.trace.export import (\n    BatchSpanProcessor,\n)\n"
              "from opentelemetry.sdk.trace import TracerProvider\n"
              "BatchSpanProcessor._export = patched_export\nTracerProvider.get_tracer = patched_get_tracer\n")
        write(self.root, "src/main/java/com/example/Patch.java", "import io.opentelemetry.sdk.trace.SdkTracerProvider;\n"
              "class Patch {\n  Field f = SdkTracerProvider.class.getDeclaredField(\"sharedState\");\n}\n")
        write(self.root, "internal/otel/link.go", "package otel\n\nimport _ \"unsafe\"\n\n"
              "//go:linkname exportSpans go.opentelemetry.io/otel/sdk/trace.exportSpans\nfunc exportSpans()\n")
        self.assertEqual(self.located("sdk-internal-patch"), [
            "app/telemetry.py:5", "app/telemetry.py:6", "internal/otel/link.go:5", "src/counter.ts:2", "src/http.ts:4",
            "src/main/java/com/example/Patch.java:3", "src/observability/metrics.ts:2", "src/provider.ts:3", "src/registry.js:2",
        ])

    def test_no_sdk_patch_for_app_classes_public_api_reads_or_tests(self):
        write(self.root, "src/metrics.ts", "\n".join([
            "import { PrometheusSerializer } from '@opentelemetry/exporter-prometheus';",
            "import { EventEmitter } from 'node:events';",
            "class Cart {}",
            "Cart.prototype._legacy = function () {};",
            "EventEmitter.prototype._maxListeners = 20;",
            "const serializer = new PrometheusSerializer();",
            "const text = serializer.serialize(metrics);",
            "const original = PrometheusSerializer.prototype._serializeMetricData;",
            "if (serializer._prefix === undefined) {}",
            "// PrometheusSerializer.prototype._serializeMetricData = patched;",
        ]) + "\n")
        write(self.root, "app/tracing.py", "from opentelemetry.sdk.trace import TracerProvider\nprovider = TracerProvider()\n"
              "provider.add_span_processor(processor)\n")
        write(self.root, "src/tests/patch.test.ts", "import { PrometheusSerializer } from '@opentelemetry/exporter-prometheus';\n"
              "PrometheusSerializer.prototype._serializeMetricData = stub;\n")
        self.assertEqual(self.located("sdk-internal-patch", include_tests=True), [])



class DataModelRules(unittest.TestCase):
    """Static rules for references/data-model.md: identity, request.id, code.namespace, PII, profiles, baggage,
    excluded resource keys and ID-like metric labels."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def found(self, rule):
        return [f.location for f in audit.audit(self.root, ["src/observability"]) if f.rule == rule]

    def test_identity_from_raw_headers(self):
        write(self.root, "src/a.ts", "span.setAttribute('user.id', req.headers['x-user-id']);\n"
                                     "span.setAttribute('user.id', req.user.id);\n")
        write(self.root, "src/b.py", "def handler(request, x_tenant_id: str = Header(None)):\n"
                                     "    span.set_attribute('tenant.id', x_tenant_id)\n"
                                     "    sid = request.cookies.get('session_id')\n"
                                     "    span.set_attribute('session.id', sid)\n"
                                     "    span.set_attribute('tenant.id', principal.tenant_id)\n")
        write(self.root, "src/C.java", "void h(@RequestHeader(\"X-User-Id\") String userId) {\n"
                                       "  span.setAttribute(\"user.id\", userId);\n}\n")
        self.assertEqual(self.found("identity-from-header"), ["src/C.java:2", "src/a.ts:1", "src/b.py:2", "src/b.py:4"])

    def test_request_id_and_code_namespace(self):
        write(self.root, "src/a.ts", "span.setAttribute('request.id', id);\nspan.setAttribute('correlation.id', id);\n"
                                     "logger.info('done', { requestId: rid, 'request_id': rid });\n")
        write(self.root, "src/b.py", "logger.info('x', extra={'request_id': rid})\nrequest_id = uuid4()\n"
                                     "span.set_attribute('code.namespace', cls)\nspan.set_attribute(CODE_NAMESPACE, cls)\n")
        self.assertEqual(self.found("request-id-attribute"), ["src/a.ts:1", "src/a.ts:3", "src/b.py:1"])
        self.assertEqual(self.found("code-namespace"), ["src/b.py:3", "src/b.py:4"])

    def test_pii_unmasked(self):
        write(self.root, "src/a.py", "\n".join([
            "logger.info('signup %s', user.email)",
            "logger.info('signup %s', mask_email(user.email))",
            "logger.info(f'call {customer.phone_number}')",
            "span.set_attribute('app.user.dob', profile.date_of_birth)",
            "raise ValueError(f'bad {first_name}')",
            "logger.info('sent', len(emails))",
            "logger.info('checked', is_email(value))",
            "logger.warning('invalid', code=ErrorCode.INVALID_EMAIL)",
            "send_email(user)",
        ]) + "\n")
        write(self.root, "src/observability/masking.py", "logger.debug('masking %s', email)\n")
        self.assertEqual(self.found("pii-unmasked"), ["src/a.py:1", "src/a.py:3", "src/a.py:4", "src/a.py:5"])

    def test_values_read_by_key(self):
        write(self.root, "src/k.py", "\n".join([
            "log.info('invoice for %s card %s', body['email'], body[\"card_number\"])",
            "span.set_attribute('customer.email', data.get('email'))",
            "log.info('invoice %s', body['amount'])",
        ]) + "\n")
        self.assertEqual(self.found("pii-unmasked"), ["src/k.py:1", "src/k.py:2"])
        self.assertEqual(self.found("secret-named-value"), ["src/k.py:1"])

    def test_id_like_metric_labels(self):
        write(self.root, "src/m.ts", "const c = new client.Counter({ name: 'orders_total', labelNames: ['tenant_id'] });\n"
                                     "const h = new client.Histogram({ name: 'x', labelNames: ['route', 'method'] });\n")
        write(self.root, "src/m.py", "ORDERS = Counter('orders_total', 'Orders', ['customer_id'])\n"
                                     "PAGES = Counter('pages_total', 'Pages', labelnames=['path'])\n")
        write(self.root, "src/m.go", 'var c = prometheus.NewCounterVec(prometheus.CounterOpts{Name: "x"}, []string{"session_id"})\n')
        write(self.root, "src/n.ts", "counter.add(1, { 'organization.id': org.id });\nmeter.record(1, { 'url.full': url });\n")
        self.assertEqual(self.found("id-metric-label"), ["src/m.go:1", "src/m.py:1", "src/m.py:2", "src/m.ts:1",
                                                         "src/n.ts:1", "src/n.ts:2"])

    def test_profile_labels_come_from_the_resource(self):
        write(self.root, "src/observability/profiling.py", "\n".join([
            "pyroscope.configure(",
            "    application_name=resource['service.name'],",
            "    tags={'service_version': '1.2.3', 'process_runtime_name': 'cpython'},",
            ")",
            "pyroscope.configure(application_name=cfg.name, tags=static_labels(resource))",
        ]) + "\n")
        write(self.root, "src/observability/profiling.ts", "Pyroscope.init({ appName: 'checkout-api', tags });\n")
        self.assertEqual(self.found("profile-label-mismatch"), ["src/observability/profiling.py:1",
                                                                "src/observability/profiling.py:1",
                                                                "src/observability/profiling.ts:1"])

    def test_baggage_limits(self):
        keys = ", ".join(f"'k{i}'" for i in range(9))
        write(self.root, "src/observability/baggage.py", f"BAGGAGE_ALLOW_LIST = [{keys}]\n")
        write(self.root, "src/observability/baggage2.ts", "const BAGGAGE_KEYS = ['tenant.id', 'authorization'];\n"
                                                         "const MAX_BAGGAGE_BYTES = 512;\n")
        write(self.root, "src/observability/ok.ts", "const BAGGAGE_KEYS = ['tenant.id', 'app.client.name'];\n"
                                                    "const MAX_BAGGAGE_BYTES = 512;\n")
        write(self.root, "src/app.ts", "propagation.setBaggage(ctx, bag);\n")
        found = [(f.location, f.message.split(";")[0]) for f in audit.audit(self.root, ["src/observability"])
                 if f.rule == "baggage-over-limit"]
        self.assertEqual(sorted(found), [
            ("src/app.ts:1", "baggage set outside the observability module"),
            ("src/observability/baggage.py:1", "baggage allow-list with 9 keys"),
            ("src/observability/baggage.py:1", "baggage allow-list without the 512-byte cap nearby"),
            ("src/observability/baggage2.ts:1", "baggage allow-list names authorization"),
        ])

    def test_too_many_baggage_keys_in_the_project(self):
        write(self.root, "src/observability/ctx.py", "\n".join(f"ctx = set_baggage('key{i}', v, ctx)" for i in range(10)) + "\n")
        self.assertEqual(self.found("baggage-over-limit"), ["src/observability/ctx.py:9"])

    def test_resource_excluded_keys(self):
        write(self.root, "deploy/values.yaml", "env:\n  - name: K8S_POD_UID\n    valueFrom: {fieldRef: {fieldPath: metadata.uid}}\n"
                                               "  - name: K8S_POD_NAME\n")
        write(self.root, "observability.yml", "resource:\n  attributes:\n    vcs.ref.head.revision: abc\n"
                                              "    k8s.cluster.name: prod\n")
        write(self.root, ".env", "OTEL_NODE_RESOURCE_DETECTORS=env,host,process\nOTEL_RESOURCE_DISABLED_KEYS=process.pid,host.id\n")
        write(self.root, "src/observability/resource.ts", "const r = resourceFromAttributes({ 'service.instance.id': id });\n"
                                                          "const DROP = ['process.pid', 'host.id'];\n"
                                                          "detectors: [envDetector, processDetector],\n")
        self.assertEqual(self.found("resource-excluded-key"), [".env:1", "deploy/values.yaml:2", "observability.yml:3",
                                                               "src/observability/resource.ts:1",
                                                               "src/observability/resource.ts:3"])


class SafetyRules(unittest.TestCase):
    """Rules that keep the telemetry itself cheap and safe: export, credentials, binds, limits and log formatting."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def found(self, rule, include_tests=False):
        return sorted((f.severity, f.location) for f in audit.audit(self.root, ["src/observability"], include_tests)
                      if f.rule == rule)

    def test_sync_exporter(self):
        write(self.root, "src/observability/tracing.py", "from opentelemetry.sdk.trace.export import SimpleSpanProcessor\n"
              "provider.add_span_processor(SimpleSpanProcessor(exporter))\n"
              "provider.add_span_processor(BatchSpanProcessor(exporter))\n")
        write(self.root, "src/observability/otel.ts", "const p = new SimpleSpanProcessor(exporter);\n"
              "const l = new SimpleLogRecordProcessor(logExporter);\n")
        write(self.root, "internal/observability/tp.go", "tp := sdktrace.NewTracerProvider(sdktrace.WithSyncer(exp))\n")
        write(self.root, "src/main/java/obs/Otel.java", "var p = SimpleSpanProcessor.create(exporter);\n")
        write(self.root, "tests/test_tracing.py", "provider.add_span_processor(SimpleSpanProcessor(InMemorySpanExporter()))\n")
        self.assertEqual(self.found("sync-exporter", include_tests=True), [
            ("P1", "internal/observability/tp.go:1"), ("P1", "src/main/java/obs/Otel.java:1"),
            ("P1", "src/observability/otel.ts:1"), ("P1", "src/observability/otel.ts:2"),
            ("P1", "src/observability/tracing.py:2")])

    def test_exporter_without_timeout_in_the_module(self):
        write(self.root, "src/observability/a.py", "exporter = OTLPSpanExporter(endpoint=cfg.endpoint)\n")
        write(self.root, "src/observability/b.py", "exporter = OTLPSpanExporter(endpoint=cfg.endpoint, timeout=cfg.timeout_s)\n")
        write(self.root, "src/observability/c.ts", "const e = new OTLPTraceExporter({\n  url,\n  timeoutMillis: cfg.timeoutMs,\n});\n")
        write(self.root, "src/observability/d.go", "exp, err := otlptracegrpc.New(ctx, otlptracegrpc.WithEndpoint(ep))\n")
        write(self.root, "src/observability/E.java", "var e = OtlpGrpcSpanExporter.builder()\n    .setEndpoint(ep)\n"
              "    .setTimeout(Duration.ofSeconds(5))\n    .build();\n")
        write(self.root, "src/app.py", "exporter = OTLPSpanExporter()\n")   # outside the module: sdk-outside-module's job
        self.assertEqual(self.found("exporter-no-timeout"), [("P2", "src/observability/a.py:1"), ("P2", "src/observability/d.go:1")])

    def test_plaintext_remote_endpoint(self):
        write(self.root, "src/observability/otel.py", "\n".join([
            "exporter = OTLPSpanExporter(endpoint='http://collector.prod.example:4317', insecure=True)",
            "local = OTLPSpanExporter(endpoint='http://localhost:4317', insecure=True)",
            "OTEL_EXPORTER_OTLP_ENDPOINT = 'http://127.0.0.1:4318'",
            "PYROSCOPE_URL = 'http://pyroscope.monitoring:4040'",
            "# sidecar collector in the same pod",
            "OTLP_ENDPOINT = 'http://otel-agent:4317'",
            "LANGFUSE_HOST = 'https://cloud.langfuse.example'",
            "docs_link = 'http://example.org/page'",
        ]) + "\n")
        write(self.root, "src/observability/otel.go", "opts = append(opts, otlptracegrpc.WithInsecure())\n")
        write(self.root, "observability.yml", "\n".join([
            "exporter:",
            "  endpoint: http://alloy:4317   # in-cluster collector, NetworkPolicy limits who can reach it",
            "  insecure: true",
            "profiling:",
            "  endpoint: ${env:PYROSCOPE_URL:-http://localhost:4040}",
            "langfuse:",
            "  base_url: http://langfuse.tools.svc:3000",
        ]) + "\n")
        self.assertEqual(self.found("plaintext-remote-endpoint"), [
            ("P1", "observability.yml:7"), ("P1", "src/observability/otel.go:1"),
            ("P1", "src/observability/otel.py:1"), ("P1", "src/observability/otel.py:4")])

    def test_private_hop_comment_on_the_line_above(self):
        write(self.root, "observability.yml", "exporter:\n  # plaintext to the in-cluster collector; mesh mTLS covers the hop\n"
              "  endpoint: http://alloy:4317\n")
        self.assertEqual(self.found("plaintext-remote-endpoint"), [])

    def test_telemetry_credential_literal(self):
        write(self.root, "src/observability/otel.ts", "\n".join([
            "const headers = { Authorization: 'Bearer abcdef0123456789' };",
            "const ok = { Authorization: `Bearer ${token}` };",
            "const dd = { 'DD-API-KEY': 'f00dfeedf00dfeed' };",
            "const lf = { publicKey: 'pk-lf-1234abcd-5678' };",
        ]) + "\n")
        write(self.root, "src/observability/otel.py", "headers = {'x-honeycomb-team': key}\n"
              "headers = {'x-honeycomb-team': 'hcaik_0123456789abcdef'}\n")
        write(self.root, "observability.yml", "exporter:\n  headers:\n    authorization: ${file:/var/run/secrets/otlp-token}\n"
              "    x-api-key: s3cr3tValue0123\n")
        write(self.root, ".env", "OTEL_EXPORTER_OTLP_HEADERS=Authorization=Basic dXNlcjpwYXNzd29yZA==\n"
              "OTEL_EXPORTER_OTLP_HEADERS=x-api-key=${API_KEY}\nLANGFUSE_SECRET_KEY=sk-lf-0123abcd-ef\n")
        self.assertEqual(self.found("telemetry-credential-literal"), [
            ("P0", ".env:1"), ("P0", ".env:3"), ("P0", "observability.yml:4"),
            ("P0", "src/observability/otel.py:2"), ("P0", "src/observability/otel.ts:1"),
            ("P0", "src/observability/otel.ts:3"), ("P0", "src/observability/otel.ts:4")])

    def test_metrics_public_bind(self):
        write(self.root, "src/observability/metrics.py", "\n".join([
            "start_http_server(9464, addr='0.0.0.0')",
            "start_http_server(9464, addr='0.0.0.0')  # Prometheus scrapes it from the pod network",
            "start_http_server(9464, addr='127.0.0.1')",
        ]) + "\n")
        write(self.root, "src/server.py", "app.run(host='0.0.0.0', port=8080)\n")   # the app's own server
        write(self.root, "observability.yml", "metrics:\n  prometheus:\n    host: 0.0.0.0\n"
              "profiling:\n  # remote: the profiler pulls from the pod network\n  host: 0.0.0.0\n")
        self.assertEqual(self.found("metrics-public-bind"), [("P2", "observability.yml:3"),
                                                             ("P2", "src/observability/metrics.py:1")])

    def test_attribute_limit_raised(self):
        write(self.root, "src/observability/otel.ts", "const limits = { attributeValueLengthLimit: 65536 };\n"
              "const ok = { attributeValueLengthLimit: 4096 };\n")
        write(self.root, "src/observability/otel.py", "os.environ['OTEL_SPAN_ATTRIBUTE_VALUE_LENGTH_LIMIT'] = '100000'\n"
              "SpanLimits(max_span_attribute_length=8192)\n")
        write(self.root, "deploy/values.yaml", "env:\n  - name: OTEL_ATTRIBUTE_VALUE_LENGTH_LIMIT\n    value: '8192'\n")
        write(self.root, ".env", "OTEL_ATTRIBUTE_VALUE_LENGTH_LIMIT=20000\n")
        self.assertEqual(self.found("attribute-limit-raised"), [("P2", ".env:1"), ("P2", "src/observability/otel.py:1"),
                                                                ("P2", "src/observability/otel.ts:1")])

    def test_attribute_limit_equal_to_max_chars_with_a_comment(self):
        write(self.root, "observability.yml", "genai:\n  content:\n    max_chars: 16000\n")
        write(self.root, "src/observability/otel.ts",
              "// raised with genai.content.max_chars: long prompts, recorded in the report\n"
              "const limits = { attributeValueLengthLimit: 16000 };\n"
              "const other = { attributeValueLengthLimit: 16000 };\n"
              "const more = { attributeValueLengthLimit: 20000 }; // max_chars\n")
        write(self.root, ".env", "# equal to max_chars\nOTEL_ATTRIBUTE_VALUE_LENGTH_LIMIT=16000\n")
        self.assertEqual(self.found("attribute-limit-raised"), [("P2", "src/observability/otel.ts:3"),
                                                                ("P2", "src/observability/otel.ts:4")])

    def test_attribute_limit_comment_without_a_yml_value_is_not_enough(self):
        write(self.root, "src/observability/otel.py", "SpanLimits(max_span_attribute_length=16000)  # max_chars\n")
        self.assertEqual(self.found("attribute-limit-raised"), [("P2", "src/observability/otel.py:1")])

    def test_log_extras_passthrough(self):
        write(self.root, "src/observability/logs.py", "\n".join([
            "class JsonFormatter(logging.Formatter):",
            "    def format(self, record):",
            "        extras = {k: v for k, v in record.__dict__.items() if k not in RESERVED}",
            "        allowed = {k: v for k, v in record.__dict__.items() if k in ALLOWED_EXTRAS}",
            "        for key, value in vars(record).items():",
            "            out[key] = value",
        ]) + "\n")
        self.assertEqual(self.found("log-extras-passthrough"), [("P1", "src/observability/logs.py:3"),
                                                                ("P1", "src/observability/logs.py:5")])

    def test_eager_debug_format(self):
        write(self.root, "src/observability/export.py", "\n".join([
            "logger.debug(f'exported {len(batch)} spans')",
            "logger.debug('exported %d spans', len(batch))",
            "logger.debug('exported {} spans'.format(len(batch)))",
            "logger.debug('exported %d spans' % len(batch))",
            "if logger.isEnabledFor(logging.DEBUG):",
            "    logger.debug(f'exported {len(batch)} spans')",
            "logger.info(f'started {name}')",
        ]) + "\n")
        write(self.root, "src/observability/export.ts", "logger.debug(`exported ${batch.length} spans`);\n"
              "logger.debug('exported ' + batch.length + ' spans');\nlogger.debug('exported', { count: batch.length });\n")
        write(self.root, "src/observability/Export.java", "log.debug(\"exported {} spans\", n);\nlog.debug(String.format(\"exported %d\", n));\n")
        self.assertEqual(self.found("eager-debug-format"), [
            ("P2", "src/observability/Export.java:2"), ("P2", "src/observability/export.py:1"),
            ("P2", "src/observability/export.py:3"), ("P2", "src/observability/export.py:4"),
            ("P2", "src/observability/export.ts:1"), ("P2", "src/observability/export.ts:2")])

    def test_detector_filtered_through_an_allow_list(self):
        write(self.root, "src/observability/resource.py", "\n".join([
            "ALLOWED_PROCESS_KEYS = {'process.runtime.name', 'process.runtime.version'}",
            "detected = ProcessResourceDetector().detect()",
            "kept = {k: v for k, v in detected.attributes.items() if k in ALLOWED_PROCESS_KEYS}",
        ]) + "\n")
        write(self.root, "src/observability/resource.ts", "const KEEP_KEYS = new Set(['host.name']);\n"
              "const r = await detectResources({ detectors: [hostDetector] });\n"
              "const attrs = Object.fromEntries(Object.entries(r.attributes).filter(([k]) => KEEP_KEYS.has(k)));\n")
        write(self.root, "src/observability/raw.ts", "const r = await detectResources({ detectors: [hostDetector] });\n")
        self.assertEqual(self.found("resource-excluded-key"), [("P2", "src/observability/raw.ts:1")])

    def test_module_given_as_an_absolute_or_working_directory_path(self):
        write(self.root, "src/observability/tracing.py", "provider = TracerProvider()\n")
        absolute = str(self.root / "src" / "observability")
        self.assertEqual(audit.module_path(self.root.resolve(), absolute), "src/observability")
        self.assertEqual(audit.module_path(self.root.resolve(), "src/observability/"), "src/observability")
        self.assertIsNone(audit.module_path(self.root.resolve(), "/elsewhere/obs"))
        cwd = os.getcwd()
        try:   # relative to the working directory, as a shell user types it
            os.chdir(self.root.parent)
            self.assertEqual(audit.module_path(self.root.resolve(), f"{self.root.name}/src/observability"), "src/observability")
        finally:
            os.chdir(cwd)
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            code = audit.main([str(self.root), "--module", absolute, "--json"])
        self.assertEqual(code, 0)
        rules = {f["rule"] for f in json.loads(out.getvalue())}
        self.assertNotIn("sdk-outside-module", rules)

    def test_the_skill_scripts_inside_a_project_are_not_audited(self):
        found = audit.audit(audit.SKILL_SCRIPTS.parent, ["scripts"])
        self.assertFalse([f for f in found if f.location.startswith("scripts/")])

    def test_logs_py_is_the_app_logger(self):
        write(self.root, "app/observability/logs.py", "import logging\n")
        write(self.root, "app/orders.py", "from app.observability import logs\n\ndef run():\n    print('starting')\n")
        messages = {f.location: f.message for f in audit.audit(self.root, ["src/observability"]) if f.rule == "logger-bypass"}
        self.assertIn("around the app logger (app/observability/logs.py)", messages["app/orders.py:4"])


if __name__ == "__main__":
    unittest.main()


class FaroRules(unittest.TestCase):
    """Browser RUM with Grafana Faro (references/signals/rum.md)."""

    LEAKY = """import { initializeFaro, getWebInstrumentations } from '@grafana/faro-web-sdk';
import { TracingInstrumentation } from '@grafana/faro-web-tracing';

export const faro = initializeFaro({
  url: import.meta.env.VITE_FARO_URL,
  apiKey: 'abc123',
  app: { name: 'shop', version: '1.0.0' },
  instrumentations: [
    ...getWebInstrumentations(),
    new TracingInstrumentation({ instrumentationOptions: { propagateTraceHeaderCorsUrls: [/.*/] } }),
  ],
  consoleInstrumentation: { disabledLevels: [] },
  webVitalsInstrumentation: { reportAllChanges: true },
  trackGeolocation: true,
});
faro.api.setUser({ id: user.id, email: user.email, username: user.login });
"""

    CLEAN = """import { initializeFaro } from '@grafana/faro-web-sdk';
import { makeBeforeSend } from './scrub';

export function start(cfg) {
  const faro = initializeFaro({
    url: cfg.rum.endpoint,
    apiKey: cfg.rum.api_key,
    app: { name: cfg.service.name },
    beforeSend: makeBeforeSend(),
    instrumentationOptions: { propagateTraceHeaderCorsUrls: [/^https:\\/\\/api\\.example\\.com(\\/|$)/] },
  });
  faro.api.setUser({ id: session.userId });
}

export const signOut = () => faro.api.resetUser();
"""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def rules(self):
        return sorted((f.severity, f.rule, f.location) for f in audit.audit(self.root, ["src/observability"])
                      if f.rule.startswith(("faro-", "rum-")))

    def test_leaky_setup(self):
        write(self.root, "src/rum.ts", self.LEAKY)
        write(self.root, "vite.config.ts", "export default { build: { sourcemap: true } };\n")
        self.assertEqual(self.rules(), [
            ("P1", "faro-propagate-all", "src/rum.ts:10"),
            ("P1", "faro-user-pii", "src/rum.ts:16"),
            ("P2", "faro-build-env-config", "src/rum.ts:5"),
            ("P2", "faro-console-all", "src/rum.ts:12"),
            ("P2", "faro-geolocation", "src/rum.ts:14"),
            ("P2", "faro-key-literal", "src/rum.ts:6"),
            ("P2", "faro-no-scrub", "src/rum.ts:4"),
            ("P2", "faro-public-sourcemaps", "vite.config.ts:1"),
            ("P2", "faro-report-all-changes", "src/rum.ts:13"),
            ("P2", "faro-user-not-reset", "src/rum.ts:16"),
        ])
        # The public Faro key is reported once, by the Faro rule.
        self.assertFalse([f for f in audit.audit(self.root, ["src/observability"])
                          if f.rule == "telemetry-credential-literal"])

    def test_page_url_in_events_and_query_access_log(self):
        write(self.root, "src/rum.ts", "import { faro } from '@grafana/faro-web-sdk';\n"
              "faro.api.pushEvent('search', { q, url: window.location.href });\n"
              "faro.api.pushEvent('view', { path: window.location.pathname });\n")
        write(self.root, "nginx.conf", "server {\n  access_log /dev/stdout combined;\n"
              "  log_format j '{\"path\":\"$path_only\",\"m\":\"$request_method\"}';\n}\n")
        write(self.root, "deploy/default.conf.template", "log_format j '{\"r\":\"$request\"}';\n")
        self.assertEqual(self.rules(), [
            ("P1", "faro-event-raw-url", "src/rum.ts:2"),
            ("P2", "rum-access-log-query", "deploy/default.conf.template:1"),
            ("P2", "rum-access-log-query", "nginx.conf:2"),
        ])

    def test_clean_setup(self):
        write(self.root, "src/observability/rum/init.ts", self.CLEAN)
        write(self.root, "vite.config.ts", "export default { build: { sourcemap: 'hidden' } };\n")
        self.assertEqual(self.rules(), [])

    def test_key_in_bundle_with_proxy_transport(self):
        write(self.root, "observability.yml", "version: 1\nrum:\n  enabled: true\n  transport: proxy\n")
        write(self.root, "src/rum.ts", "import { initializeFaro } from '@grafana/faro-web-sdk';\n"
              "initializeFaro({ url: '/telemetry/faro/collect', apiKey: 'k-123', beforeSend: scrub, app: {} });\n")
        self.assertEqual(self.rules(), [("P0", "faro-key-in-bundle", "src/rum.ts:2")])

    def test_sourcemaps_ignored_without_faro(self):
        write(self.root, "vite.config.ts", "export default { build: { sourcemap: true } };\n")
        self.assertEqual(self.rules(), [])

    def test_skill_templates_are_clean(self):
        assets = Path(audit.__file__).resolve().parents[1] / "assets" / "js" / "faro"
        for src in assets.glob("*.ts*"):
            write(self.root, f"src/observability/rum/{src.name}", src.read_text(encoding="utf-8"))
        self.assertEqual(self.rules(), [])
