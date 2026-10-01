import assert from "node:assert/strict";
import test from "node:test";

import { encodeBase64Url } from "../src/core.mjs";
import { executeStructuralGatewayRequest } from "../src/structural-gateway-runtime.mjs";
import { validateLaColoniaStructuralGetUrl } from "../src/structural-request.mjs";
import { STRUCTURAL_GRAPHQL_QUERY_SHA256_BY_KIND } from "../src/worker-policy.mjs";

const ROOT_QUERY = `query FacetDiscoveryRootTotal(
  $query: String
  $fullText: String
  $selectedFacets: [SelectedFacetInput]
  $from: Int
  $to: Int
) {
  productSearch(
    query: $query
    fullText: $fullText
    selectedFacets: $selectedFacets
    from: $from
    to: $to
    hideUnavailableItems: false
  ) @context(provider: "vtex.search-graphql") {
    recordsFiltered
  }
}`;

function rootUrl() {
  const params = new URLSearchParams();
  params.set("workspace", "master");
  params.set("maxAge", "short");
  params.set("appsEtag", "remove");
  params.set("domain", "store");
  params.set("locale", "es-HN");
  params.set("operationName", "FacetDiscoveryRootTotal");
  params.set("query", ROOT_QUERY);
  params.set("variables", JSON.stringify({ query: "", fullText: "", selectedFacets: [], from: 0, to: 0 }));
  return `https://www.lacolonia.com/_v/segment/graphql/v1?${params.toString()}`;
}

function claims() {
  return {
    sha: "a".repeat(40),
    repository: "jchernandez-portfolio/precios-supermercados-sps",
    repository_id: "1282475205",
    ref: "refs/heads/main",
    workflow_ref: "jchernandez-portfolio/precios-supermercados-sps/.github/workflows/precios-supermercados-sps-la-colonia-live.yml@refs/heads/main",
    environment: "la-colonia-live",
    run_id: "32530000000",
    run_attempt: "1",
    sub: "repo:jchernandez-portfolio/precios-supermercados-sps:environment:la-colonia-live",
    jti: "jti-structural-runtime-001",
  };
}

async function input(overrides = {}) {
  const originUrl = rootUrl();
  const validated = await validateLaColoniaStructuralGetUrl(originUrl, {
    expectedQuerySha256ByKind: STRUCTURAL_GRAPHQL_QUERY_SHA256_BY_KIND,
  });
  return {
    authorizationToken: "verified",
    originUrl,
    requestContext: {
      authorizationId: "auth-structural-001",
      runId: "32530000000:1",
      approvedCommitSha: "a".repeat(40),
      reservationId: "reservation-structural-001",
      requestId: "request-structural-001",
      requestDigest: validated.canonicalRequestSha256,
      nonce: "nonce-structural-001",
      requestKind: "root_total",
      ...overrides,
    },
    collector: {
      principal: "cloudflare-worker:precios-sps-provenance",
      releaseId: "release-structural-001",
      codeSha256: "d".repeat(64),
      signingKeyId: "cloudflare-ed25519-v1",
    },
  };
}

function clockSequence(...dates) {
  let index = 0;
  return () => dates[Math.min(index++, dates.length - 1)];
}

function fakeStore() {
  return {
    reservations: [],
    completed: [],
    failed: [],
    replayEnvelope: null,
    reserve(context, nowMs) {
      this.reservations.push({ context: structuredClone(context), nowMs });
      if (this.replayEnvelope) {
        return {
          decision: "REPLAY_COMPLETED",
          replayEnvelope: structuredClone(this.replayEnvelope),
        };
      }
      return {
        decision: "RESERVED",
        reservation: { physicalStartMs: nowMs },
      };
    },
    async complete(reservationId, envelope, nowMs) {
      this.completed.push({ reservationId, envelope: structuredClone(envelope), nowMs });
      this.replayEnvelope = structuredClone(envelope);
    },
    fail(reservationId, reason, nowMs) {
      this.failed.push({ reservationId, reason, nowMs });
    },
  };
}

