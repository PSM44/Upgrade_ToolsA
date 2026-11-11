// Diagnostic import hook: records only header presence and SHA-256 fingerprints.
// It never emits header values, bodies, or response payloads.
import { createHash } from "node:crypto";

const originalFetch = globalThis.fetch;
const fp = (value) => value ? createHash("sha256").update(String(value)).digest("hex") : null;

globalThis.fetch = async function tracedFetch(input, init = {}) {
  const headers = new Headers(init.headers || {});
  const authorization = headers.get("authorization");
  const cliToken = headers.get("x-omniroute-cli-token");
  const url = typeof input === "string" ? input : input?.url;
  const response = await originalFetch(input, init);
  process.stderr.write(`[northstar-auth-trace] ${JSON.stringify({
    url,
    method: init.method || "GET",
    status: response.status,
    authorization_present: Boolean(authorization),
    authorization_sha256: fp(authorization),
    cli_token_present: Boolean(cliToken),
    cli_token_sha256: fp(cliToken),
  })}\n`);
  return response;
};
