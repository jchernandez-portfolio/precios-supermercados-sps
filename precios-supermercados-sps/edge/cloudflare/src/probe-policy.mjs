export const CONTROLLED_PROBE_PURPOSE = "precios-sps-controlled-origin-probe-v1";
export const CONTROLLED_PROBE_SCHEMA_VERSION = "probe-1";
export const CONTROLLED_PROBE_RECEIPT_SIGNATURE_DOMAIN =
  "precios-sps/cloudflare-controlled-origin-probe-receipt/v1\0";

export const CONTROLLED_PROBE_ROUTE = "/v1/probe";
export const CONTROLLED_PROBE_ORIGIN_PATH = "/v1/probe-origin";
export const CONTROLLED_PROBE_SPAN_NAME = "precios_sps.cloudflare.controlled_origin_probe";
export const CONTROLLED_PROBE_OIDC_AUDIENCE = "urn:precios-sps:cloudflare:probe:v1";
export const CONTROLLED_PROBE_SIGNING_KEY_ID = "cloudflare-probe-ed25519-v1";
export const CONTROLLED_PROBE_MAX_BODY_BYTES = 32 * 1024;
export const CONTROLLED_PROBE_MAX_REQUEST_BODY_BYTES = 16 * 1024;

export const CONTROLLED_PROBE_WORKER_POLICY = Object.freeze({
  repository: "jchernandez-portfolio/precios-supermercados-sps",
  repositoryId: "1282475205",
  ref: "refs/heads/main",
  workflowRef:
    "jchernandez-portfolio/precios-supermercados-sps/.github/workflows/precios-supermercados-sps-cloudflare-probe.yml@refs/heads/main",
  environment: "cloudflare-probe",
  eventName: "workflow_dispatch",
  subject: "repo:jchernandez-portfolio/precios-supermercados-sps:environment:cloudflare-probe",
  audience: CONTROLLED_PROBE_OIDC_AUDIENCE,
  clockSkewSeconds: 30,
  maxTokenAgeSeconds: 600,
});
