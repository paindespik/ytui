// Batterie sur la vue lecture (views/watch.js) : cycle de vie du chat en
// direct, lecture auto, panneau « À suivre » et bascule « direct » déclarée par
// le serveur. Ce sont les chemins où une fuite (minuteur qui survit à la
// navigation) ou un mauvais état (direct pris pour une VOD) se paient cher.
//
// Lancement : node --test "backend/ytui_server/web/tests/*.test.mjs"

import { test, describe, beforeEach, afterEach } from "node:test";
import assert from "node:assert/strict";

import { installDom, mockApi, flush, makeVideo } from "./harness.mjs";

let watch;
let queue;

const STREAMS_VOD = {
  kind: "progressive",
  url: "https://cdn.example/video.mp4",
  title: "Une vidéo",
  duration: 600,
  subtitles: [],
};

/// Routes minimales d'une page lecture. `lives` et `chat` varient par test.
function routes({
  lives = [],
  livesDelay = 0,
  related = [],
  streams = STREAMS_VOD,
  chat,
  details = null,
} = {}) {
  return [
    ...(details ? [{ match: /\/api\/videos\/[^/?]+\?/, method: "GET", json: details }] : []),
    { match: "/api/lives", json: lives, delay: livesDelay },
    { match: "/chat", json: chat || { messages: [], cursor: 1, active: true } },
    { match: "/related", json: { items: related } },
    { match: "/rating", json: { rating: "none" } },
    { match: /\/api\/history\/[^/]+\/resume/, method: "GET", status: 404, json: {} },
    { match: /\/position/, method: "PUT", status: 204 },
    { match: "/api/history", method: "POST", status: 204 },
    { match: "/mpd", status: 404, json: {} },
    { match: "/streams", json: streams },
    { match: "/sponsor", json: { segments: [] } },
  ];
}

const mounted = [];

async function mount(view, { platform = "youtube", id = "abc" } = {}) {
  const cleanup = await watch.render(view, {
    params: { platform, id },
    query: new URLSearchParams(),
  });
  mounted.push(cleanup);
  return cleanup;
}

const mediaEl = (view) => view.querySelectorAll("video")[0];
const textOf = (node) =>
  node.children
    .map((c) => (typeof c === "string" ? c : c.textContent || textOf(c)))
    .join(" ");

