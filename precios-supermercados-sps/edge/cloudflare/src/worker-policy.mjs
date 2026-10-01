export const FIXED_GRAPHQL_QUERY_SHA256 = "72576e0296de646a532e197886f2468f6a87234964109e982ca27af4ba5c1663";

export const STRUCTURAL_GRAPHQL_QUERY_SHA256_BY_KIND = Object.freeze({
  root_total: "00441ce39ffbb02803351b96826fb86feafad3b3870137f01f074b11260e8163",
  category_tree: "0a9265b63af869850fac217238fc82aaa3b9fa396ca77f35ee98679e4bb066cb",
});

export const GITHUB_OIDC_JWKS_URL = "https://token.actions.githubusercontent.com/.well-known/jwks";
export const GITHUB_OIDC_AUDIENCE = "urn:precios-sps:cloudflare:collector:v1";

export const WORKER_POLICY = Object.freeze({
  repository: "jchernandez-portfolio/precios-supermercados-sps",
  repositoryId: "1282475205",
  ref: "refs/heads/main",
  workflowRef: "jchernandez-portfolio/precios-supermercados-sps/.github/workflows/precios-supermercados-sps-la-colonia-live.yml@refs/heads/main",
  environment: "la-colonia-live",
  eventName: "workflow_dispatch",
  subject: "repo:jchernandez-portfolio/precios-supermercados-sps:environment:la-colonia-live",
  audience: GITHUB_OIDC_AUDIENCE,
  clockSkewSeconds: 30,
  maxTokenAgeSeconds: 600,
});

export const WORKER_ROUTES = Object.freeze({
  initialize: "/v1/initialize",
  execute: "/v1/execute",
  catalogExecute: "/v1/catalog-execute",
  structuralExecute: "/v1/structural-execute",
});

export const AUTHORIZATION_LIMITS = Object.freeze({
  minStartIntervalMs: 1500,
  maxLifetimeMs: 45 * 60 * 1000,
  maxRequests: 1000,
  maxRequestBodyBytes: 64 * 1024,
  maxJwksBodyBytes: 128 * 1024,
});

export const RECEIPT_SIGNING_KEY_ID = "cloudflare-ed25519-v1";
export const JWKS_CACHE_TTL_MS = 5 * 60 * 1000;
