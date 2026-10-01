import assert from "node:assert/strict";
import test from "node:test";

import { canonicalBytes, encodeBase64Url, sha256Hex } from "../src/core.mjs";
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
    sha: "a".repeat(40), repository: "jchernandez-portfolio/precios-supermercados-sps", repository_id: "1282475205",
    ref: "refs/heads/main", workflow_ref: "repo/workflow@refs/heads/main", environment: "la-colonia-live",
    run_id: "32530000000", run_attempt: "1", sub: "repo:jchernandez-portfolio/precios-supermercados-sps:environment:la-colonia-live",
    jti: "jti-context-001",
  };
}

function store() {
  return {
    completed: [], failed: [],
    reserve(_context, nowMs) { return { decision: "RESERVED", reservation: { physicalStartMs: nowMs } }; },
    async complete(id, envelope) { this.completed.push({ id, envelope }); },
    fail(id, reason) { this.failed.push({ id, reason }); },
  };
}

async function locationContext(originUrl, rawValue, placement = "query", wireKey = "regionId") {
  const contextFingerprint = await sha256Hex(canonicalBytes(rawValue));
  let wireUrl = originUrl;
  const headers = {};
  if (placement === "query") {
    const parsed = new URL(originUrl);
    parsed.searchParams.append(wireKey, rawValue);
    wireUrl = parsed.toString();
  } else {
    headers[wireKey] = rawValue;
  }
  const wireRequestFingerprint = await sha256Hex(canonicalBytes({ method: "GET", url: wireUrl, headers }));
  return {
    locationId: "la_colonia_sps",
    bindingSourceKey: `request:regionid:sha256:${contextFingerprint}`,
    bindingEvidence: `location_binding_radiography:sha256:${"8".repeat(64)}`,
    contextFingerprint,
    placement,
    wireKey,
    valuePath: [],
    wireRequestFingerprint,
    rawValue,
  };
}

async function structuralInput(location) {
  const originUrl = rootUrl();
  const validated = await validateLaColoniaStructuralGetUrl(originUrl, {
    expectedQuerySha256ByKind: STRUCTURAL_GRAPHQL_QUERY_SHA256_BY_KIND,
  });
  return {
    authorizationToken: "verified",
    originUrl,
    locationContext: location,
    requestContext: {
      authorizationId: "auth-context-001", runId: "32530000000:1", approvedCommitSha: "a".repeat(40),
      reservationId: "reservation-context-001", requestId: "request-context-001",
      requestDigest: validated.canonicalRequestSha256, nonce: "nonce-context-001", requestKind: "root_total",
    },
    collector: {
      principal: "cloudflare-worker:precios-sps-provenance", releaseId: "release-context-001",
      codeSha256: "d".repeat(64), signingKeyId: "cloudflare-ed25519-v1",
    },
  };
}

function deps(state, fetchOrigin) {
  const signature = encodeBase64Url(new Uint8Array(64).fill(9));
  const times = [
    new Date("2026-08-24T02:00:00.000Z"),
    new Date("2026-08-24T02:00:00.000Z"),
    new Date("2026-08-24T02:00:00.100Z"),
  ];
  let index = 0;
  return {
    store: state,
    authenticate: async () => ({ claims: claims() }),
    fetchOrigin,
    signReceipt: async () => signature,
    verifyReceipt: async () => true,
    clock: () => times[Math.min(index++, times.length - 1)],
    executionId: () => "execution-context-001",
  };
}

test("query SPS se aplica físicamente y queda firmado sin raw region", async () => {
  const originUrl = rootUrl();
  const raw = "opaque-sps-region-test";
  const location = await locationContext(originUrl, raw);
  const state = store();
  let fetchedUrl = null;
  const result = await executeStructuralGatewayRequest(
    await structuralInput(location),
    deps(state, async (url, options) => {
      fetchedUrl = url;
      assert.equal(options.headers.accept, "application/json");
      return new Response(JSON.stringify({ data: { productSearch: { recordsFiltered: 12 } } }), {
        status: 200, headers: { "content-type": "application/json" },
      });
    }),
  );
  assert.ok(fetchedUrl.includes(`regionId=${raw}`));
  assert.equal(result.receiptPayload.schema_version, "2");
  assert.equal(result.receiptPayload.location_id, "la_colonia_sps");
  assert.equal(result.receiptPayload.context_fingerprint, location.contextFingerprint);
  assert.equal(result.receiptPayload.wire_request_fingerprint, location.wireRequestFingerprint);
  assert.equal(JSON.stringify(result.receiptPayload).includes(raw), false);
});

test("header SPS llega al fetch y raw alterado falla antes de reservar", async () => {
  const originUrl = rootUrl();
  const raw = "opaque-sps-header-test";
  const location = await locationContext(originUrl, raw, "header", "X-VTEX-Region");
  const state = store();
  let observedHeader = null;
  const result = await executeStructuralGatewayRequest(
    await structuralInput(location),
    deps(state, async (_url, options) => {
      observedHeader = options.headers["X-VTEX-Region"];
      return new Response(JSON.stringify({ data: { productSearch: { recordsFiltered: 8 } } }), {
        status: 200, headers: { "content-type": "application/json" },
      });
    }),
  );
  assert.equal(observedHeader, raw);
  assert.equal(result.receiptPayload.context_placement, "header");

  const tampered = { ...location, rawValue: "different-region" };
  const blocked = store();
  await assert.rejects(
    async () => executeStructuralGatewayRequest(
      await structuralInput(tampered),
      deps(blocked, async () => { throw new Error("no fetch"); }),
    ),
    (error) => error?.code === "structural_context_raw_fingerprint_mismatch",
  );
  assert.equal(blocked.completed.length, 0);
});
