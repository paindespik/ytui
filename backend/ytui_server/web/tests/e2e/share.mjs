// E2E réel : lien de partage ytui (/s/{token}) ouvert par un navigateur SANS
// session — lecture, cloisonnement (aucun appel /api), qualité, révocation.
// Même backend jetable que resume.mjs (cf. README.md).
const CANDIDATES = [
  process.env.PLAYWRIGHT_MODULE,
  "playwright",
  "/home/sean/ilemarine/node_modules/playwright/index.mjs",
].filter(Boolean);

let chromium = null;
for (const mod of CANDIDATES) {
  try {
    ({ chromium } = await import(mod));
    break;
  } catch {
    /* candidat suivant */
  }
}
if (!chromium) {
  console.error("Playwright introuvable — export PLAYWRIGHT_MODULE=/chemin/vers/playwright");
  process.exit(2);
}

const BASE = process.env.YTUI_BASE || "http://127.0.0.1:8791";
const TOKEN = process.env.YTUI_TOKEN || "e2e-token";
const VIDEO = process.env.YTUI_VIDEO || "aqz-KE-bpKQ";
const PLATFORM = process.env.YTUI_PLATFORM || "youtube";

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

async function api(path, init = {}) {
  const r = await fetch(BASE + path, {
    ...init,
    headers: { Authorization: `Bearer ${TOKEN}`, "Content-Type": "application/json", ...(init.headers || {}) },
  });
  if (r.status === 204) return { status: 204, body: null };
  return { status: r.status, body: await r.json().catch(() => null) };
}

const results = [];
function check(name, ok, detail = "") {
  results.push({ name, ok });
  console.log(`${ok ? "✅" : "❌"} ${name}${detail ? " — " + detail : ""}`);
}

const created = await api("/api/shares", {
  method: "POST",
  body: JSON.stringify({ video: { video_id: VIDEO, title: "", platform: PLATFORM } }),
});
if (created.status !== 200) {
  console.error("création du lien impossible", created);
  process.exit(1);
}
const share = created.body;
console.log("lien :", BASE + share.path);

const browser = await chromium.launch({
  executablePath: process.env.CHROMIUM || "/usr/bin/chromium",
  args: ["--autoplay-policy=no-user-gesture-required", "--no-sandbox", "--mute-audio"],
});
// Contexte vierge : aucun cookie de session, comme un destinataire.
const ctx = await browser.newContext({ viewport: { width: 1280, height: 800 } });
const page = await ctx.newPage();
const requests = [];
const problems = [];
page.on("request", (r) => requests.push(new URL(r.url())));
page.on("console", (m) => {
  // Les 403 relayés de googlevideo (URL tuées en cours de lecture, cf.
  // README) sont du bruit réseau géré par le lecteur, pas une erreur de page.
  if (m.type() === "error" && !m.text().startsWith("Failed to load resource")) {
    problems.push(m.text());
  }
});
page.on("pageerror", (e) => problems.push(String(e)));

const timeOf = () => page.evaluate(() => document.getElementById("video").currentTime);
const heightOf = () => page.evaluate(() => document.getElementById("video").videoHeight);
async function waitPlaying(min = 2, timeout = 60_000) {
  const start = Date.now();
  while (Date.now() - start < timeout) {
    if ((await timeOf()) >= min) return true;
    await sleep(500);
  }
  return false;
}

try {
  await page.goto(BASE + share.path);
  const playing = await waitPlaying(3);
  check("la vidéo démarre sans session", playing, `t=${(await timeOf()).toFixed(1)}s, ${await heightOf()}p`);
  check(
    "titre résolu affiché",
    (await page.textContent("#title")).trim().length > 0,
    (await page.textContent("#title")).trim(),
  );

  const sameOrigin = requests.filter((u) => u.origin === new URL(BASE).origin);
  const apiCalls = sameOrigin.filter((u) => u.pathname.startsWith("/api/"));
  check("aucun appel /api depuis la page", apiCalls.length === 0, apiCalls.map((u) => u.pathname).join(", "));
  check(
    "médias servis par /s/{token}/…",
    sameOrigin.some((u) => u.pathname.startsWith(`${share.path}/`)),
  );

  const status = await page.evaluate(async () => {
    const r = await fetch("/api/proxy?url=" + encodeURIComponent("https://i.ytimg.com/vi/x/hq.jpg"));
    return r.status;
  });
  check("le relais général /api/proxy reste fermé", status === 401, `HTTP ${status}`);

  if (PLATFORM === "youtube") {
    const before = await timeOf();
    await page.selectOption("#quality", "480");
    await sleep(1000);
    const resumed = await waitPlaying(before - 1, 60_000);
    await sleep(3000);
    const h = await heightOf();
    check("changement de qualité : 480p max, position conservée", resumed && h > 0 && h <= 480, `${h}p, t=${(await timeOf()).toFixed(1)}s (avant ${before.toFixed(1)}s)`);
  }

  check("aucune erreur JS / CSP", problems.length === 0, problems.slice(0, 3).join(" | "));

  const revoked = await api(`/api/shares/${share.token}`, { method: "DELETE" });
  check("révocation", revoked.status === 204);
  await page.reload();
  check("lien révoqué → page « plus valable »", (await page.textContent("h1")).includes("plus valable"));
} finally {
  await browser.close();
}

const failed = results.filter((r) => !r.ok).length;
console.log(failed ? `\n${failed} échec(s)` : "\nTout est vert");
process.exit(failed ? 1 : 0);
