import assert from "node:assert/strict";
import test from "node:test";

import {
  EdgePolicyError,
  buildReceiptPayload,
  canonicalJson,
  decodeBase64Url,
  encodeBase64Url,
  receiptSigningBytes,
  sha256Hex,
  signReceiptPayload,
  validateLaColoniaGetUrl,
  verifyGitHubOidcToken,
  verifyReceiptSignature,
} from "../src/core.mjs";

const QUERY = "query productSearchV3 { productSearch { recordsFiltered } }";
const REPOSITORY = "jchernandez-portfolio/precios-supermercados-sps";
const REPOSITORY_ID = "1282475205";
const WORKFLOW_REF = `${REPOSITORY}/.github/workflows/precios-supermercados-sps-la-colonia-live.yml@refs/heads/main`;
const ENVIRONMENT = "la-colonia-live";
const SHA = "a".repeat(40);
const AUDIENCE = "https://precios-sps-provenance.invalid/github";

function expectPolicyError(error, code) {
  assert.ok(error instanceof EdgePolicyError);
  assert.equal(error.code, code);
  return true;
}

async function makeOriginUrl(overrides = {}) {
  const variables = {
    query: "supermercado",
    fullText: "",
    selectedFacets: [{ key: "category-1", value: "supermercado" }],
    orderBy: "OrderByNameASC",
    from: 0,
    to: 9,
    hideUnavailableItems: false,
    skusFilter: "ALL",
    ...(overrides.variables ?? {}),
  };
  const params = new URLSearchParams({
    workspace: "master",
    maxAge: "short",
    appsEtag: "remove",
    domain: "store",
    locale: "es-HN",
    operationName: "productSearchV3",
    query: overrides.query ?? QUERY,
    variables: JSON.stringify(variables),
  });
  return `https://www.lacolonia.com/_v/segment/graphql/v1?${params.toString()}`;
}

async function originPolicy() {
  return { expectedGraphqlQuerySha256: await sha256Hex(QUERY) };
}

function toB64UrlJson(value) {
  return encodeBase64Url(new TextEncoder().encode(JSON.stringify(value)));
}

async function makeRsaFixture(now = 2_000_000_000) {
  const pair = await crypto.subtle.generateKey(
    {
      name: "RSASSA-PKCS1-v1_5",
      modulusLength: 2048,
      publicExponent: new Uint8Array([1, 0, 1]),
      hash: "SHA-256",
    },
    true,
    ["sign", "verify"],
  );
  const publicJwk = await crypto.subtle.exportKey("jwk", pair.publicKey);
  Object.assign(publicJwk, { kid: "test-kid", alg: "RS256", use: "sig" });

  const claims = {
    iss: "https://token.actions.githubusercontent.com",
    aud: AUDIENCE,
    sub: `repo:${REPOSITORY}:environment:${ENVIRONMENT}`,
    repository: REPOSITORY,
    repository_id: REPOSITORY_ID,
    ref: "refs/heads/main",
    workflow_ref: WORKFLOW_REF,
    environment: ENVIRONMENT,
    event_name: "workflow_dispatch",
    sha: SHA,
    run_id: "32500000000",
    run_attempt: "1",
    jti: "jti-001",
    iat: now - 10,
    nbf: now - 10,
    exp: now + 290,
  };

  async function sign(overrides = {}, headerOverrides = {}) {
    const header = { alg: "RS256", typ: "JWT", kid: "test-kid", ...headerOverrides };
    const payload = { ...claims, ...overrides };
    const input = `${toB64UrlJson(header)}.${toB64UrlJson(payload)}`;
    const signature = await crypto.subtle.sign(
      "RSASSA-PKCS1-v1_5",
      pair.privateKey,
      new TextEncoder().encode(input),
    );
    return `${input}.${encodeBase64Url(new Uint8Array(signature))}`;
  }

  return {
    claims,
    jwks: { keys: [publicJwk] },
    sign,
    now,
  };
}

