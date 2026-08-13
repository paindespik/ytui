// E2E réel : Chromium + backend local + vraie vidéo YouTube.
// Playwright n'est pas une dépendance du dépôt : on le cherche là où il est
// déjà installé sur la machine (ou via PLAYWRIGHT_MODULE).
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
const ROUNDS = Number(process.env.ROUNDS || 3);

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

async function api(path, init = {}) {
  const r = await fetch(BASE + path, {
    ...init,
    headers: { Authorization: `Bearer ${TOKEN}`, "Content-Type": "application/json", ...(init.headers || {}) },
  });
  if (r.status === 204) return null;
  return { status: r.status, body: await r.json().catch(() => null) };
}

const results = [];
function check(name, ok, detail = "") {
  results.push({ name, ok, detail });
  console.log(`${ok ? "✅" : "❌"} ${name}${detail ? " — " + detail : ""}`);
}

const browser = await chromium.launch({
  executablePath: process.env.CHROMIUM || "/usr/bin/chromium",
  args: ["--autoplay-policy=no-user-gesture-required", "--no-sandbox", "--mute-audio"],
});
const ctx = await browser.newContext({ viewport: { width: 1280, height: 800 } });
await ctx.addCookies([{ name: "ytui_session", value: TOKEN, url: BASE }]);
const page = await ctx.newPage();
// Réseau lent optionnel : c'est là que dash.js prend son temps entre le
// manifeste et le premier segment — la fenêtre où la reprise se perdait.
if (process.env.SLOW) {
  const cdp = await ctx.newCDPSession(page);
  await cdp.send("Network.enable");
  await cdp.send("Network.emulateNetworkConditions", {
    offline: false,
    latency: Number(process.env.LATENCY || 600),
    downloadThroughput: (Number(process.env.KBPS || 900) * 1024) / 8,
    uploadThroughput: (200 * 1024) / 8,
  });
}
const errors = [];
page.on("pageerror", (e) => errors.push("pageerror: " + e.message));
page.on("console", (m) => {
  const t = m.text();
  // 409 = « compte YouTube non connecté » (le bouton J'aime) : attendu sur une
  // instance jetable sans cookies, sans rapport avec la lecture.
  if (m.type() === "error" && !t.includes("409")) errors.push("console: " + t);
});

// Codecs disponibles ?
await page.goto(BASE, { waitUntil: "domcontentloaded" });
// Plafond de qualité bas : sous réseau bridé, un 1440p ne démarrerait jamais.
await page.evaluate(() => localStorage.setItem("ytui.max_height", "360"));
const h264 = await page.evaluate(() =>
  window.MediaSource ? MediaSource.isTypeSupported('video/mp4; codecs="avc1.4d4020"') : false,
);
check("chromium supporte H.264/MSE", h264);
if (!h264) {
  await browser.close();
  process.exit(1);
}

async function openWatch() {
  await page.goto(`${BASE}/#/watch/youtube/${VIDEO}`, { waitUntil: "domcontentloaded" });
  await page.waitForSelector("video", { timeout: 20000 });
}

async function waitPlaying(timeout = Number(process.env.PLAY_TIMEOUT || 45000)) {
  const t0 = Date.now();
  while (Date.now() - t0 < timeout) {
    const s = await page.evaluate(() => {
      const v = document.querySelector("video");
      if (!v) return null;
      return { t: v.currentTime, ready: v.readyState, paused: v.paused, dur: v.duration };
    });
    if (s && s.ready >= 2 && s.t > 0) return s;
    await sleep(500);
  }
  return null;
}

// ─── Manche 1..N : poser une position, rouvrir, vérifier la reprise ───
const targets = [300, 120, 480, 42, 240].slice(0, ROUNDS);
for (const [i, target] of targets.entries()) {
  // Reprise « propre » : on écrit la position côté serveur comme l'aurait fait
  // une lecture partielle, après avoir enregistré la vidéo dans l'historique.
  await openWatch();
  const started = await waitPlaying();
  check(`manche ${i + 1} : la lecture démarre`, !!started, started ? `t=${started.t.toFixed(1)}s dur=${started.dur?.toFixed(0)}s` : "aucune lecture");
  if (!started) break;

  // Seek utilisateur, puis on laisse la pulsation enregistrer.
  await page.evaluate((t) => {
    document.querySelector("video").currentTime = t;
  }, target);
  await sleep(12000); // pulsation = 10 s
  const saved = await api(`/api/history/${VIDEO}/resume`);
  const savedPos = saved?.body?.position ?? -1;
  check(
    `manche ${i + 1} : la position ${target}s est enregistrée`,
    Math.abs(savedPos - target) < 20,
    `serveur=${savedPos.toFixed?.(1)}`,
  );

  // Quitter la page (démontage du lecteur) puis rouvrir : c'est le chemin qui
  // échouait de façon intermittente sur dash.js.
  await page.goto(`${BASE}/#/`, { waitUntil: "domcontentloaded" });
  await sleep(1500);
  await openWatch();
  const resumed = await waitPlaying();
  const at = resumed?.t ?? -1;
  check(
    `manche ${i + 1} : reprise à ~${target}s`,
    at >= target - 10 && at <= target + 30,
    `currentTime=${at.toFixed?.(1)}s`,
  );

  // La reprise ne doit pas être écrasée par une pulsation postérieure.
  await sleep(12000);
  const after = await api(`/api/history/${VIDEO}/resume`);
  const afterPos = after?.body?.position ?? -1;
  check(
    `manche ${i + 1} : la position n'est pas écrasée par ~0`,
    afterPos >= target - 10,
    `serveur=${afterPos.toFixed?.(1)}`,
  );
}

check("aucune erreur JS dans la page", errors.length === 0, errors.slice(0, 5).join(" | "));

await browser.close();
const failed = results.filter((r) => !r.ok);
console.log(`\n${results.length - failed.length}/${results.length} vérifications OK`);
process.exit(failed.length ? 1 : 0);
