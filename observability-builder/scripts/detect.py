#!/usr/bin/env python3
"""Detect a project's stack and existing telemetry and print it as JSON.

Usage: python3 detect.py <project> [--max-files N]
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

SKIP_DIRS = {
    ".git", "node_modules", "dist", "build", "target", "vendor", "__pycache__", ".venv",
    "venv", ".next", "coverage", ".turbo", ".gradle", ".idea", ".vscode", ".svelte-kit", ".nuxt",
    ".output", ".angular",
}
SOURCE_SUFFIXES = {
    ".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs", ".mts", ".cts", ".vue", ".svelte", ".py", ".go",
    ".java", ".kt", ".kts", ".rb", ".php", ".cs", ".rs",
}
# Test files (same pattern as audit.py): they start servers too, but are never entry points.
TEST_RE = re.compile(
    r"(^|/)(tests?|__tests__|spec|e2e|testdata)(/|$)|\.(test|spec|e2e-spec)\.[^/]+$|_test\.(go|py)$|(^|/)(test_[^/]+|conftest)\.py$"
    r"|(^|/)tests?\.rs$"
)

FRAMEWORKS = {
    "javascript": {
        "express": "express", "fastify": "fastify", "@nestjs/core": "nestjs", "koa": "koa",
        "@hapi/hapi": "hapi", "hono": "hono", "next": "nextjs", "@sveltejs/kit": "sveltekit",
        "nuxt": "nuxt", "@react-router/node": "react-router", "@remix-run/node": "remix",
        "@angular/ssr": "angular-ssr", "astro": "astro", "react": "react", "vue": "vue",
        "@angular/core": "angular", "svelte": "svelte", "vite": "vite", "@trpc/server": "trpc",
        "@apollo/server": "apollo", "graphql-yoga": "graphql-yoga", "@grpc/grpc-js": "grpc",
        "bullmq": "bullmq", "kafkajs": "kafka", "amqplib": "rabbitmq", "node-cron": "node-cron",
        "ws": "websocket", "@modelcontextprotocol/sdk": "mcp", "openai": "openai",
        "@anthropic-ai/sdk": "anthropic", "ai": "vercel-ai-sdk", "langchain": "langchain",
    },
    "python": {
        "fastapi": "fastapi", "django": "django", "djangorestframework": "django-rest-framework",
        "flask": "flask", "litestar": "litestar", "quart": "quart", "sanic": "sanic", "falcon": "falcon",
        "tornado": "tornado", "starlette": "starlette", "aiohttp": "aiohttp", "celery": "celery", "grpcio": "grpc",
        "gunicorn": "gunicorn", "uvicorn": "uvicorn", "mcp": "mcp", "openai": "openai",
        "anthropic": "anthropic", "langchain": "langchain", "langgraph": "langgraph",
    },
    "go": {
        "github.com/gin-gonic/gin": "gin", "github.com/labstack/echo": "echo",
        "github.com/gofiber/fiber": "fiber", "github.com/go-chi/chi": "chi",
        "github.com/gorilla/mux": "gorilla-mux",
        "google.golang.org/grpc": "grpc", "connectrpc.com/connect": "connect",
    },
    "java": {
        "spring-boot-starter-webflux": "spring-webflux", "spring-boot-starter-web": "spring-mvc", "spring-boot-starter-webmvc": "spring-mvc",
        "spring-boot": "spring-boot", "quarkus": "quarkus", "micronaut": "micronaut", "javalin": "javalin",
        "helidon": "helidon", "vertx-web": "vertx", "ktor-server": "ktor", "grpc": "grpc", "spring-ai": "spring-ai",
    },
}

# Dependency-name fragments that reveal existing telemetry.
TELEMETRY = [
    ("@opentelemetry/", "opentelemetry"), ("opentelemetry", "opentelemetry"),
    ("go.opentelemetry.io", "opentelemetry"), ("io.opentelemetry", "opentelemetry"),
    ("@sentry/", "sentry"), ("sentry-sdk", "sentry"), ("dd-trace", "datadog"),
    ("ddtrace", "datadog"), ("newrelic", "newrelic"), ("elastic-apm", "elastic-apm"),
    ("prom-client", "prometheus"), ("@prometheus-io/client", "prometheus"),
    ("prometheus_client", "prometheus"), ("prometheus-", "prometheus"),
    ("github.com/prometheus/client_golang", "prometheus"), ("micrometer", "micrometer"),
    ("pino", "pino"), ("winston", "winston"), ("morgan", "morgan"), ("bunyan", "bunyan"),
    ("structlog", "structlog"), ("loguru", "loguru"), ("logback", "logback"), ("log4j", "log4j"),
    ("go.uber.org/zap", "zap"), ("zerolog", "zerolog"), ("logrus", "logrus"),
    ("@pyroscope/nodejs", "pyroscope"), ("pyroscope-io", "pyroscope"),
    ("github.com/grafana/pyroscope-go", "pyroscope"), ("io.pyroscope", "pyroscope"),
]

# GenAI by manifest name: model providers, AI frameworks, vector stores and LLM-observability tools.
# A key ending in * matches a prefix; Go keys match a module and its subpackages; Java keys an artifact
# and its suffixed variants. OpenLLMetry publishes most `opentelemetry-instrumentation-<ai library>`
# packages on PyPI; the OpenTelemetry contrib ones are the -v2 packages, google-genai and util-genai.
OPENLLMETRY_PY = ("openai", "anthropic", "langchain", "llamaindex", "bedrock", "vertexai", "google-generativeai",
                  "cohere", "mistralai", "ollama", "groq", "together", "replicate", "watsonx", "transformers",
                  "sagemaker", "haystack", "crewai", "mcp", "openai-agents", "alephalpha", "writer", "chromadb",
                  "pinecone", "qdrant", "milvus", "weaviate", "lancedb", "marqo")
AI = {
    "providers": {
        "javascript": {
            "openai": "openai", "@anthropic-ai/sdk": "anthropic", "@anthropic-ai/bedrock-sdk": "bedrock",
            "@anthropic-ai/vertex-sdk": "vertexai", "@aws-sdk/client-bedrock-runtime": "bedrock",
            "@google/genai": "google-genai", "@google/generative-ai": "google-genai", "@google-cloud/vertexai": "vertexai",
            "@mistralai/mistralai": "mistral", "cohere-ai": "cohere", "groq-sdk": "groq", "ollama": "ollama",
            "@azure/openai": "azure-openai", "@ai-sdk/openai": "openai", "@ai-sdk/anthropic": "anthropic",
            "@ai-sdk/google": "google-genai", "@ai-sdk/google-vertex": "vertexai", "@ai-sdk/amazon-bedrock": "bedrock",
            "@ai-sdk/mistral": "mistral", "@ai-sdk/azure": "azure-openai", "@ai-sdk/groq": "groq", "@ai-sdk/cohere": "cohere",
            "@langchain/openai": "openai", "@langchain/anthropic": "anthropic", "@langchain/google-genai": "google-genai",
            "@langchain/aws": "bedrock", "@langchain/ollama": "ollama", "@langchain/mistralai": "mistral",
        },
        "python": {
            "openai": "openai", "anthropic": "anthropic", "google-genai": "google-genai", "google-generativeai": "google-genai",
            "google-cloud-aiplatform": "vertexai", "vertexai": "vertexai", "mistralai": "mistral", "cohere": "cohere",
            "groq": "groq", "ollama": "ollama", "litellm": "litellm", "together": "together", "langchain-openai": "openai",
            "langchain-anthropic": "anthropic", "langchain-google-genai": "google-genai", "langchain-google-vertexai": "vertexai",
            "langchain-aws": "bedrock", "langchain-ollama": "ollama", "langchain-mistralai": "mistral",
            "llama-index-llms-openai": "openai", "llama-index-llms-anthropic": "anthropic",
        },
        "go": {
            "github.com/openai/openai-go": "openai", "github.com/sashabaranov/go-openai": "openai",
            "github.com/anthropics/anthropic-sdk-go": "anthropic", "google.golang.org/genai": "google-genai",
            "github.com/aws/aws-sdk-go-v2/service/bedrockruntime": "bedrock", "github.com/ollama/ollama": "ollama",
        },
        "java": {"openai-java": "openai", "anthropic-java": "anthropic", "google-genai": "google-genai",
                 "bedrockruntime": "bedrock", "azure-ai-openai": "azure-openai"},
    },
    "frameworks": {
        "javascript": {"ai": "vercel-ai-sdk", "langchain": "langchain", "@langchain/core": "langchain",
                       "@langchain/langgraph": "langgraph", "llamaindex": "llamaindex", "@openai/agents": "openai-agents",
                       "@mastra/core": "mastra", "genkit": "genkit", "@modelcontextprotocol/sdk": "mcp"},
        "python": {"langchain": "langchain", "langchain-core": "langchain", "langgraph": "langgraph",
                   "llama-index": "llamaindex", "llama-index-core": "llamaindex", "openai-agents": "openai-agents",
                   "pydantic-ai": "pydantic-ai", "pydantic-ai-slim": "pydantic-ai", "crewai": "crewai",
                   "autogen-agentchat": "autogen", "pyautogen": "autogen", "ag2": "autogen",
                   "semantic-kernel": "semantic-kernel", "dspy": "dspy", "dspy-ai": "dspy", "haystack-ai": "haystack",
                   "instructor": "instructor", "mcp": "mcp", "smolagents": "smolagents", "google-adk": "google-adk",
                   "strands-agents": "strands"},
        "go": {"github.com/tmc/langchaingo": "langchaingo", "github.com/firebase/genkit/go": "genkit",
               "github.com/cloudwego/eino": "eino", "github.com/mark3labs/mcp-go": "mcp",
               "github.com/modelcontextprotocol/go-sdk": "mcp"},
        "java": {"spring-ai": "spring-ai", "langchain4j": "langchain4j", "semantickernel-api": "semantic-kernel"},
    },
    "vector_stores": {
        "javascript": {"pgvector": "pgvector", "@pinecone-database/pinecone": "pinecone", "@qdrant/js-client-rest": "qdrant",
                       "weaviate-client": "weaviate", "weaviate-ts-client": "weaviate", "chromadb": "chroma",
                       "@zilliz/milvus2-sdk-node": "milvus", "@lancedb/lancedb": "lancedb", "@upstash/vector": "upstash-vector"},
        "python": {"pgvector": "pgvector", "langchain-postgres": "pgvector", "pinecone": "pinecone",
                   "pinecone-client": "pinecone", "qdrant-client": "qdrant", "weaviate-client": "weaviate", "chromadb": "chroma",
                   "pymilvus": "milvus", "faiss-cpu": "faiss", "faiss-gpu": "faiss", "lancedb": "lancedb", "redisvl": "redis"},
        "go": {"github.com/pgvector/pgvector-go": "pgvector", "github.com/qdrant/go-client": "qdrant",
               "github.com/pinecone-io/go-pinecone": "pinecone", "github.com/weaviate/weaviate-go-client": "weaviate",
               "github.com/milvus-io/milvus-sdk-go": "milvus"},
        "java": {"pgvector": "pgvector", "spring-ai-pgvector-store": "pgvector", "langchain4j-pgvector": "pgvector",
                 "pinecone-client": "pinecone", "milvus-sdk-java": "milvus"},
    },
    "observability": {
        "javascript": {"langfuse": "langfuse", "langfuse-node": "langfuse", "langfuse-langchain": "langfuse",
                       "langfuse-vercel": "langfuse", "@langfuse/*": "langfuse", "@traceloop/node-server-sdk": "openllmetry",
                       "@traceloop/instrumentation-*": "openllmetry", "@arizeai/openinference-*": "openinference",
                       "@arizeai/phoenix-otel": "phoenix", "@opentelemetry/instrumentation-openai": "otel-genai",
                       "langsmith": "langsmith", "braintrust": "braintrust", "@lmnr-ai/lmnr": "laminar", "opik": "opik"},
        "python": {"langfuse": "langfuse", "traceloop-sdk": "openllmetry",
                   **{f"opentelemetry-instrumentation-{name}": "openllmetry" for name in OPENLLMETRY_PY},
                   "opentelemetry-instrumentation-openai-v2": "otel-genai", "opentelemetry-instrumentation-google-genai": "otel-genai",
                   "opentelemetry-instrumentation-openai-agents-v2": "otel-genai", "opentelemetry-util-genai": "otel-genai",
                   "openinference-*": "openinference", "arize-phoenix": "phoenix", "arize-phoenix-otel": "phoenix",
                   "langsmith": "langsmith", "logfire": "logfire", "weave": "weave", "braintrust": "braintrust", "opik": "opik",
                   "agentops": "agentops", "lmnr": "laminar"},
        "go": {"github.com/henomis/langfuse-go": "langfuse"},
        "java": {"langfuse-java": "langfuse"},
    },
}
# Variable names in example env files that show a model provider or an LLM-observability tool; names only.
AI_ENV_FILES = (".env.example", ".env.sample", ".env.template", "example.env", "env.example")
AI_ENV = re.compile(r"^\s*(?:export\s+)?((?:OPENAI|AZURE_OPENAI|ANTHROPIC|GEMINI|GOOGLE_GENAI|MISTRAL|COHERE|GROQ|OLLAMA|"
                    r"LITELLM|LANGFUSE|LANGSMITH|LANGCHAIN|TRACELOOP|OPENINFERENCE|PHOENIX|HELICONE)_[A-Z0-9_]*)\s*=", re.MULTILINE)

# A path whose last segment is a probe name: `/health`, `/health/live`, `/q/health`; never `/api/healthcare`.
HEALTH_RE = re.compile(r"""["'`](?:[A-Z]+\s+)?((?:/[\w.-]+)*/(?:health|healthz|ready|readyz|readiness|live|livez|liveness|status|ping))["'`]""")
# Entry points: a server started in code.
# A runtime started by a container entrypoint, a Procfile or a shell script: `exec node --import tsx src/server.ts`.
START_RE = re.compile(
    r"^\s*(?:exec\s+)?(?:(?:web|worker):\s*)?((?:node|bun|deno|tsx|ts-node|python3?|gunicorn|uvicorn|hypercorn|java|dotnet)\b[^\n&|;]*)",
    re.MULTILINE,
)
START_FILE_RE = re.compile(r"(?:^|\s)((?:\./)?[\w./-]+\.(?:m?[jt]s|cjs|py|jar))(?=\s|$)")
ENTRY_RE = re.compile(
    r"\buvicorn\.run\s*\(|\bhypercorn\b|=\s*(?:FastAPI|Flask|Quart|Starlette|Litestar)\s*\(|get_[wa]sgi_application\s*\("
    r"|\bNestFactory\.create\s*\(|\.listen\s*\(\s*(?:port|PORT|process\.env|config|\d)|\bhttp\.ListenAndServe|\bListenAndServe\s*\("
    r"|\bSpringApplication\.run\s*\(|@QuarkusMain|\bQuarkus\.run\s*\(|\bMicronaut\.run\s*\("
)


def _walk(root: Path, max_files: int):
    count = 0
    for path in sorted(root.rglob("*")):
        if any(part in SKIP_DIRS for part in path.relative_to(root).parts):
            continue
        if path.is_file():
            yield path
            count += 1
            if count >= max_files:
                return


def _js_deps(pkg: dict) -> dict:
    deps = {}
    for key in ("dependencies", "devDependencies", "optionalDependencies", "peerDependencies"):
        deps.update(pkg.get(key) or {})
    return deps


def _python_deps(text: str) -> list[str]:
    names = re.findall(r"^\s*([A-Za-z0-9_.\-\[\]]+)\s*(?:[=<>~!].*)?$", text, re.MULTILINE)
    names += re.findall(r"""["']([A-Za-z0-9_.\-]+)(?:\[[^\]]*\])?\s*(?:[=<>~!][^"']*)?["']""", text)
    return sorted({n.split("[")[0].lower() for n in names if n and not n.startswith("#")})


def _match(names, table, language: str = "") -> list[str]:
    found = set()
    for name in names:
        if language == "java":
            name = name.split(":")[-1]   # Gradle group:artifact
        for key, label in table.items():
            if name == key or name.startswith(key + "/") or (key in name and "/" in key) \
                    or (language == "java" and name.startswith(key + "-")):
                found.add(label)
    return sorted(found)


def _telemetry(names) -> list[str]:
    found = set()
    for name in names:
        lowered = name.lower()
        for fragment, label in TELEMETRY:
            if fragment in lowered:
                found.add(label)
    return sorted(found)


def _ai_match(names, table: dict, language: str) -> set[str]:
    found = set()
    for name in names:
        name = name.split(":")[-1] if language == "java" else re.sub(r"[-_.]+", "-", name) if language == "python" else name
        for key, label in table.items():
            if key.endswith("*"):
                hit = name.startswith(key[:-1])
            else:
                hit = name == key or (language == "go" and name.startswith(key + "/")) \
                    or (language == "java" and name.startswith(key + "-"))
            if hit:
                found.add(label)
    return found


def _ai(names: dict[str, list[str]], root: Path) -> dict | None:
    """Model providers, AI frameworks, vector stores and LLM-observability tools; None when there are none."""
    found = {kind: set() for kind in AI}
    for language, deps in names.items():
        for kind, tables in AI.items():
            found[kind] |= _ai_match(deps, tables.get(language, {}), language)
    env_names = set()
    for name in AI_ENV_FILES:
        path = root / name
        if path.is_file():
            env_names |= set(AI_ENV.findall(path.read_text(encoding="utf-8", errors="ignore")))
    if not any(found.values()) and not env_names:
        return None
    return {**{kind: sorted(values) for kind, values in found.items()}, "env_names": sorted(env_names)}


def _uses_tool(tool: str, command: str) -> bool:
    """`tool` as a word in a script: `vite build` yes, `vitest run` no."""
    return re.search(rf"(?<![\w@/.-]){re.escape(tool)}(?![\w-])", command) is not None


def _start_commands(root: Path) -> list[str]:
    """How the process really starts: Dockerfile ENTRYPOINT/CMD, an entrypoint script they run, a Procfile."""
    commands: list[str] = []
    scripts: list[Path] = []
    for name in ("Dockerfile", "Containerfile", "Procfile"):
        path = root / name
        if not path.is_file():
            continue
        text = path.read_text(encoding="utf-8", errors="ignore")
        if name == "Procfile":
            commands += [m.strip() for m in START_RE.findall(text)]
            continue
        for directive in re.findall(r"^\s*(?:ENTRYPOINT|CMD)\s+(.+)$", text, re.MULTILINE):
            try:
                parts = json.loads(directive)
                line = " ".join(parts) if isinstance(parts, list) else str(parts)
            except ValueError:
                line = directive.strip()
            commands += [m.strip() for m in START_RE.findall(line)]
            for token in line.split():
                candidate = root / token.lstrip("./")
                if candidate.is_file() and candidate.suffix in ("", ".sh") and candidate not in scripts:
                    scripts.append(candidate)
    for path in scripts:
        commands += [m.strip() for m in START_RE.findall(path.read_text(encoding="utf-8", errors="ignore"))]
    return list(dict.fromkeys(commands))


def detect(root: Path, max_files: int = 20000) -> dict:
    result = {
        "project": str(root), "languages": [], "frameworks": [], "telemetry": [],
        "module_system": None, "bundler": None, "loader": None, "scripts": {}, "start_commands": [], "entrypoints": [],
        "health_paths": [], "files": {}, "observability_modules": [], "workload": None, "ai": None,
    }
    names: dict[str, list[str]] = {}

    pkg_path = root / "package.json"
    if pkg_path.is_file():
        pkg = json.loads(pkg_path.read_text(encoding="utf-8"))
        deps = _js_deps(pkg)
        names["javascript"] = sorted(deps)
        result["module_system"] = "esm" if pkg.get("type") == "module" else "commonjs"
        scripts = pkg.get("scripts") or {}
        result["scripts"] = {k: scripts[k] for k in ("start", "dev", "build", "test") if k in scripts}
        for tool in ("esbuild", "webpack", "vite", "rollup", "tsup", "@swc/core", "typescript"):
            if tool in deps or any(_uses_tool(tool, v) for v in scripts.values()):
                result["bundler"] = result["bundler"] or tool
        # TypeScript run directly by a loader: the observability preload goes after it (`--import tsx --import ...`).
        commands = " ".join(scripts.values())
        for loader in ("tsx", "ts-node", "@swc-node/register"):
            if loader in deps and _uses_tool(loader, commands):
                result["loader"] = loader
                break
        if pkg.get("main"):
            result["entrypoints"].append(pkg["main"])
        start = re.search(r"\b(?:node|ts-node|tsx|bun)\s+(?:--?\S+(?:\s+\S+\.m?[jt]s)?\s+)*([\w./-]+\.(?:m?js|cjs|ts))", scripts.get("start", ""))
        if start and start.group(1) not in result["entrypoints"]:
            result["entrypoints"].append(start.group(1))
    # The container's real start command comes first: package.json `start` may be unused by the image.
    result["start_commands"] = _start_commands(root)
    for command in result["start_commands"]:
        for match in START_FILE_RE.findall(command):
            rel = match[2:] if match.startswith("./") else match
            if (root / rel).is_file() and rel not in result["entrypoints"] and "/observability/" not in f"/{rel}":
                result["entrypoints"].insert(0, rel)
        if result["loader"] is None and re.search(r"--(?:import|require|loader)[ =](tsx|ts-node)\b", command):
            result["loader"] = re.search(r"--(?:import|require|loader)[ =](tsx|ts-node)\b", command).group(1)
    for name in ("requirements.txt", "pyproject.toml", "setup.py", "Pipfile"):
        path = root / name
        if path.is_file():
            names.setdefault("python", []).extend(_python_deps(path.read_text(encoding="utf-8")))
    gomod = root / "go.mod"
    if gomod.is_file():
        names["go"] = re.findall(r"^\s*(?:require\s+)?([\w.\-]+\.[\w.\-/]+)\s+v", gomod.read_text(encoding="utf-8"), re.MULTILINE)
    for name in ("pom.xml", "build.gradle", "build.gradle.kts"):
        path = root / name
        if path.is_file():
            text = path.read_text(encoding="utf-8")
            names.setdefault("java", []).extend(re.findall(r"<artifactId>([^<]+)</artifactId>", text))
            names.setdefault("java", []).extend(re.findall(r"""["']([\w.\-]+:[\w.\-]+)(?::[^"']*)?["']""", text))

    for language, deps in names.items():
        result["languages"].append(language)
        result["frameworks"] += _match(deps, FRAMEWORKS.get(language, {}), language)
        result["telemetry"] += _telemetry(deps)
    result["ai"] = _ai(names, root)
    if result["ai"]:
        result["telemetry"] += result["ai"]["observability"]
    result["frameworks"] = sorted(set(result["frameworks"]))
    result["telemetry"] = sorted(set(result["telemetry"]))

    for candidate in ("observability.yml", "AGENTS.md", "CONTRIBUTING.md", "README.md", "Dockerfile",
                      "docker-compose.yml", "compose.yaml", "chart/Chart.yaml"):
        result["files"][candidate] = (root / candidate).is_file()

    health, go_http = set(), False
    for path in _walk(root, max_files):
        rel = path.relative_to(root)
        test = TEST_RE.search(rel.as_posix()) is not None
        named = not test and (path.name in {"main.go", "manage.py", "wsgi.py", "asgi.py", "gunicorn.conf.py"}
                              or re.search(r"Application\.(java|kt)$", path.name))
        if named and str(rel) not in result["entrypoints"]:
            result["entrypoints"].append(str(rel))
        if path.suffix not in SOURCE_SUFFIXES:
            continue
        if "observability" in rel.parts[:-1] and not test:
            module = str(Path(*rel.parts[: rel.parts.index("observability") + 1]))
            if module not in result["observability_modules"]:
                result["observability_modules"].append(module)
        try:
            text = path.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        health.update(HEALTH_RE.findall(text))
        in_module = "observability" in rel.parts[:-1]   # its own /metrics server is not the app
        if not named and not test and not in_module and str(rel) not in result["entrypoints"] and ENTRY_RE.search(text):
            result["entrypoints"].append(str(rel))
        if path.suffix == ".go" and '"net/http"' in text and re.search(r"\bhttp\.(ListenAndServe|Server\s*\{|NewServeMux)", text):
            go_http = True
    if go_http and not any(f in result["frameworks"] for f in FRAMEWORKS["go"].values()):
        result["frameworks"].append("net/http")
    if "nextjs" in result["frameworks"]:
        # File-system routing: app/<segments>/route.ts and page.tsx define the paths.
        for base in (root / "app", root / "src" / "app"):
            for path in sorted(base.rglob("*")) if base.is_dir() else []:
                if path.stem in ("route", "page") and path.suffix in (".ts", ".tsx", ".js", ".jsx"):
                    segments = [s for s in path.relative_to(base).parts[:-1] if not s.startswith("(")]
                    route = "/" + "/".join(segments)
                    if HEALTH_RE.search(f'"{route}"'):
                        health.add(route)
        for name in ("instrumentation.ts", "instrumentation.js", "src/instrumentation.ts", "instrumentation-client.ts",
                     "src/instrumentation-client.ts", "app/layout.tsx", "src/app/layout.tsx", "proxy.ts", "middleware.ts"):
            if (root / name).is_file() and name not in result["entrypoints"]:
                result["entrypoints"].append(name)
    result["health_paths"] = sorted(health)[:50]
    frameworks = set(result["frameworks"])
    ui = {"react", "vue", "angular", "svelte"}
    ssr = {"nextjs", "sveltekit", "nuxt", "react-router", "remix", "angular-ssr", "astro"}
    servers = {"express", "fastify", "nestjs", "koa", "hapi", "hono"}
    if frameworks & ssr or (frameworks & ui and frameworks & servers):
        # A server-rendered framework, or a server that also serves the pages: the proxy lives in the app.
        result["workload"] = "fullstack"
    elif frameworks & ui:
        result["workload"] = "spa"
    elif frameworks:
        result["workload"] = "api"
    return result


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("project", type=Path)
    parser.add_argument("--max-files", type=int, default=20000)
    args = parser.parse_args(argv)
    if not args.project.is_dir():
        print(f"not a directory: {args.project}", file=sys.stderr)
        return 1
    print(json.dumps(detect(args.project.resolve(), args.max_files), indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