describe("vue lecture — cycle de vie et lecture auto", () => {
  beforeEach(async () => {
    installDom();
    globalThis.localStorage.clear();
    ({ queue } = await import("../js/queue.js"));
    queue.items = [];
    queue.index = 0;
    watch = await import("../views/watch.js");
    // Enchaînement immédiat par défaut ; les tests du compte à rebours le
    // règlent eux-mêmes.
    watch.upNextConfig.seconds = 0;
    globalThis.localStorage.removeItem("ytui.cinema");
    // Le harness partage #view entre les tests (installDom est idempotent) :
    // sans ce vidage, mediaEl() renverrait le <video> d'un montage précédent.
    document.getElementById("view").replaceChildren();
  });

  afterEach(async () => {
    for (const c of mounted.splice(0)) {
      try {
        c();
      } catch {
        /* déjà démonté par le test */
      }
    }
    await flush();
  });

  // ─── Chat : aucune fuite de minuteur ───

  test("vue démontée pendant /api/lives → le chat ne démarre jamais", async () => {
    const api = mockApi(routes({ lives: [], livesDelay: 30 }));
    const view = document.getElementById("view");
    const cleanup = await mount(view);

    cleanup(); // navigation pendant que /api/lives est encore en vol
    await flush(8);
    assert.equal(api.find("/chat").length, 0, "aucun sondage de chat après démontage");
  });

  test("un direct confirmé sonde le chat, et le démontage l'arrête", async () => {
    const live = { video: makeVideo("abc", { platform: "youtube" }) };
    const api = mockApi(routes({ lives: [live] }));
    const view = document.getElementById("view");
    const cleanup = await mount(view);
    await flush(6);

    const during = api.find("/chat").length;
    assert.ok(during >= 1, "le chat a bien démarré");

    cleanup();
    await flush(6);
    assert.equal(api.find("/chat").length, during, "plus aucun tick après démontage");
  });

  test("le curseur du chat ne recule jamais", async () => {
    const live = { video: makeVideo("abc", { platform: "youtube" }) };
    const api = mockApi([
      // 2ᵉ page sans curseur : rejouer depuis 0 doublonnerait les messages.
      { match: "/chat", json: () => ({ messages: [], cursor: 0, active: true }) },
      ...routes({ lives: [live] }),
    ]);
    api.routes.unshift({
      match: "/chat",
      json: { messages: [{ author: "a", text: "salut" }], cursor: 12, active: true },
    });
    const view = document.getElementById("view");
    await mount(view);
    await flush(6);

    const calls = api.find("/chat");
    assert.ok(calls.length >= 1);
    for (const c of calls.slice(1)) {
      assert.ok(c.url.includes("cursor=12"), `curseur régressé : ${c.url}`);
    }
  });

  // ─── Lecture auto ───

  test("lecture auto désactivée : la fin d'une vidéo n'avance pas la file", async () => {
    mockApi(routes());
    globalThis.localStorage.setItem("ytui.autoplay", "false");
    queue.play([makeVideo("abc"), makeVideo("def")], 0);
    const view = document.getElementById("view");
    await mount(view);
    await flush(4);

    const el = mediaEl(view);
    el.emitLoadedMetadata({ duration: 600 });
    el.currentTime = 600;
    el.emitEnded();
    await flush(4);

    assert.equal(queue.index, 0, "la lecture s'arrête en fin de vidéo");
  });

  test("lecture auto activée (défaut) : la file avance", async () => {
    mockApi(routes());
    queue.play([makeVideo("abc"), makeVideo("def")], 0);
    const view = document.getElementById("view");
    await mount(view);
    await flush(4);

    const el = mediaEl(view);
    el.emitLoadedMetadata({ duration: 600 });
    el.currentTime = 600;
    el.emitEnded();
    await flush(4);

    assert.equal(queue.index, 1);
  });

  // ─── « À suivre » et état direct ───

  test("aucune suggestion → message explicite dans le panneau", async () => {
    mockApi(routes({ related: [] }));
    const view = document.getElementById("view");
    await mount(view);
    await flush(6);

    assert.ok(textOf(view).includes("Aucune suggestion"), textOf(view).slice(0, 200));
  });

  test("is_live renvoyé par /streams : badge EN DIRECT et pas d'enchaînement", async () => {
    mockApi(
      routes({
        streams: { ...STREAMS_VOD, kind: "progressive", is_live: true },
        related: [makeVideo("zzz")],
      }),
    );
    queue.play([makeVideo("abc"), makeVideo("def")], 0);
    const view = document.getElementById("view");
    await mount(view);
    await flush(6);

    assert.ok(textOf(view).includes("EN DIRECT"), "badge direct affiché");

    const el = mediaEl(view);
    el.emitEnded();
    await flush(4);
    assert.equal(queue.index, 0, "un direct terminé n'enchaîne pas sur une suggestion");
  });

  // ─── Barre d'actions / panneau « Lecture » ───

  test("file vide : la section « File d'attente » est masquée", async () => {
    mockApi(routes());
    const view = document.getElementById("view");
    await mount(view);
    await flush(4);
    assert.ok(!textOf(view).includes("File d'attente vide"));
    const queueSection = findText(view, "File d'attente");
    assert.equal(queueSection?.parentNode?.hidden, true);
  });

  test("la bascule « Lecture auto » du panneau écrit la préférence", async () => {
    mockApi(routes());
    const view = document.getElementById("view");
    await mount(view);
    await flush(4);
    const toggle = findByAttr(view, "aria-label", "Lecture auto");
    assert.equal(toggle.checked, true, "défaut : activée");
    toggle.checked = false;
    toggle.dispatchEvent(new Event("change"));
    assert.equal(globalThis.localStorage.getItem("ytui.autoplay"), "false");
  });

  test("le panneau se ferme au clic extérieur, et plus d'écouteur après démontage", async () => {
    mockApi(routes());
    const view = document.getElementById("view");
    const cleanup = await mount(view);
    await flush(4);
    const btn = findByAttr(view, "aria-haspopup", "dialog");
    const panel = findByAttr(view, "aria-label", "Réglages de lecture");
    btn.dispatchEvent(Object.assign(new Event("click"), { stopPropagation() {} }));
    assert.equal(panel.hidden, false);
    document.dispatchEvent(new Event("click"));
    assert.equal(panel.hidden, true);

    btn.dispatchEvent(Object.assign(new Event("click"), { stopPropagation() {} }));
    cleanup();
    document.dispatchEvent(new Event("click"));
    assert.equal(panel.hidden, false, "l'écouteur document a été retiré au démontage");
  });

  // ─── Lot 3 : « À suivre », compte à rebours, description, cinéma ───

  test("clic sur « À suivre » : lecture immédiate, le reste de la file reste à venir", async () => {
    const zzz = makeVideo("zzz");
    mockApi(routes({ related: [zzz] }));
    queue.play([makeVideo("abc"), makeVideo("def")], 0);
    const view = document.getElementById("view");
    await mount(view);
    await flush(6);
    const row = findRowFor(view, "zzz");
    row.dispatchEvent(new Event("click"));
    assert.deepEqual(
      queue.items.map((v) => v.video_id),
      ["abc", "zzz", "def"],
    );
    assert.equal(queue.index, 1);
  });

  test("bouton + de « À suivre » : ajout en fin de file, lecture inchangée", async () => {
    mockApi(routes({ related: [makeVideo("zzz")] }));
    queue.play([makeVideo("abc")], 0);
    const view = document.getElementById("view");
    await mount(view);
    await flush(6);
    const add = findByAttr(findRowFor(view, "zzz"), "title", "Ajouter à la file d'attente");
    add.dispatchEvent(Object.assign(new Event("click"), { stopPropagation() {} }));
    assert.deepEqual(queue.items.map((v) => v.video_id), ["abc", "zzz"]);
    assert.equal(queue.index, 0);
  });

  test("fin de vidéo : « Suivant dans N s » puis enchaînement", async () => {
    watch.upNextConfig.seconds = 0.05;
    mockApi(routes());
    queue.play([makeVideo("abc"), makeVideo("def")], 0);
    const view = document.getElementById("view");
    await mount(view);
    await flush(4);
    mediaEl(view).emitEnded();
    await flush(2);
    assert.ok(textOf(view).includes("Suivant dans"), "écran d'attente affiché");
    assert.equal(queue.index, 0, "pas encore enchaîné");
    await new Promise((r) => setTimeout(r, 120));
    assert.equal(queue.index, 1);
  });

  test("« Annuler » arrête l'enchaînement", async () => {
    watch.upNextConfig.seconds = 0.05;
    mockApi(routes());
    queue.play([makeVideo("abc"), makeVideo("def")], 0);
    const view = document.getElementById("view");
    await mount(view);
    await flush(4);
    mediaEl(view).emitEnded();
    await flush(2);
    findText(view, "Annuler").dispatchEvent(new Event("click"));
    await new Promise((r) => setTimeout(r, 120));
    assert.equal(queue.index, 0);
    assert.ok(!textOf(view).includes("Suivant dans"));
  });

  test("démontage pendant le compte à rebours : aucun enchaînement", async () => {
    watch.upNextConfig.seconds = 0.05;
    mockApi(routes());
    queue.play([makeVideo("abc"), makeVideo("def")], 0);
    const view = document.getElementById("view");
    const cleanup = await mount(view);
    await flush(4);
    mediaEl(view).emitEnded();
    await flush(2);
    cleanup();
    await new Promise((r) => setTimeout(r, 120));
    assert.equal(queue.index, 0);
  });

  test("description : un horodatage cliquable déplace la lecture", async () => {
    mockApi(
      routes({
        details: {
          video_id: "abc",
          title: "Une vidéo",
          channel_id: "UCchan",
          channel_title: "Chaîne",
          description: "Sommaire\n1:05 le vif du sujet\nhttps://example.org/source",
          upload_date: "2026-09-20",
          like_count: 1200,
        },
      }),
    );
    const view = document.getElementById("view");
    await mount(view);
    await flush(8);
    const el = mediaEl(view);
    el.emitLoadedMetadata({ duration: 600 });
    const ts = findByAttr(view, "title", "Aller à 1:05");
    assert.ok(ts, "horodatage rendu en lien");
    ts.dispatchEvent(Object.assign(new Event("click"), { preventDefault() {}, stopPropagation() {} }));
    assert.equal(el.currentTime, 65);
    assert.ok(findByAttr(view, "href", "https://example.org/source"), "lien rendu");
  });

  test("lien nu : la chaîne vient des détails (lien + bouton Suivre)", async () => {
    mockApi(
      routes({
        details: { video_id: "abc", channel_id: "UCchan", channel_title: "Chaîne", description: "" },
      }),
    );
    const view = document.getElementById("view");
    await mount(view);
    await flush(8);
    assert.equal(queue.current.channel_id, "UCchan");
    assert.ok(findByAttr(view, "href", "#/channel/youtube/UCchan?title=Cha%C3%AEne"));
  });

  test("mode cinéma : bascule persistée", async () => {
    mockApi(routes());
    const view = document.getElementById("view");
    await mount(view);
    await flush(4);
    const btn = findByAttr(view, "title", "Mode cinéma (t)");
    btn.dispatchEvent(new Event("click"));
    assert.equal(globalThis.localStorage.getItem("ytui.cinema"), "true");
    assert.equal(btn.getAttribute("aria-pressed"), "true");
  });
});

/// Premier noeud dont le texte propre vaut `text`.
function findText(root, text) {
  for (const c of root.children || []) {
    if (typeof c !== "object") continue;
    if (c.textContent === text) return c;
    const hit = findText(c, text);
    if (hit) return hit;
  }
  return null;
}

/// Premier noeud portant l'attribut `name` = `value`.
function findByAttr(root, name, value) {
  for (const c of root.children || []) {
    if (typeof c !== "object") continue;
    if (c.getAttribute && c.getAttribute(name) === value) return c;
    const hit = findByAttr(c, name, value);
    if (hit) return hit;
  }
  return null;
}

/// Ligne du panneau latéral portant la vidéo `id`.
function findRowFor(root, id) {
  for (const c of root.children || []) {
    if (typeof c !== "object") continue;
    if (c._video && c._video.video_id === id) return c;
    const hit = findRowFor(c, id);
    if (hit) return hit;
  }
  return null;
}
