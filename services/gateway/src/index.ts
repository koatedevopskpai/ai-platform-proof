import Fastify from "fastify";
import { randomBytes } from "node:crypto";
import rateLimit from "@fastify/rate-limit";
import { pathToFileURL } from "node:url";
import { loadConfig, type Config } from "./config.js";

declare module "fastify" {
  interface FastifyRequest {
    traceId: string;
    spanId: string;
    startedAt: number;
  }
}

interface HealthResult {
  service: string;
  status: string;
}

async function checkHealth(url: string): Promise<HealthResult> {
  try {
    const res = await fetch(`${url}/health`, { signal: AbortSignal.timeout(2000) });
    const body = await res.json() as { status?: string };
    return { service: url, status: res.ok && body.status === "ok" ? "ok" : "error" };
  } catch {
    return { service: url, status: "unreachable" };
  }
}

export async function buildApp(config: Config = loadConfig()) {
  const app = Fastify({ logger: true, disableRequestLogging: true });
  app.addHook("onRequest", async (req, reply) => {
    const incoming = req.headers["x-trace-id"];
    req.traceId = typeof incoming === "string" && /^[0-9a-f]{32}$/.test(incoming) ? incoming : randomBytes(16).toString("hex");
    req.spanId = randomBytes(8).toString("hex");
    req.startedAt = performance.now();
    reply.header("x-trace-id", req.traceId);
  });
  app.addHook("onResponse", async (req, reply) => {
    app.log.info({event: "span", trace_id: req.traceId, span_id: req.spanId,
      parent_span_id: null, operation: "http.request", component: "gateway",
      duration_ms: performance.now() - req.startedAt, http_status: reply.statusCode,
      status: reply.statusCode >= 400 ? "error" : "ok"});
  });

  await app.register(rateLimit, { max: config.rateLimitMax, timeWindow: "1 minute" });

  app.get("/health", async () => {
    const [rag, dotnet] = await Promise.all([
      checkHealth(config.ragApiUrl),
      checkHealth(config.dotnetApiUrl),
    ]);
    const services = [rag, dotnet];
    const allOk = services.every((s) => s.status === "ok");
    return { status: allOk ? "ok" : "degraded", services };
  });

  for (const [route, target, operation] of [
    ["/ingest", config.dotnetApiUrl, "tool.ingest"],
    ["/search", config.ragApiUrl, "tool.search"],
    ["/rag", config.ragApiUrl, "rag.application"],
  ]) {
    app.post(route, async (req, reply) => {
      const spanId = randomBytes(8).toString("hex");
      const started = performance.now();
      let status = "error";
      let errorType: string | undefined;
      try {
        const res = await fetch(`${target}${route}`, {
          method: "POST",
          headers: {"content-type": "application/json", "x-trace-id": req.traceId, "x-parent-span-id": spanId},
          body: JSON.stringify(req.body ?? {}), signal: AbortSignal.timeout(15000),
        });
        const body = await res.json();
        status = res.ok ? "ok" : "error";
        reply.code(res.status);
        return body;
      } catch (error) {
        errorType = error instanceof Error ? error.name : "UnknownError";
        reply.code(503);
        return {error: "upstream_unavailable", trace_id: req.traceId};
      } finally {
        app.log.info({event: "span", trace_id: req.traceId, span_id: spanId,
          parent_span_id: req.spanId, component: route === "/ingest" ? "dotnet-ingest" : "rag-api",
          operation, status, error_type: errorType, duration_ms: performance.now() - started});
      }
    });
  }

  return app;
}

export async function startServer(config: Config = loadConfig()) {
  const app = await buildApp(config);

  const shutdown = async (signal: string) => {
    app.log.info(`received ${signal}, shutting down`);
    await app.close();
    process.exit(0);
  };
  process.on("SIGINT", () => void shutdown("SIGINT"));
  process.on("SIGTERM", () => void shutdown("SIGTERM"));

  await app.listen({ port: config.port, host: "0.0.0.0" });
  return app;
}

const isMain =
  process.argv[1] !== undefined && import.meta.url === pathToFileURL(process.argv[1]).href;

if (isMain) {
  void startServer();
}
