import assert from "node:assert/strict";
import test from "node:test";

import {
  EdgePolicyError,
  encodeBase64Url,
  sha256Hex,
  signReceiptPayload,
  validateLaColoniaGetUrl,
  verifyReceiptSignature,
} from "../src/core.mjs";
import { DurableAuthorizationStore, MAX_REPLAY_BODY_BYTES } from "../src/durable-store.mjs";
import { executeGatewayRequest } from "../src/gateway-runtime.mjs";

const QUERY = "query productSearchV3 { productSearch { recordsFiltered } }";
const SHA = "a".repeat(40);
const T0 = 2_000_000_000_000;

function expectPolicyError(error, code) {
  assert.ok(error instanceof EdgePolicyError);
  assert.equal(error.code, code);
  return true;
}

class FakeStorage {
  constructor() {
    this.data = new Map();
    this.kv = {
      get: (key) => {
        const value = this.data.get(key);
        return value === undefined ? undefined : structuredClone(value);
      },
      put: (key, value) => this.data.set(key, structuredClone(value)),
    };
  }

  transactionSync(callback) {
    const snapshot = new Map(
      [...this.data.entries()].map(([key, value]) => [key, structuredClone(value)]),
    );
    try {
      return callback();
    } catch (error) {
      this.data = snapshot;
      throw error;
    }
  }
}

async function makeOrigin(page = 1) {
  const from = (page - 1) * 10;
  const variables = {
    query: "supermercado",
    fullText: "",
    selectedFacets: [{ key: "category-1", value: "supermercado" }],
    orderBy: "OrderByNameASC",
    from,
    to: from + 9,
    hideUnavailableItems: false,
    skusFilter: "ALL",
  };
  const params = new URLSearchParams({
    workspace: "master",
    maxAge: "short",
    appsEtag: "remove",
    domain: "store",
    locale: "es-HN",
    operationName: "productSearchV3",
    query: QUERY,
    variables: JSON.stringify(variables),
  });
  const url = `https://www.lacolonia.com/_v/segment/graphql/v1?${params.toString()}`;
  const expectedGraphqlQuerySha256 = await sha256Hex(QUERY);
  const validated = await validateLaColoniaGetUrl(url, { expectedGraphqlQuerySha256 });
  return { url, expectedGraphqlQuerySha256, validated };
}

function claims() {
  return {
    sha: SHA,
    repository: "jchernandez-portfolio/precios-supermercados-sps",
    repository_id: "1282475205",
    ref: "refs/heads/main",
    workflow_ref: "jchernandez-portfolio/precios-supermercados-sps/.github/workflows/precios-supermercados-sps-la-colonia-live.yml@refs/heads/main",
    environment: "la-colonia-live",
    run_id: "32500000000",
    run_attempt: "1",
    sub: "repo:jchernandez-portfolio/precios-supermercados-sps:environment:la-colonia-live",
    jti: "jti-runtime-001",
  };
}

function context(origin, index = 1, overrides = {}) {
  return {
    authorizationId: "auth-001",
    runId: "run-001",
    approvedCommitSha: SHA,
    reservationId: `reservation-${index}`,
    requestId: `request-${index}`,
    requestDigest: origin.validated.canonicalRequestSha256,
    nonce: `nonce-${index}`,
    traversalRole: index === 1 ? "primary" : "reconciliation",
    traversalId: `traversal-${index}`,
    partitionId: "root",
    ...overrides,
  };
}

function collector() {
  return {
    principal: "cloudflare-worker:precios-sps-provenance",
    releaseId: "release-test-001",
    codeSha256: "c".repeat(64),
    signingKeyId: "edge-signing-key-001",
  };
}

function initializeStore(store) {
  store.initialize({
    authorizationId: "auth-001",
    runId: "run-001",
    approvedCommitSha: SHA,
    createdAtMs: T0,
    expiresAtMs: T0 + 30 * 60 * 1000,
    maxRequests: 10,
    minStartIntervalMs: 1500,
  });
}

function clock(start = T0 + 100) {
  let value = start;
  return () => {
    const result = new Date(value);
    value += 100;
    return result;
  };
}

async function ed25519Signer() {
  const pair = await crypto.subtle.generateKey("Ed25519", true, ["sign", "verify"]);
  const privatePkcs8 = await crypto.subtle.exportKey("pkcs8", pair.privateKey);
  const publicSpki = await crypto.subtle.exportKey("spki", pair.publicKey);
  const privateB64 = encodeBase64Url(new Uint8Array(privatePkcs8));
  const publicB64 = encodeBase64Url(new Uint8Array(publicSpki));
  return {
    sign: (payload) => signReceiptPayload(payload, privateB64),
    publicB64,
  };
}

