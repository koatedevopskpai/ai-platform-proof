import { test } from "node:test";
import assert from "node:assert/strict";
import { createServer } from "node:http";
import { once } from "node:events";
import { buildApp } from "../src/index.js";
import { configFrom } from "../src/config.js";

test("gateway propagates trace and parent IDs to actual backend HTTP requests", async () => {
  let trace: string | string[] | undefined;
  let parent: string | string[] | undefined;
  const backend = createServer((req, reply) => {
    trace = req.headers["x-trace-id"];
    parent = req.headers["x-parent-span-id"];
    reply.setHeader("content-type", "application/json");
    reply.end(JSON.stringify({answer: "fixture", sources: []}));
  });
  backend.listen(0, "127.0.0.1"); await once(backend, "listening");
  const address = backend.address(); assert.ok(address && typeof address === "object");
  const app = await buildApp(configFrom({ragApiUrl: `http://127.0.0.1:${address.port}`}));
  try {
    const response = await app.inject({method: "POST", url: "/rag", payload: {query: "fixture"}, headers: {"x-trace-id": "a".repeat(32)}});
    assert.equal(response.statusCode, 200);
    assert.equal(trace, "a".repeat(32));
    assert.match(String(parent), /^[0-9a-f]{16}$/);
    assert.equal(response.headers["x-trace-id"], trace);
  } finally {
    await app.close();
    await new Promise<void>((resolve, reject) => backend.close(error => error ? reject(error) : resolve()));
  }
});