function dependencies(store, { fetchOrigin } = {}) {
  const signature = encodeBase64Url(new Uint8Array(64).fill(7));
  return {
    store,
    authenticate: async () => ({ claims: claims() }),
    fetchOrigin: fetchOrigin ?? (async () => new Response(
      JSON.stringify({ data: { productSearch: { recordsFiltered: 321 } } }),
      { status: 200, headers: { "content-type": "application/json" } },
    )),
    signReceipt: async () => signature,
    verifyReceipt: async (_payload, value, keyId) => value === signature && keyId === "cloudflare-ed25519-v1",
    clock: clockSequence(
      new Date("2026-08-21T20:30:00.000Z"),
      new Date("2026-08-21T20:30:00.000Z"),
      new Date("2026-08-21T20:30:00.100Z"),
    ),
    executionId: () => "execution-structural-001",
  };
}

test("root_total usa el mismo ledger sin exponer traversal al caller", async () => {
  const store = fakeStore();
  const result = await executeStructuralGatewayRequest(await input(), dependencies(store));
  assert.equal(result.decision, "ORIGIN_COMPLETED");
  assert.equal(result.receiptPayload.request_kind, "root_total");
  assert.equal(result.receiptPayload.graphql_query_sha256, STRUCTURAL_GRAPHQL_QUERY_SHA256_BY_KIND.root_total);
  assert.equal(result.receiptPayload.response_status, 200);
  assert.equal(store.reservations.length, 1);
  assert.equal(store.reservations[0].context.traversalRole, "primary");
  assert.equal(store.reservations[0].context.traversalId, "structural-discovery");
  assert.equal(store.reservations[0].context.partitionId, "structural:root_total");
  assert.equal(store.completed.length, 1);
  assert.match(result.evidenceId, /^[0-9a-f]{64}$/u);
});

test("requestKind no puede reetiquetar el URL raíz", async () => {
  const store = fakeStore();
  await assert.rejects(
    async () => executeStructuralGatewayRequest(await input({ requestKind: "category_tree" }), dependencies(store)),
    (error) => error?.code === "structural_request_kind_origin_mismatch",
  );
  assert.equal(store.reservations.length, 0);
});

test("digest incorrecto falla antes de reservar o hacer fetch", async () => {
  const store = fakeStore();
  let fetches = 0;
  await assert.rejects(
    async () => executeStructuralGatewayRequest(
      await input({ requestDigest: "f".repeat(64) }),
      dependencies(store, { fetchOrigin: async () => { fetches += 1; throw new Error("unexpected"); } }),
    ),
    (error) => error?.code === "request_digest_origin_mismatch",
  );
  assert.equal(store.reservations.length, 0);
  assert.equal(fetches, 0);
});

test("replay verifica hash, firma, kind y evidence id sin refetch", async () => {
  const store = fakeStore();
  const first = await executeStructuralGatewayRequest(await input(), dependencies(store));
  let fetches = 0;
  const replayDeps = dependencies(store, {
    fetchOrigin: async () => { fetches += 1; throw new Error("replay no debe abrir red"); },
  });
  const replay = await executeStructuralGatewayRequest(await input(), replayDeps);
  assert.equal(replay.decision, "REPLAY_COMPLETED");
  assert.equal(replay.evidenceId, first.evidenceId);
  assert.equal(fetches, 0);
});

test("body replay alterado falla sin refetch", async () => {
  const store = fakeStore();
  await executeStructuralGatewayRequest(await input(), dependencies(store));
  store.replayEnvelope.rawBody = new TextEncoder().encode('{"tampered":true}');
  let fetches = 0;
  await assert.rejects(
    async () => executeStructuralGatewayRequest(
      await input(),
      dependencies(store, { fetchOrigin: async () => { fetches += 1; throw new Error("unexpected"); } }),
    ),
    (error) => error?.code === "replay_receipt_hash_mismatch",
  );
  assert.equal(fetches, 0);
});

test("HTTP no 200 cierra reserva y no firma evidencia", async () => {
  const store = fakeStore();
  await assert.rejects(
    async () => executeStructuralGatewayRequest(
      await input(),
      dependencies(store, {
        fetchOrigin: async () => new Response("blocked", { status: 429, headers: { "content-type": "text/plain" } }),
      }),
    ),
    (error) => error?.code === "origin_http_429",
  );
  assert.equal(store.completed.length, 0);
  assert.equal(store.failed.at(-1)?.reason, "origin_http_429");
});
