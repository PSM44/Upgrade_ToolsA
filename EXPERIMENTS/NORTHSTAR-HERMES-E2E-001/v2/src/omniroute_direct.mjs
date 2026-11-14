#!/usr/bin/env node
// Direct OmniRoute OpenAI-compatible client. Credentials stay inside the
// broker process and are never serialized in requests, receipts or logs.
import fs from "node:fs";
import os from "node:os";

function parseValue(value) {
  const v = value.trim();
  if ((v.startsWith('"') && v.endsWith('"')) || (v.startsWith("'") && v.endsWith("'"))) return v.slice(1, -1);
  return v;
}
function loadEnv(path) {
  if (!fs.existsSync(path)) return;
  for (const line of fs.readFileSync(path, "utf8").split("\n")) {
    const text = line.trim();
    if (!text || text.startsWith("#")) continue;
    const split = text.indexOf("=");
    if (split > 0) {
      const key = text.slice(0, split).trim();
      if (process.env[key] === undefined) process.env[key] = parseValue(text.slice(split + 1));
    }
  }
}

loadEnv(`${os.homedir()}/.omniroute/.env`);
loadEnv("/usr/local/lib/node_modules/omniroute/.env");
const { apiFetch } = await import("/usr/local/lib/node_modules/omniroute/bin/cli/api.mjs");

let input = "";
for await (const chunk of process.stdin) input += chunk;
const request = JSON.parse(input);
const timeout = Math.max(1000, Math.min(Number(request.timeout_ms || 60000), 300000));

let path;
let init = { timeout, retry: false, acceptNotOk: true };
if (request.operation === "models") {
  path = "/v1/models";
} else if (request.operation === "chat") {
  path = "/v1/chat/completions";
  init = {
    ...init,
    method: "POST",
    idempotencyKey: request.request_id,
    body: {
      model: request.model,
      messages: request.messages,
      max_tokens: request.max_tokens || 256,
      temperature: request.temperature ?? 0,
      stream: false,
      usage: { include: true },
    },
  };
} else {
  throw new Error("unsupported operation");
}

const response = await apiFetch(path, init);
const data = await response.json().catch(async () => ({ error: { message: (await response.text()).slice(0, 500) } }));
if (!response.ok) {
  const detail = data?.error?.message || data?.message || `HTTP ${response.status}`;
  process.stdout.write(JSON.stringify({ ok: false, status: response.status, error: String(detail).slice(0, 500) }));
  process.exit(2);
}
if (request.operation === "models") {
  const models = Array.isArray(data?.data) ? data.data.map((x) => ({ id: x.id, owned_by: x.owned_by ?? null })) : [];
  process.stdout.write(JSON.stringify({ ok: true, status: response.status, models }));
} else {
  process.stdout.write(JSON.stringify({ ok: true, status: response.status, data }));
}
