// E2E réel : enchaînement d'une file de lecture (playlist) dans le navigateur.
// Vérifie qu'aucune vidéo n'est sautée (double `ended`) et que chaque titre
// reprend là où il en était.
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
const IDS = (process.env.IDS || "jNQXAC9IVRw,dQw4w9WgXcQ,9bZkp7q19f0").split(",");

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const results = [];
function check(name, ok, detail = "") {
  results.push({ name, ok });
  console.log(`${ok ? "✅" : "❌"} ${name}${detail ? " — " + detail : ""}`);
}
async function api(path, init = {}) {
  const r = await fetch(BASE + path, {
    ...init,
    headers: { Authorization: `Bearer ${TOKEN}`, "Content-Type": "application/json", ...(init.headers || {}) },
  });
  return r.status === 204 ? null : { status: r.status, body: await r.json().catch(() => null) };
}

const browser = await chromium.launch({
  executablePath: process.env.CHROMIUM || "/usr/bin/chromium",
  args: ["--autoplay-policy=no-user-gesture-required", "--no-sandbox", "--mute-audio"],
});
const ctx = await browser.newContext({ viewport: { width: 1280, height: 800 } });
await ctx.addCookies([{ name: "ytui_session", value: TOKEN, url: BASE }]);
const page = await ctx.newPage();
const errors = [];
page.on("pageerror", (e) => errors.push("pageerror: " + e.message));
page.on("console", (m) => {
  const t = m.text();
  if (m.type() === "error" && !t.includes("409")) errors.push(t);
});

if (process.env.SLOW) {
  const cdp = await ctx.newCDPSession(page);
  await cdp.send("Network.enable");
  await cdp.send("Network.emulateNetworkConditions", {
    offline: false,
    latency: Number(process.env.LATENCY || 700),
    downloadThroughput: (Number(process.env.KBPS || 3000) * 1024) / 8,
    uploadThroughput: (200 * 1024) / 8,
  });
}

await page.goto(BASE, { waitUntil: "domcontentloaded" });
await page.evaluate(() => localStorage.setItem("ytui.max_height", "360"));

// Chaque avance de file est journalisée : c'est ainsi qu'on détecte un saut.
await page.evaluate(async (ids) => {
  const { queue } = await import("/js/queue.js");
  window.__visited = [];
  queue.addEventListener("change", () => {
    const c = queue.current;
    const last = window.__visited[window.__visited.length - 1];
    if (c && (!last || last.id !== c.video_id)) {
      window.__visited.push({ id: c.video_id, index: queue.index });
    }
  });
  queue.play(
    ids.map((id) => ({
      video_id: id,
      platform: "youtube",
      title: id,
      channel_title: "",
      channel_id: "",
      thumbnail_url: "",
      kind: "video",
      duration: null,
      published: null,
      playlist_id: "",
    })),
  );
  location.hash = "#/watch/youtube/" + ids[0];
}, IDS);

async function currentState() {
  return page.evaluate(() => {
    const v = document.querySelector("video");
    return {
      hash: location.hash,
      t: v ? v.currentTime : -1,
      dur: v ? v.duration : -1,
      ready: v ? v.readyState : -1,
      visited: window.__visited,
    };
  });
}

async function waitPlaying(timeout = 90000) {
  const t0 = Date.now();
  while (Date.now() - t0 < timeout) {
    const s = await currentState();
    if (s.ready >= 2 && s.t > 0 && Number.isFinite(s.dur)) return s;
    await sleep(400);
  }
  return null;
}

// ─── Parcours de la file : chaque vidéo doit enchaîner sur la suivante ───
for (const [i, id] of IDS.entries()) {
  const s = await waitPlaying();
  check(`vidéo ${i + 1}/${IDS.length} (${id}) démarre`, !!s && s.hash.includes(id), s ? `hash=${s.hash} dur=${s.dur?.toFixed(0)}s` : "pas de lecture");
  if (!s) break;

  // Sauter près de la fin pour déclencher la fin de lecture naturellement.
  await page.evaluate(() => {
    const v = document.querySelector("video");
    v.currentTime = Math.max(0, v.duration - 4);
  });

  if (i < IDS.length - 1) {
    const next = IDS[i + 1];
    const t0 = Date.now();
    let advanced = false;
    while (Date.now() - t0 < 60000) {
      const st = await currentState();
      if (st.hash.includes(next)) {
        advanced = true;
        break;
      }
      await sleep(400);
    }
    check(`la file avance vers la vidéo ${i + 2}`, advanced);
    if (!advanced) break;
  } else {
    await sleep(8000); // fin de file : autoplay d'une suggestion
  }
}

const final = await currentState();
const visitedIds = (final.visited || []).map((v) => v.id);
// Aucun titre sauté : les 3 vidéos, dans l'ordre, une seule fois chacune.
check(
  "aucune vidéo sautée dans la file",
  IDS.every((id, i) => visitedIds[i] === id),
  `parcours=${visitedIds.join(" → ")}`,
);
check(
  "aucune double avance",
  new Set(visitedIds.slice(0, IDS.length)).size === IDS.length,
  `parcours=${visitedIds.join(" → ")}`,
);

// Chaque vidéo lue jusqu'au bout doit être marquée comme terminée
// (position ≥ 95 % → la prochaine ouverture repart du début).
for (const id of IDS.slice(0, -1)) {
  const r = await api(`/api/history/${id}/resume`);
  const pos = r?.body?.position ?? -1;
  const dur = r?.body?.duration ?? 0;
  check(
    `${id} est marquée comme vue en entier`,
    dur > 0 && pos / dur >= 0.9,
    `position=${pos?.toFixed?.(1)}/${dur}`,
  );
}

check("aucune erreur JS dans la page", errors.length === 0, errors.slice(0, 4).join(" | "));

await browser.close();
const failed = results.filter((r) => !r.ok);
console.log(`\n${results.length - failed.length}/${results.length} vérifications OK`);
process.exit(failed.length ? 1 : 0);
