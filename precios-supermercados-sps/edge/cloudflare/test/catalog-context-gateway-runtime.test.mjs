import assert from "node:assert/strict";
import test from "node:test";

import {
  canonicalBytes,
  encodeBase64Url,
  sha256Hex,
  validateLaColoniaGetUrl,
} from "../src/core.mjs";
import { executeContextBoundCatalogRequest } from "../src/catalog-context-gateway-runtime.mjs";

const QUERY = "query productSearchV3 { productSearch { recordsFiltered } }";
const SHA = "a".repeat(40);
const RUN_ID = "32540000000:1";
const RAW_REGION = "synthetic-sps-region-runtime";
const SIGNATURE = encodeBase64Url(new Uint8Array(64).fill(7));

async function origin() {
  const variables = {
    query: "supermercado",
    fullText: "",
    selectedFacets: [{ key: "category-1", value: "supermercado" }],
    orderBy: "OrderByNameASC",
    from: 0,
    to: 9,
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
    run_id: "32540000000",
    run_attempt: "1",
    sub: "repo:jchernandez-portfolio/precios-supermercados-sps:environment:la-colonia-live",
    jti: "jti-catalog-context-runtime-001",
  };
}

async function locationContext(originUrl, overrides = {}) {
  const contextFingerprint = await sha256Hex(canonicalBytes(RAW_REGION));
  const wireKey = "X-VTEX-Region";
  const headers = { [wireKey]: RAW_REGION };
  const wireRequestFingerprint = await sha256Hex(canonicalBytes({
    method: "GET",
    url: originUrl,
    headers,
  }));
  return {
    locationId: "la_colonia_sps",
    bindingSourceKey: `request:regionid:sha256:${contextFingerprint}`,
    bindingEvidence: `location_binding_radiography:sha256:${"c".repeat(64)}`,
    contextFingerprint,
    placement: "header",
    wireKey,
    valuePath: [],
    wireRequestFingerprint,
    rawValue: RAW_REGION,
    ...overrides,
  };
}

async function input(overrides = {}) {
  const source = await origin();
  return {
    authorizationToken: "verified-by-test",
    originUrl: source.url,
    expectedGraphqlQuerySha256: source.expectedGraphqlQuerySha256,
    requestContext: {
      authorizationId: "auth-catalog-context-001",
      runId: RUN_ID,
      approvedCommitSha: SHA,
      reservationId: "reservation-catalog-001",
      requestId: "request-catalog-001",
      requestDigest: source.validated.canonicalRequestSha256,
      nonce: "nonce-catalog-001",
      traversalRole: "primary",
      traversalId: "traversal-primary-catalog",
      partitionId: "supermercado",
    },
    locationContext: await locationContext(source.url),
    collector: {
      principal: "cloudflare-worker:precios-sps-provenance",
      releaseId: "release-catalog-context-001",
      codeSha256: "d".repeat(64),
      signingKeyId: "cloudflare-ed25519-v1",
    },
    ...overrides,
  };
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
        return { decision: "REPLAY_COMPLETED", replayEnvelope: structuredClone(this.replayEnvelope) };
      }
      return { decision: "RESERVED", reservation: { physicalStartMs: nowMs } };
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

function clockSequence(...dates) {
  let index = 0;
  return () => dates[Math.min(index++, dates.length - 1)];
}

function dependencies(store, { fetchOrigin } = {}) {
  return {
    store,
    authenticate: async () => ({ claims: claims() }),
    fetchOrigin: fetchOrigin ?? (async () => new Response(
      JSON.stringify({ data: { productSearch: { recordsFiltered: 10 } } }),
      { status: 200, headers: { "content-type": "application/json" } },
    )),
    signReceipt: async () => SIGNATURE,
    verifyReceipt: async (_payload, signature, keyId) => (
      signature === SIGNATURE && keyId === "cloudflare-ed25519-v1"
    ),
    clock: clockSequence(
      new Date("2026-08-24T15:20:00.000Z"),
      new Date("2026-08-24T15:20:00.123Z"),
      new Date("2026-08-24T15:20:00.456Z"),
    ),
    executionId: () => "execution-catalog-context-001",
  };
}

test("catálogo contextual firma receipt v3 canónico y no filtra raw regionId", async () => {
  const request = await input();
  const store = fakeStore();
  const fetches = [];
  const deps = dependencies(store, {
    fetchOrigin: async (url, init) => {
      fetches.push({ url, init });
      return new Response(JSON.stringify({ data: { productSearch: { recordsFiltered: 10 } } }), {
        status: 200,
        headers: { "content-type": "application/json" },
      });
    },
  });

  const result = await executeContextBoundCatalogRequest(request, deps);

  assert.equal(result.decision, "ORIGIN_COMPLETED");
  assert.equal(result.receiptPayload.schema_version, "3");
  assert.equal(result.receiptPayload.location_id, "la_colonia_sps");
  assert.equal(result.receiptPayload.context_fingerprint, request.locationContext.contextFingerprint);
  assert.equal(result.receiptPayload.context_placement, "header");
  assert.equal(result.receiptPayload.context_wire_key, "X-VTEX-Region");
  assert.equal(result.receiptPayload.physical_started_at_utc, "2026-08-24T15:20:00.123000Z");
  assert.equal(result.receiptPayload.response_completed_at_utc, "2026-08-24T15:20:00.456000Z");
  assert.equal(fetches.length, 1);
  assert.equal(fetches[0].url, request.originUrl);
  assert.equal(fetches[0].init.headers["X-VTEX-Region"], RAW_REGION);
  assert.equal(JSON.stringify(result.receiptPayload).includes(RAW_REGION), false);
  assert.equal(store.completed.length, 1);
});

test("replay contextual valida evidencia y no vuelve a abrir red", async () => {
  const request = await input();
  const store = fakeStore();
  const first = await executeContextBoundCatalogRequest(request, dependencies(store));
  let fetches = 0;
  const replay = await executeContextBoundCatalogRequest(
    request,
    dependencies(store, {
      fetchOrigin: async () => {
        fetches += 1;
        throw new Error("replay no debe hacer fetch");
      },
    }),
  );

  assert.equal(replay.decision, "REPLAY_COMPLETED");
  assert.equal(replay.evidenceId, first.evidenceId);
  assert.equal(fetches, 0);
});

test("raw regionId alterado falla antes de reservar o hacer fetch", async () => {
  const request = await input();
  request.locationContext = { ...request.locationContext, rawValue: "tampered" };
  const store = fakeStore();
  let fetches = 0;

  await assert.rejects(
    executeContextBoundCatalogRequest(
      request,
      dependencies(store, { fetchOrigin: async () => { fetches += 1; throw new Error("unexpected"); } }),
    ),
    (error) => error?.code === "catalog_context_raw_fingerprint_mismatch",
  );
  assert.equal(store.reservations.length, 0);
  assert.equal(fetches, 0);
});

test("receipt v3 replay alterado en ubicación se rechaza sin refetch", async () => {
  const request = await input();
  const store = fakeStore();
  await executeContextBoundCatalogRequest(request, dependencies(store));
  store.replayEnvelope.receiptPayload.location_id = "la_colonia_tgu";
  let fetches = 0;

  await assert.rejects(
    executeContextBoundCatalogRequest(
      request,
      dependencies(store, { fetchOrigin: async () => { fetches += 1; throw new Error("unexpected"); } }),
    ),
    (error) => error?.code === "replay_catalog_location_id_mismatch",
  );
  assert.equal(fetches, 0);
});