const oidcPolicy = {
  audience: AUDIENCE,
  repository: REPOSITORY,
  repositoryId: REPOSITORY_ID,
  ref: "refs/heads/main",
  workflowRef: WORKFLOW_REF,
  environment: ENVIRONMENT,
  eventName: "workflow_dispatch",
  sha: SHA,
  subject: `repo:${REPOSITORY}:environment:${ENVIRONMENT}`,
  clockSkewSeconds: 30,
  maxTokenAgeSeconds: 600,
};

test("canonicalJson ordena recursivamente y conserva unicode", () => {
  assert.equal(
    canonicalJson({ z: "á", a: { y: 2, x: 1 }, list: [{ b: 2, a: 1 }] }),
    '{"a":{"x":1,"y":2},"list":[{"a":1,"b":2}],"z":"á"}',
  );
});

test("canonicalJson rechaza float y undefined para evitar divergencia Python/JS", () => {
  assert.throws(() => canonicalJson({ value: 1.5 }), (error) => expectPolicyError(error, "canonical_non_integer"));
  assert.throws(() => canonicalJson({ value: undefined }), (error) => expectPolicyError(error, "canonical_undefined"));
});

test("base64url exige forma canónica sin padding", () => {
  const encoded = encodeBase64Url(new Uint8Array([0, 1, 2, 253, 254, 255]));
  assert.deepEqual([...decodeBase64Url(encoded)], [0, 1, 2, 253, 254, 255]);
  assert.throws(() => decodeBase64Url(`${encoded}=`), (error) => expectPolicyError(error, "invalid_base64url"));
});

test("valida GET canónico de La Colonia y liga el request exacto", async () => {
  const result = await validateLaColoniaGetUrl(await makeOriginUrl(), await originPolicy());
  assert.equal(result.from, 0);
  assert.equal(result.to, 9);
  assert.equal(result.orderBy, "OrderByNameASC");
  assert.equal(result.canonicalRequest.method, "GET");
  assert.match(result.canonicalRequestSha256, /^[0-9a-f]{64}$/);
});

test("rechaza host, path, puerto, credenciales y fragmento", async () => {
  const policy = await originPolicy();
  const valid = new URL(await makeOriginUrl());
  const cases = [
    [new URL(valid), "origin_host_mismatch", (url) => { url.hostname = "example.com"; }],
    [new URL(valid), "origin_path_mismatch", (url) => { url.pathname = "/other"; }],
    [new URL(valid), "origin_port_forbidden", (url) => { url.port = "8443"; }],
    [new URL(valid), "origin_credentials_forbidden", (url) => { url.username = "user"; }],
    [new URL(valid), "origin_fragment_forbidden", (url) => { url.hash = "x"; }],
  ];
  for (const [url, code, mutate] of cases) {
    mutate(url);
    await assert.rejects(validateLaColoniaGetUrl(url.toString(), policy), (error) => expectPolicyError(error, code));
  }
});

test("rechaza parámetros extra o duplicados", async () => {
  const policy = await originPolicy();
  const extra = new URL(await makeOriginUrl());
  extra.searchParams.set("evil", "1");
  await assert.rejects(validateLaColoniaGetUrl(extra.toString(), policy), (error) => expectPolicyError(error, "unexpected_query_parameters"));

  const duplicate = new URL(await makeOriginUrl());
  duplicate.searchParams.append("workspace", "master");
  await assert.rejects(validateLaColoniaGetUrl(duplicate.toString(), policy), (error) => expectPolicyError(error, "duplicate_query_parameter"));
});

test("rechaza cualquier GraphQL query que no coincida con el hash versionado", async () => {
  await assert.rejects(
    validateLaColoniaGetUrl(await makeOriginUrl({ query: `${QUERY} # changed` }), await originPolicy()),
    (error) => expectPolicyError(error, "graphql_query_mismatch"),
  );
});

