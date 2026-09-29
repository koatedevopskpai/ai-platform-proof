import { test } from "node:test";
import assert from "node:assert/strict";
import { buildApp } from "../src/index.js";
import { configFrom } from "../src/config.js";

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