async function harness({ fetchOrigin, authenticate, start = T0 + 100 } = {}) {
  const storage = new FakeStorage();
  const store = new DurableAuthorizationStore(storage);
  initializeStore(store);
  const signer = await ed25519Signer();
  let fetchCalls = 0;
  const deps = {
    store,
    authenticate: authenticate ?? (async () => ({ claims: claims() })),
    fetchOrigin: async (...args) => {
      fetchCalls += 1;
      if (fetchOrigin) return fetchOrigin(...args);
      return new Response('{"data":{"productSearch":{"recordsFiltered":0}}}', {
        status: 200,
        headers: { "content-type": "application/json; charset=utf-8" },
      });
    },
    signReceipt: signer.sign,
    verifyReceipt: (payload, signature, keyId) => {
      if (keyId !== collector().signingKeyId) return false;
      return verifyReceiptSignature(payload, signature, signer.publicB64);
    },
    clock: clock(start),
    executionId: () => "cf-execution-test-001",
  };
  return { storage, store, signer, deps, fetchCalls: () => fetchCalls };
}

function input(origin, requestContext) {
  return {
    authorizationToken: "synthetic-token-never-sent",
    originUrl: origin.url,
    expectedGraphqlQuerySha256: origin.expectedGraphqlQuerySha256,
    requestContext,
    collector: collector(),
  };
}

test("success hace un único fetch, firma receipt y persiste replay exacto", async () => {
  const origin = await makeOrigin(1);
  const h = await harness();
  const first = await executeGatewayRequest(input(origin, context(origin, 1)), h.deps);
  assert.equal(first.decision, "ORIGIN_COMPLETED");
  assert.equal(first.replayed, false);
  assert.equal(h.fetchCalls(), 1);
  assert.equal(first.receiptPayload.canonical_request_sha256, origin.validated.canonicalRequestSha256);
  assert.equal(first.receiptPayload.request_digest, origin.validated.canonicalRequestSha256);
  assert.equal(first.receiptPayload.approved_commit_sha, SHA);
  assert.equal(first.receiptPayload.response_body_bytes, first.rawBody.byteLength);
  assert.equal(
    await verifyReceiptSignature(first.receiptPayload, first.signatureB64Url, h.signer.publicB64),
    true,
  );

  const replay = await executeGatewayRequest(input(origin, context(origin, 1)), h.deps);
  assert.equal(replay.decision, "REPLAY_COMPLETED");
  assert.equal(replay.replayed, true);
  assert.equal(h.fetchCalls(), 1);
  assert.deepEqual(replay.rawBody, first.rawBody);
  assert.equal(replay.signatureB64Url, first.signatureB64Url);
  assert.equal(replay.evidenceId, first.evidenceId);
});

test("request_digest distinto del GET validado falla antes de reservar o hacer fetch", async () => {
  const origin = await makeOrigin(1);
  const h = await harness();
  await assert.rejects(
    executeGatewayRequest(input(origin, context(origin, 1, { requestDigest: "f".repeat(64) })), h.deps),
    (error) => expectPolicyError(error, "request_digest_origin_mismatch"),
  );
  assert.equal(h.fetchCalls(), 0);
  assert.equal(h.store.summary(T0 + 1000).requestsUsed, 0);
});

test("fallo de identidad ocurre antes de reservar y nunca hace fetch", async () => {
  const origin = await makeOrigin(1);
  const h = await harness({
    authenticate: async () => {
      throw new EdgePolicyError("jwt_signature_invalid");
    },
  });
  await assert.rejects(
    executeGatewayRequest(input(origin, context(origin, 1)), h.deps),
    (error) => expectPolicyError(error, "jwt_signature_invalid"),
  );
  assert.equal(h.fetchCalls(), 0);
  assert.equal(h.store.summary(T0 + 1000).requestsUsed, 0);
});

test("OIDC de otro commit falla antes de tocar el presupuesto", async () => {
  const origin = await makeOrigin(1);
  const h = await harness({ authenticate: async () => ({ claims: { ...claims(), sha: "f".repeat(40) } }) });
  await assert.rejects(
    executeGatewayRequest(input(origin, context(origin, 1)), h.deps),
    (error) => expectPolicyError(error, "oidc_commit_context_mismatch"),
  );
  assert.equal(h.fetchCalls(), 0);
  assert.equal(h.store.summary(T0 + 1000).requestsUsed, 0);
});

test("single-flight retorna WAIT y no ejecuta segundo fetch después de vencer pacing", async () => {
  const firstOrigin = await makeOrigin(1);
  const secondOrigin = await makeOrigin(2);
  const h = await harness({ start: T0 + 5000 });
  h.store.reserve(context(firstOrigin, 1), T0 + 100);

  const result = await executeGatewayRequest(input(secondOrigin, context(secondOrigin, 2)), h.deps);
  assert.equal(result.decision, "WAIT");
  assert.equal(result.reason, "physical_request_in_flight");
  assert.equal(h.fetchCalls(), 0);
  assert.equal(h.store.summary(T0 + 5000).requestsUsed, 1);
});

