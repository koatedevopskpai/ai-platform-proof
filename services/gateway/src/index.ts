import Fastify from "fastify";
import rateLimit from "@fastify/rate-limit";
import { pathToFileURL } from "node:url";
import { loadConfig, type Config } from "./config.js";

interface HealthResult {
  service: string;
  status: string;
}

async function checkHealth(url: string): Promise<HealthResult> {
  try {
    const res = await fetch(`${url}/health`, { signal: AbortSignal.timeout(2000) });
    return { service: url, status: res.ok ? "ok" : "error" };
  } catch {
    return { service: url, status: "unreachable" };
  }
}

export async function buildApp(config: Config = loadConfig()) {
  const app = Fastify({ logger: true });

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

  app.post("/ingest", async (req, reply) => {
    const res = await fetch(`${config.dotnetApiUrl}/ingest`, {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify(req.body ?? {}),
    });
    reply.code(res.status);
    return res.json();
  });

  app.post("/search", async (req, reply) => {
    const res = await fetch(`${config.ragApiUrl}/search`, {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify(req.body ?? {}),
    });
    reply.code(res.status);
    return res.json();
  });

  app.post("/rag", async (req, reply) => {
    const res = await fetch(`${config.ragApiUrl}/rag`, {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify(req.body ?? {}),
    });
    reply.code(res.status);
    return res.json();
  });

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