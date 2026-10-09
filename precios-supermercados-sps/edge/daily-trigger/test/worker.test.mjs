import assert from "node:assert/strict";
import test from "node:test";

import worker, { CRON, dispatchDaily, OWNER, REPO, WORKFLOW } from "../worker.mjs";

const noWait = async () => {};

function fakeFetch(statuses) {
  const calls = [];
  const impl = async (url, init) => {
    calls.push({ url, init });
    const status = statuses.shift();
    if (status === "throw") throw new TypeError("network down");
    return new Response(null, { status });
  };
  return { impl, calls };
}

test("dispatches the daily workflow on main with the read-only authorization input", async () => {
  const { impl, calls } = fakeFetch([204]);
  const result = await dispatchDaily({ GITHUB_DISPATCH_TOKEN: " tok " }, { fetchImpl: impl, wait: noWait });
  assert.deepEqual(result, { dispatched: true, attempt: 1, status: 204 });
  assert.equal(calls.length, 1);
  assert.equal(calls[0].url, `https://api.github.com/repos/${OWNER}/${REPO}/actions/workflows/${WORKFLOW}/dispatches`);
  assert.equal(calls[0].init.method, "POST");
  assert.equal(calls[0].init.headers.authorization, "Bearer tok");
  assert.equal(calls[0].init.headers["user-agent"], "precios-sps-daily-trigger");
  assert.deepEqual(JSON.parse(calls[0].init.body), { ref: "main", inputs: { live_read_only_authorized: "true" } });
});

test("retries transient GitHub errors and network failures up to three times", async () => {
  const { impl, calls } = fakeFetch([502, "throw", 204]);
  const result = await dispatchDaily({ GITHUB_DISPATCH_TOKEN: "tok" }, { fetchImpl: impl, wait: noWait });
  assert.equal(result.attempt, 3);
  assert.equal(calls.length, 3);
});

test("does not retry client errors and reports the status", async () => {
  const { impl, calls } = fakeFetch([401, 204]);
  await assert.rejects(
    dispatchDaily({ GITHUB_DISPATCH_TOKEN: "tok" }, { fetchImpl: impl, wait: noWait }),
    /daily_trigger_dispatch_failed:401/,
  );
  assert.equal(calls.length, 1);
});

test("fails closed without a token", async () => {
  await assert.rejects(dispatchDaily({}, { fetchImpl: async () => new Response(null, { status: 204 }) }), /token_missing/);
});

test("exposes no public endpoint and runs at 07:43 UTC (01:43 Honduras)", async () => {
  const response = await worker.fetch(new Request("https://example.test/"));
  assert.equal(response.status, 404);
  assert.equal(CRON, "43 7 * * *");
});
