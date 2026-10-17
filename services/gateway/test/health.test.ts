import { test } from "node:test";
import assert from "node:assert/strict";
import { buildApp } from "../src/index.js";
import { configFrom } from "../src/config.js";
import { createServer } from "node:http";
import { once } from "node:events";

test("GET /health returns degraded when backends unreachable", async () => {
  const app = await buildApp(
    configFrom({ ragApiUrl: "http://127.0.0.1:1", dotnetApiUrl: "http://127.0.0.1:2" }),
  );

  const res = await app.inject({ method: "GET", url: "/health" });
  assert.equal(res.statusCode, 200);
  const body = res.json();
  assert.equal(body.status, "degraded");
  assert.equal(body.services.length, 2);

  await app.close();
});

test("GET /health rejects an HTTP 200 degraded backend", async () => {
  const backend = createServer((_req, reply) => {
    reply.setHeader("content-type", "application/json");
    reply.end(JSON.stringify({ status: "degraded" }));
  });
  backend.listen(0, "127.0.0.1");
  await once(backend, "listening");
  const address = backend.address();
  assert.ok(address && typeof address === "object");
  const url = `http://127.0.0.1:${address.port}`;
  const app = await buildApp(configFrom({ ragApiUrl: url, dotnetApiUrl: url }));
  try {
    const reply = await app.inject({ method: "GET", url: "/health" });
    assert.equal(reply.json().status, "degraded");
    assert.ok(reply.json().services.every((s: { status: string }) => s.status === "error"));
  } finally {
    await app.close();
    await new Promise<void>((resolve, reject) => backend.close((error) => error ? reject(error) : resolve()));
  }
});