test("variables GraphQL mantienen ALL, disponibilidad visible, order allowlist y página <= 50", async () => {
  const policy = await originPolicy();
  for (const [variables, code] of [
    [{ skusFilter: "FIRST_AVAILABLE" }, "skus_filter_must_be_all"],
    [{ hideUnavailableItems: true }, "hide_unavailable_must_be_false"],
    [{ orderBy: "Random" }, "order_by_not_allowed"],
    [{ to: 50 }, "page_size_above_limit"],
    [{ from: 10, to: 9 }, "range_invalid"],
  ]) {
    await assert.rejects(
      validateLaColoniaGetUrl(await makeOriginUrl({ variables }), policy),
      (error) => expectPolicyError(error, code),
    );
  }
});

test("modo fullText y modo facet no pueden mezclarse", async () => {
  const policy = await originPolicy();
  await assert.rejects(
    validateLaColoniaGetUrl(
      await makeOriginUrl({ variables: { fullText: "arroz", query: "supermercado" } }),
      policy,
    ),
    (error) => expectPolicyError(error, "full_text_mode_shape_mismatch"),
  );
  await assert.rejects(
    validateLaColoniaGetUrl(
      await makeOriginUrl({ variables: { fullText: "", query: "", selectedFacets: [] } }),
      policy,
    ),
    (error) => expectPolicyError(error, "facet_mode_shape_mismatch"),
  );
});

test("selectedFacets sólo acepta category-N y sin duplicados", async () => {
  const policy = await originPolicy();
  await assert.rejects(
    validateLaColoniaGetUrl(
      await makeOriginUrl({ variables: { selectedFacets: [{ key: "brand", value: "x" }] } }),
      policy,
    ),
    (error) => expectPolicyError(error, "invalid_selected_facet_key"),
  );
  await assert.rejects(
    validateLaColoniaGetUrl(
      await makeOriginUrl({ variables: { selectedFacets: [
        { key: "category-1", value: "supermercado" },
        { key: "category-1", value: "supermercado" },
      ] } }),
      policy,
    ),
    (error) => expectPolicyError(error, "duplicate_selected_facet"),
  );
});

test("OIDC válido verifica firma RSA y claims inmutables", async () => {
  const fixture = await makeRsaFixture();
  const token = await fixture.sign();
  const verified = await verifyGitHubOidcToken(token, fixture.jwks, oidcPolicy, fixture.now);
  assert.equal(verified.claims.repository_id, REPOSITORY_ID);
  assert.equal(verified.claims.environment, ENVIRONMENT);
});

test("OIDC rechaza token con firma alterada", async () => {
  const fixture = await makeRsaFixture();
  const token = await fixture.sign();
  const parts = token.split(".");
  const signature = decodeBase64Url(parts[2]);
  signature[0] ^= 1;
  const altered = `${parts[0]}.${parts[1]}.${encodeBase64Url(signature)}`;
  await assert.rejects(
    verifyGitHubOidcToken(altered, fixture.jwks, oidcPolicy, fixture.now),
    (error) => expectPolicyError(error, "jwt_signature_invalid"),
  );
});

test("OIDC rechaza repo id, ref, workflow, environment, event o SHA distintos", async () => {
  const fixture = await makeRsaFixture();
  const cases = [
    [{ repository_id: "999" }, "jwt_repository_id_mismatch"],
    [{ ref: "refs/heads/feature" }, "jwt_ref_mismatch"],
    [{ workflow_ref: `${REPOSITORY}/.github/workflows/other.yml@refs/heads/main` }, "jwt_workflow_ref_mismatch"],
    [{ environment: "other" }, "jwt_environment_mismatch"],
    [{ event_name: "pull_request" }, "jwt_event_mismatch"],
    [{ sha: "b".repeat(40) }, "jwt_sha_mismatch"],
  ];
  for (const [overrides, code] of cases) {
    const token = await fixture.sign(overrides);
    await assert.rejects(
      verifyGitHubOidcToken(token, fixture.jwks, oidcPolicy, fixture.now),
      (error) => expectPolicyError(error, code),
    );
  }
});

