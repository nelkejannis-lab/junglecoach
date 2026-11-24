// The data crawler (GitHub Actions in nelkejannis-lab/junglecoach-data) gets the server's Riot key here,
// so the admin only updates the key once in the app. No shared secret: the run proves who it is with
// GitHub's OIDC token (RS256, signed by GitHub), and only the crawl workflow on main is accepted.
const ISS = "https://token.actions.githubusercontent.com";
const REPO = "nelkejannis-lab/junglecoach-data";
const WORKFLOW = `${REPO}/.github/workflows/crawl.yml@refs/heads/main`;
export const AUD = "moosi.app";

const b64 = (s) => Uint8Array.from(atob(s.replace(/-/g, "+").replace(/_/g, "/") + "===".slice((s.length + 3) % 4)), (c) => c.charCodeAt(0));
let jwks = { keys: null, at: 0 };

export async function verifyGithubOidc(token, now = Date.now() / 1000, fetchKeys = null) {
  const parts = String(token || "").split(".");
  if (parts.length !== 3) return null;
  let head, pl;
  try { head = JSON.parse(new TextDecoder().decode(b64(parts[0]))); pl = JSON.parse(new TextDecoder().decode(b64(parts[1]))); } catch { return null; }
  if (head.alg !== "RS256") return null;
  if (!jwks.keys || Date.now() - jwks.at > 3600e3 || !jwks.keys.some((k) => k.kid === head.kid)) {
    jwks = { keys: fetchKeys ? await fetchKeys() : (await (await fetch(`${ISS}/.well-known/jwks`)).json()).keys, at: Date.now() };
  }
  const jwk = (jwks.keys || []).find((k) => k.kid === head.kid);
  if (!jwk) return null;
  const key = await crypto.subtle.importKey("jwk", { kty: "RSA", n: jwk.n, e: jwk.e, alg: "RS256", ext: true },
    { name: "RSASSA-PKCS1-v1_5", hash: "SHA-256" }, false, ["verify"]);
  const ok = await crypto.subtle.verify("RSASSA-PKCS1-v1_5", key, b64(parts[2]), new TextEncoder().encode(parts[0] + "." + parts[1]));
  if (!ok) return null;
  if (pl.iss !== ISS || pl.aud !== AUD || !(pl.exp > now) || (pl.nbf && pl.nbf > now + 60)) return null;
  if (pl.repository !== REPO || pl.ref !== "refs/heads/main" || pl.workflow_ref !== WORKFLOW) return null;
  if (!["schedule", "workflow_dispatch"].includes(pl.event_name)) return null;
  return pl;
}

export function resetJwks() { jwks = { keys: null, at: 0 }; }