test("redirect físico rechaza autorización y no persiste response replay", async () => {
  const origin = await makeOrigin(1);
  const h = await harness({
    fetchOrigin: async () => new Response(null, {
      status: 302,
      headers: { location: "https://example.com/" },
    }),
  });
  await assert.rejects(
    executeGatewayRequest(input(origin, context(origin, 1)), h.deps),
    (error) => expectPolicyError(error, "origin_redirect"),
  );
  assert.equal(h.fetchCalls(), 1);
  assert.equal(h.store.summary(T0 + 5000).state, "rejected");
});

test("429 físico consume el cupo reservado y rechaza autorización", async () => {
  const origin = await makeOrigin(1);
  const h = await harness({
    fetchOrigin: async () => new Response('{"error":"rate"}', {
      status: 429,
      headers: { "content-type": "application/json" },
    }),
  });
  await assert.rejects(
    executeGatewayRequest(input(origin, context(origin, 1)), h.deps),
    (error) => expectPolicyError(error, "origin_http_429"),
  );
  const summary = h.store.summary(T0 + 5000);
  assert.equal(summary.state, "rejected");
  assert.equal(summary.requestsUsed, 1);
});

test("HTML 200 se rechaza como contenido no JSON", async () => {
  const origin = await makeOrigin(1);
  const h = await harness({
    fetchOrigin: async () => new Response("<html>blocked</html>", {
      status: 200,
      headers: { "content-type": "text/html" },
    }),
  });
  await assert.rejects(
    executeGatewayRequest(input(origin, context(origin, 1)), h.deps),
    (error) => expectPolicyError(error, "origin_content_type_invalid"),
  );
  assert.equal(h.store.summary(T0 + 5000).state, "rejected");
});

test("Content-Length sobre el límite falla cerrado sin materializar body", async () => {
  const origin = await makeOrigin(1);
  const h = await harness({
    fetchOrigin: async () => new Response("{}", {
      status: 200,
      headers: {
        "content-type": "application/json",
        "content-length": String(MAX_REPLAY_BODY_BYTES + 1),
      },
    }),
  });
  await assert.rejects(
    executeGatewayRequest(input(origin, context(origin, 1)), h.deps),
    (error) => expectPolicyError(error, "origin_body_above_limit"),
  );
  assert.equal(h.store.summary(T0 + 5000).state, "rejected");
});

test("firma con formato inválido nunca completa ni devuelve evidencia", async () => {
  const origin = await makeOrigin(1);
  const h = await harness();
  h.deps.signReceipt = async () => "not+base64";
  await assert.rejects(
    executeGatewayRequest(input(origin, context(origin, 1)), h.deps),
    (error) => expectPolicyError(error, "receipt_signature_invalid"),
  );
  assert.equal(h.store.summary(T0 + 5000).state, "rejected");
});

test("firma bien formada pero criptográficamente inválida rechaza autorización", async () => {
  const origin = await makeOrigin(1);
  const h = await harness();
  h.deps.verifyReceipt = async () => false;
  await assert.rejects(
    executeGatewayRequest(input(origin, context(origin, 1)), h.deps),
    (error) => expectPolicyError(error, "receipt_signature_verification_failed"),
  );
  assert.equal(h.store.summary(T0 + 5000).state, "rejected");
});

test("corrupción estructural del replay persistido no provoca un refetch", async () => {
  const origin = await makeOrigin(1);
  const h = await harness();
  await executeGatewayRequest(input(origin, context(origin, 1)), h.deps);
  const key = "response:reservation-1";
  h.storage.data.get(key).rawResponseSha256 = "f".repeat(64);

  await assert.rejects(
    executeGatewayRequest(input(origin, context(origin, 1)), h.deps),
    (error) => expectPolicyError(error, "completed_replay_evidence_missing_or_corrupt"),
  );
  assert.equal(h.fetchCalls(), 1);
});

test("corrupción same-length de bytes persistidos se detecta criptográficamente sin refetch", async () => {
  const origin = await makeOrigin(1);
  const h = await harness();
  await executeGatewayRequest(input(origin, context(origin, 1)), h.deps);
  const key = "response:reservation-1";
  const envelope = h.storage.data.get(key);
  envelope.rawBody[0] ^= 1;

  await assert.rejects(
    executeGatewayRequest(input(origin, context(origin, 1)), h.deps),
    (error) => expectPolicyError(error, "replay_raw_body_hash_mismatch"),
  );
  assert.equal(h.fetchCalls(), 1);
});