test("OIDC rechaza audience, issuer, algoritmo y kid no esperados", async () => {
  const fixture = await makeRsaFixture();
  await assert.rejects(
    verifyGitHubOidcToken(await fixture.sign({ aud: "other" }), fixture.jwks, oidcPolicy, fixture.now),
    (error) => expectPolicyError(error, "jwt_audience_mismatch"),
  );
  await assert.rejects(
    verifyGitHubOidcToken(await fixture.sign({ iss: "https://issuer.invalid" }), fixture.jwks, oidcPolicy, fixture.now),
    (error) => expectPolicyError(error, "jwt_issuer_mismatch"),
  );
  await assert.rejects(
    verifyGitHubOidcToken(await fixture.sign({}, { alg: "HS256" }), fixture.jwks, oidcPolicy, fixture.now),
    (error) => expectPolicyError(error, "jwt_alg_invalid"),
  );
  await assert.rejects(
    verifyGitHubOidcToken(await fixture.sign({}, { kid: "unknown" }), fixture.jwks, oidcPolicy, fixture.now),
    (error) => expectPolicyError(error, "jwks_kid_not_unique"),
  );
});

test("OIDC aplica exp, nbf, iat y edad máxima", async () => {
  const fixture = await makeRsaFixture();
  for (const [overrides, code] of [
    [{ exp: fixture.now - 31 }, "jwt_expired"],
    [{ nbf: fixture.now + 31 }, "jwt_not_yet_valid"],
    [{ iat: fixture.now + 31, exp: fixture.now + 300 }, "jwt_issued_in_future"],
    [{ iat: fixture.now - 700, nbf: fixture.now - 700, exp: fixture.now + 1 }, "jwt_too_old"],
  ]) {
    await assert.rejects(
      verifyGitHubOidcToken(await fixture.sign(overrides), fixture.jwks, oidcPolicy, fixture.now),
      (error) => expectPolicyError(error, code),
    );
  }
});

test("Ed25519 firma el payload canónico con domain separation", async () => {
  const pair = await crypto.subtle.generateKey({ name: "Ed25519" }, true, ["sign", "verify"]);
  const pkcs8 = await crypto.subtle.exportKey("pkcs8", pair.privateKey);
  const spki = await crypto.subtle.exportKey("spki", pair.publicKey);
  const privateKey = encodeBase64Url(new Uint8Array(pkcs8));
  const publicKey = encodeBase64Url(new Uint8Array(spki));
  const payload = { schema_version: "2", run_id: "run-1", response_status: 200 };
  const signature = await signReceiptPayload(payload, privateKey);
  assert.equal(await verifyReceiptSignature(payload, signature, publicKey), true);
  assert.equal(await verifyReceiptSignature({ ...payload, response_status: 201 }, signature, publicKey), false);
  assert.ok(receiptSigningBytes(payload).byteLength > canonicalJson(payload).length);
});

test("buildReceiptPayload liga OIDC, request físico y release del collector", async () => {
  const origin = await validateLaColoniaGetUrl(await makeOriginUrl(), await originPolicy());
  const fixture = await makeRsaFixture();
  const payload = buildReceiptPayload({
    requestContext: {
      authorizationId: "auth-001",
      nonce: "nonce-001",
      partitionId: "root",
      requestDigest: "f".repeat(64),
      requestId: "req-001",
      reservationId: "reservation-001",
      runId: "run-001",
      traversalId: "traversal-primary",
      traversalRole: "primary",
    },
    origin,
    response: { status: 200 },
    oidcClaims: fixture.claims,
    collector: {
      codeSha256: "e".repeat(64),
      execution: "cf-version-001",
      principal: "cloudflare:worker:precios-sps-provenance",
      releaseId: "release-001",
      signingKeyId: "edge-ed25519-v1",
    },
    physicalStartedAt: "2026-08-21T16:00:00Z",
    responseCompletedAt: "2026-08-21T16:00:01Z",
    rawResponseSha256: "d".repeat(64),
    responseBodyBytes: 1234,
  });
  assert.equal(payload.github_repository_id, REPOSITORY_ID);
  assert.equal(payload.http_method, "GET");
  assert.equal(payload.raw_response_sha256, "d".repeat(64));
  assert.equal(payload.collector_provider, "cloudflare_workers");
});
