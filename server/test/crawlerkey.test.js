import test from "node:test";
import assert from "node:assert/strict";
import { verifyGithubOidc, resetJwks } from "../src/crawlerkey.js";

const enc = (o) => Buffer.from(JSON.stringify(o)).toString("base64url");
test("crawler key: only the crawl workflow of the data repo on main", async () => {
  const kp = await crypto.subtle.generateKey({ name: "RSASSA-PKCS1-v1_5", modulusLength: 2048, publicExponent: new Uint8Array([1, 0, 1]), hash: "SHA-256" }, true, ["sign", "verify"]);
  const pub = { ...(await crypto.subtle.exportKey("jwk", kp.publicKey)), kid: "k1" };
  const now = Math.floor(Date.now() / 1000);
  const good = { iss: "https://token.actions.githubusercontent.com", aud: "moosi.app", exp: now + 300, repository: "nelkejannis-lab/junglecoach-data",
    ref: "refs/heads/main", workflow_ref: "nelkejannis-lab/junglecoach-data/.github/workflows/crawl.yml@refs/heads/main", event_name: "schedule" };
  const sign = async (pl) => { const h = enc({ alg: "RS256", kid: "k1" }) + "." + enc(pl);
    return h + "." + Buffer.from(await crypto.subtle.sign("RSASSA-PKCS1-v1_5", kp.privateKey, new TextEncoder().encode(h))).toString("base64url"); };
  const keys = async () => [pub];
  resetJwks();
  assert.ok(await verifyGithubOidc(await sign(good), now, keys));
  for (const bad of [{ repository: "evil/junglecoach-data" }, { aud: "x" }, { exp: now - 1 }, { ref: "refs/heads/dev" }, { event_name: "pull_request" },
    { workflow_ref: "nelkejannis-lab/junglecoach-data/.github/workflows/other.yml@refs/heads/main" }])
    assert.equal(await verifyGithubOidc(await sign({ ...good, ...bad }), now, keys), null);
  const t = await sign(good);
  assert.equal(await verifyGithubOidc(t.slice(0, -4) + "AAAA", now, keys), null);   // tampered signature
});
