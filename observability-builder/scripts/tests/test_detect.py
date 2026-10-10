import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import detect  # noqa: E402


def write(root: Path, rel: str, text: str) -> None:
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


class DetectNodeProject(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        write(self.root, "package.json", json.dumps({
            "type": "module",
            "main": "dist/index.js",
            "scripts": {"start": "node dist/index.js", "build": "esbuild src/index.ts --bundle"},
            "dependencies": {"express": "^4.22.0", "pino": "^10.0.0", "@opentelemetry/api": "^1.9.0", "openai": "^4.0.0"},
            "devDependencies": {"esbuild": "^0.25.0"},
        }))
        write(self.root, "src/routes/health.ts", 'router.get("/health/ready", ready);\nrouter.get("/api/users", list);\n')
        write(self.root, "src/observability/tracing.ts", "export const x = 1;\n")
        write(self.root, "node_modules/express/index.js", 'app.get("/health/ignored")\n')
        write(self.root, "AGENTS.md", "# rules\n")
        write(self.root, "chart/Chart.yaml", "name: demo\n")

    def tearDown(self):
        self.tmp.cleanup()

    def test_stack_and_telemetry(self):
        result = detect.detect(self.root)
        self.assertEqual(result["languages"], ["javascript"])
        self.assertIn("express", result["frameworks"])
        self.assertIn("openai", result["frameworks"])
        self.assertIn("opentelemetry", result["telemetry"])
        self.assertIn("pino", result["telemetry"])
        self.assertEqual(result["module_system"], "esm")
        self.assertEqual(result["bundler"], "esbuild")
        self.assertEqual(result["scripts"]["start"], "node dist/index.js")

    def test_files_health_and_modules(self):
        result = detect.detect(self.root)
        self.assertTrue(result["files"]["AGENTS.md"])
        self.assertTrue(result["files"]["chart/Chart.yaml"])
        self.assertFalse(result["files"]["observability.yml"])
        self.assertIn("/health/ready", result["health_paths"])
        self.assertNotIn("/health/ignored", result["health_paths"])
        self.assertEqual(result["observability_modules"], ["src/observability"])


class DetectStartCommand(unittest.TestCase):
    """The container's start command, a TypeScript loader, and test runners that look like bundlers."""

    def test_entrypoint_script_and_loader(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            write(root, "package.json", json.dumps({
                "type": "module",
                "scripts": {"start": "node dist/server.js", "dev": "tsx watch src/server.ts", "test": "vitest run"},
                "dependencies": {"express": "^4.21.0", "tsx": "^4.19.0"},
                "devDependencies": {"vitest": "^4.1.0", "typescript": "^5.7.0"},
            }))
            write(root, "Dockerfile", 'FROM node:22\nCOPY . .\nENTRYPOINT ["./docker-entrypoint.sh"]\n')
            write(root, "docker-entrypoint.sh", "#!/bin/sh\nnpm run db:migrate\nexec node --import tsx src/server.ts\n")
            write(root, "src/server.ts", "app.listen(port);\n")
            write(root, "src/observability/metrics.ts", "server.listen(port, host);\n")
            result = detect.detect(root)
            self.assertEqual(result["start_commands"], ["node --import tsx src/server.ts"])
            self.assertEqual(result["entrypoints"][0], "src/server.ts")
            self.assertNotIn("src/observability/metrics.ts", result["entrypoints"])
            self.assertEqual(result["loader"], "tsx")
            # `vitest run` is not vite, and typescript is the only build tool here.
            self.assertEqual(result["bundler"], "typescript")

    def test_vite_build_is_still_a_bundler(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            write(root, "package.json", json.dumps({"scripts": {"build": "vite build"}, "dependencies": {"react": "^19.0.0"}}))
            self.assertEqual(detect.detect(root)["bundler"], "vite")

    def test_procfile_and_cmd(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            write(root, "requirements.txt", "flask\ngunicorn\n")
            write(root, "Procfile", "web: gunicorn -c gunicorn.conf.py app:app\n")
            write(root, "Dockerfile", 'CMD ["python", "-m", "app.main"]\n')
            commands = detect.detect(root)["start_commands"]
            self.assertIn("python -m app.main", commands)
            self.assertIn("gunicorn -c gunicorn.conf.py app:app", commands)


class DetectPythonProject(unittest.TestCase):
    def test_requirements(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            write(root, "requirements.txt", "fastapi==0.142.2\nuvicorn[standard]>=0.30\nsentry-sdk\n")
            result = detect.detect(root)
            self.assertEqual(result["languages"], ["python"])
            self.assertIn("fastapi", result["frameworks"])
            self.assertIn("uvicorn", result["frameworks"])
            self.assertIn("sentry", result["telemetry"])


class DetectOtherStacks(unittest.TestCase):
    def test_maven_go_stdlib_entrypoints_and_exact_health_paths(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            write(root, "java/pom.xml", "<artifactId>spring-boot-starter-webmvc</artifactId><artifactId>quarkus-rest</artifactId>")
            write(root, "go/go.mod", "module example.com/x\n\ngo 1.27\n")
            write(root, "go/main.go", 'package main\nimport "net/http"\nfunc main() { mux := http.NewServeMux()\n'
                  'mux.Handle("GET /ready", nil); mux.Handle("/api/healthcare", nil); http.ListenAndServe(":1", mux) }\n')
            write(root, "py/requirements.txt", "fastapi==1.0\nuvicorn\n")
            write(root, "py/run.py", "import uvicorn\nuvicorn.run('app.main:app')\n")
            write(root, "node/package.json", json.dumps({"dependencies": {"@nestjs/core": "11"}, "scripts": {"start": "node dist/main.js"}}))
            write(root, "node/src/main.ts", "const app = await NestFactory.create(AppModule);\nawait app.listen(port);\n")
            java = detect.detect(root / "java")
            self.assertEqual(java["frameworks"], ["quarkus", "spring-boot", "spring-mvc"])
            go = detect.detect(root / "go")
            self.assertEqual(go["frameworks"], ["net/http"])
            self.assertEqual(go["health_paths"], ["/ready"])
            self.assertIn("run.py", detect.detect(root / "py")["entrypoints"])
            write(root, "next/package.json", json.dumps({"dependencies": {"next": "16", "react": "19"}}))
            write(root, "next/app/health/route.ts", "export function GET() {}\n")
            write(root, "next/app/(shop)/api/healthcare/route.ts", "export function GET() {}\n")
            write(root, "next/instrumentation.ts", "export async function register() {}\n")
            nxt = detect.detect(root / "next")
            self.assertEqual((nxt["workload"], nxt["health_paths"], nxt["entrypoints"]), ("fullstack", ["/health"], ["instrumentation.ts"]))
            node = detect.detect(root / "node")
            self.assertEqual(node["frameworks"], ["nestjs"])
            self.assertEqual(node["entrypoints"], ["dist/main.js", "src/main.ts"])


    def test_test_files_are_not_entrypoints_or_modules(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            write(root, "package.json", json.dumps({"dependencies": {"express": "5"}, "scripts": {"start": "node dist/index.js"}}))
            write(root, "src/index.ts", "app.listen(port);\n")
            write(root, "src/tests/routes/health.test.ts", "const server = app.listen(0);\n")
            write(root, "src/handlers/orders.handler.test.ts", "server.listen(3000);\n")
            write(root, "src/__tests__/app.ts", "app.listen(config.port);\n")
            write(root, "e2e/app.e2e-spec.ts", "await app.listen(3000);\n")
            write(root, "tests/conftest.py", "app = FastAPI()\n")
            write(root, "tools/testdata/main.go", "package main\n")
            write(root, "src/tests/observability/fake-exporter.ts", "export {};\n")
            result = detect.detect(root)
            self.assertEqual(result["entrypoints"], ["dist/index.js", "src/index.ts"])
            self.assertEqual(result["observability_modules"], [])

    def test_frontend_workloads(self):
        cases = {
            "sveltekit": ({"@sveltejs/kit": "2", "svelte": "5", "vite": "7"}, "fullstack"),
            "nuxt": ({"nuxt": "4", "vue": "3"}, "fullstack"),
            "react-router": ({"@react-router/node": "7", "react": "19"}, "fullstack"),
            "angular-express": ({"@angular/core": "20", "express": "5"}, "fullstack"),
            "vue-spa": ({"vue": "3", "vite": "7"}, "spa"),
            "fastify": ({"fastify": "5"}, "api"),
        }
        with tempfile.TemporaryDirectory() as tmp:
            for name, (deps, workload) in cases.items():
                write(Path(tmp), f"{name}/package.json", json.dumps({"dependencies": deps}))
                write(Path(tmp), f"{name}/src/App.vue", "<template><div /></template>\n")
                self.assertEqual(detect.detect(Path(tmp) / name)["workload"], workload, name)


class DetectGenAI(unittest.TestCase):
    """Model providers, AI frameworks, vector stores and LLM-observability tools, in their own field."""

    def test_javascript_ai_stack_and_env_names(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            write(root, "package.json", json.dumps({"dependencies": {
                "express": "5", "openai": "6", "@ai-sdk/anthropic": "2", "ai": "5", "@langfuse/otel": "5",
                "@arizeai/openinference-instrumentation-openai": "3", "@pinecone-database/pinecone": "6"}}))
            write(root, ".env.example", "OPENAI_API_KEY=sk-CANARY-VALUE\nexport LANGFUSE_PUBLIC_KEY=\nDATABASE_URL=postgres://x\n")
            write(root, ".env", "ANTHROPIC_API_KEY=never-read\n")
            result = detect.detect(root)
            self.assertEqual(result["ai"], {
                "providers": ["anthropic", "openai"], "frameworks": ["vercel-ai-sdk"], "vector_stores": ["pinecone"],
                "observability": ["langfuse", "openinference"], "env_names": ["LANGFUSE_PUBLIC_KEY", "OPENAI_API_KEY"]})
            self.assertLessEqual({"langfuse", "openinference"}, set(result["telemetry"]))
            self.assertEqual(result["workload"], "api")
            self.assertNotIn("CANARY", json.dumps(result))
            self.assertNotIn("ANTHROPIC_API_KEY", json.dumps(result))

    def test_python_instrumentation_families(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            write(root, "requirements.txt", "fastapi\nopenai>=1\nlanggraph\nllama_index\npgvector\nlangfuse==3.0\n"
                                            "opentelemetry-instrumentation-openai\nopentelemetry-instrumentation-openai-v2\n"
                                            "openinference-instrumentation-langchain\n")
            ai = detect.detect(root)["ai"]
            self.assertEqual((ai["providers"], ai["frameworks"], ai["vector_stores"]),
                             (["openai"], ["langgraph", "llamaindex"], ["pgvector"]))
            self.assertEqual(ai["observability"], ["langfuse", "openinference", "openllmetry", "otel-genai"])

    def test_go_and_java_ai_stacks(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            write(root, "go/go.mod", "module example.com/x\n\nrequire (\n\tgithub.com/openai/openai-go/v3 v3.2.0\n"
                                     "\tgithub.com/pgvector/pgvector-go v0.3.0\n\tgithub.com/tmc/langchaingo v0.1.13\n)\n")
            write(root, "java/pom.xml", "<artifactId>spring-ai-openai-spring-boot-starter</artifactId>"
                                        "<artifactId>spring-ai-pgvector-store</artifactId><artifactId>langchain4j-open-ai</artifactId>")
            go = detect.detect(root / "go")["ai"]
            self.assertEqual((go["providers"], go["frameworks"], go["vector_stores"]), (["openai"], ["langchaingo"], ["pgvector"]))
            java = detect.detect(root / "java")["ai"]
            self.assertEqual((java["frameworks"], java["vector_stores"]), (["langchain4j", "spring-ai"], ["pgvector"]))

    def test_no_ai(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            write(root, "requirements.txt", "fastapi\nuvicorn\n")
            write(root, ".env.example", "DATABASE_URL=\n")
            self.assertIsNone(detect.detect(root)["ai"])


if __name__ == "__main__":
    unittest.main()
