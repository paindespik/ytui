// Lecteur public des liens de partage (js/share.js) : choix et repli des
// sources, messages d'erreur, reprise locale, qualité, sous-titres, SponsorBlock.

import { test, describe, beforeEach, afterEach } from "node:test";
import assert from "node:assert/strict";

import {
  installDom,
  mockApi,
  fakeDashjs,
  fakeHls,
  fakeMpegts,
  FakeMediaElement,
  FakeNode,
} from "./harness.mjs";

let env;
let mod;
const players = [];

const TOKEN = "tok_123";
const SIGNED = `/s/${TOKEN}/p?u=https%3A%2F%2Fgv.example%2Fv&e=1&s=sig`;
const tick = () => new Promise((r) => setTimeout(r, 0));

function makeUi() {
  return {
    video: new FakeMediaElement("video"),
    message: new FakeNode("div"),
    title: new FakeNode("h1"),
    quality: new FakeNode("select"),
    served: new FakeNode("span"),
    subs: new FakeNode("select"),
    subsLabel: new FakeNode("label"),
    skip: new FakeNode("span"),
  };
}

function makePlayer({ platform = "youtube", ui = makeUi() } = {}) {
  const loads = [];
  const player = new mod.SharePlayer({
    token: TOKEN,
    platform,
    ui,
    storage: globalThis.localStorage,
    load: async (src) => {
      loads.push(src);
    },
  });
  players.push(player);
  return { player, ui, loads };
}

function streams(sources, extra = {}) {
  return { title: "Titre résolu", is_live: false, sources, subtitles: [], ...extra };
}

describe("lien de partage — lecteur public", () => {
  beforeEach(async () => {
    env = installDom();
    globalThis.localStorage.clear();
    mod = await import("../js/share.js");
  });

  afterEach(() => {
    for (const p of players.splice(0)) p.destroy();
    delete globalThis.window.dashjs;
    delete globalThis.window.Hls;
    delete globalThis.window.mpegts;
  });

  test("YouTube : MPD disponible → dash.js sur l'URL absolue, plafond 1080p par défaut", async () => {
    const dash = fakeDashjs();
    globalThis.window.dashjs = dash;
    const api = mockApi([
      { match: "/mpd", status: 200, json: {} },
      {
        match: "/streams",
        json: streams([
          { engine: "dash", url: `/s/${TOKEN}/mpd?max_height=1080` },
          { engine: "progressive", url: SIGNED },
        ]),
      },
      { match: "/sponsor", json: { segments: [] } },
    ]);
    const { player, ui, loads } = makePlayer();
    await player.start();
    const req = api.find("/streams")[0];
    assert.match(req.url, new RegExp(`^/s/${TOKEN}/streams\\?`));
    assert.match(req.url, /max_height=1080/);
    assert.match(req.url, /sub_langs=fr%2Cen/);
    assert.deepEqual(loads, ["/vendor/dash.all.min.js"]);
    assert.equal(dash.instances.length, 1);
    assert.equal(
      dash.instances[0].calls.initialize[0].source,
      `http://localhost:8000/s/${TOKEN}/mpd?max_height=1080`,
    );
    assert.equal(ui.title.textContent, "Titre résolu");
    assert.equal(ui.message.hidden, true);
  });

  test("MPD absent → repli sur le progressif signé, sans charger dash.js", async () => {
    globalThis.window.dashjs = fakeDashjs();
    mockApi([
      { match: "/mpd", status: 404, json: { detail: "No split MP4 formats" } },
      {
        match: "/streams",
        json: streams([
          { engine: "dash", url: `/s/${TOKEN}/mpd?max_height=1080` },
          { engine: "progressive", url: SIGNED },
        ]),
      },
    ]);
    const { player, ui, loads } = makePlayer({ platform: "bitchute" });
    await player.start();
    assert.deepEqual(loads, []);
    assert.equal(ui.video.src, SIGNED);
    assert.ok(ui.video.calls.play >= 1);
  });

  test("lien expiré / révoqué → message explicite", async () => {
    mockApi([{ match: "/streams", status: 404, json: { detail: "gone" } }]);
    const { player, ui } = makePlayer();
    await player.start();
    assert.equal(ui.message.hidden, false);
    assert.match(ui.message.textContent, /expiré ou a été révoqué/);
  });

  test("live terminé → message explicite", async () => {
    mockApi([{ match: "/streams", status: 410, json: { detail: "This live has ended" } }]);
    const { player, ui } = makePlayer({ platform: "twitch" });
    await player.start();
    assert.match(ui.message.textContent, /live est terminé/);
  });

  test("reprise : la position mémorisée pour CE lien est demandée à dash.js", async () => {
    const dash = fakeDashjs();
    globalThis.window.dashjs = dash;
    globalThis.localStorage.setItem(`ytui.share.pos.${TOKEN}`, "120");
    mockApi([
      { match: "/mpd", status: 200, json: {} },
      { match: "/streams", json: streams([{ engine: "dash", url: `/s/${TOKEN}/mpd` }]) },
    ]);
    const { player } = makePlayer();
    await player.start();
    assert.equal(dash.instances[0].calls.initialize[0].startTime, 120);
  });

  test("la position est mémorisée localement, effacée en fin de vidéo", async () => {
    mockApi([{ match: "/streams", json: streams([{ engine: "progressive", url: SIGNED }]) }]);
    const { player, ui } = makePlayer({ platform: "odysee" });
    await player.start();
    ui.video.emitLoadedMetadata({ duration: 600 });
    ui.video.currentTime = 300;
    player.savePosition();
    assert.equal(globalThis.localStorage.getItem(`ytui.share.pos.${TOKEN}`), "300");
    ui.video.currentTime = 590;
    player.savePosition();
    assert.equal(globalThis.localStorage.getItem(`ytui.share.pos.${TOKEN}`), null);
  });

  test("changement de qualité → re-résolution au même endroit, choix mémorisé", async () => {
    const dash = fakeDashjs();
    globalThis.window.dashjs = dash;
    const api = mockApi([
      { match: "/mpd", status: 200, json: {} },
      { match: "/streams", json: streams([{ engine: "dash", url: `/s/${TOKEN}/mpd` }]) },
    ]);
    const { player, ui } = makePlayer();
    await player.start();
    assert.equal(ui.quality.children.length, mod.QUALITIES.length);
    assert.equal(ui.quality.value, "1080");
    ui.video.currentTime = 42.7;
    await player.setQuality(480);
    const reqs = api.find("/streams");
    assert.equal(reqs.length, 2);
    assert.match(reqs[1].url, /max_height=480/);
    assert.equal(globalThis.localStorage.getItem("ytui.share.quality"), "480");
    assert.equal(dash.instances[1].calls.initialize[0].startTime, 42);
    assert.equal(dash.instances[0].destroyed, true);
  });

  test("erreur HLS fatale → source suivante", async () => {
    const hls = fakeHls();
    globalThis.window.Hls = hls.Hls;
    mockApi([
      {
        match: "/streams",
        json: streams([
          { engine: "hls", url: `/s/${TOKEN}/hls?u=x&e=1&s=y` },
          { engine: "progressive", url: SIGNED },
        ]),
      },
    ]);
    const { player, ui } = makePlayer({ platform: "crowdbunker" });
    await player.start();
    assert.equal(hls.instances[0].url, `/s/${TOKEN}/hls?u=x&e=1&s=y`);
    hls.instances[0].emit(hls.Events.ERROR, { fatal: true });
    await tick();
    assert.equal(hls.instances[0].destroyed, true);
    assert.equal(ui.video.src, SIGNED);
  });

  test("toutes les sources en échec → une re-résolution, puis message", async () => {
    const api = mockApi([
      { match: "/streams", json: streams([{ engine: "progressive", url: SIGNED }]) },
    ]);
    const { player, ui } = makePlayer({ platform: "bitchute" });
    await player.start();
    ui.video.emitError();
    await tick();
    assert.equal(api.find("/streams").length, 2);
    ui.video.emitError();
    await tick();
    assert.equal(api.find("/streams").length, 2);
    assert.match(ui.message.textContent, /Lecture impossible/);
  });

  test("live TikTok FLV → mpegts.js, sans reprise", async () => {
    const m = fakeMpegts();
    globalThis.window.mpegts = m.mpegts;
    globalThis.localStorage.setItem(`ytui.share.pos.${TOKEN}`, "120");
    mockApi([
      {
        match: "/streams",
        json: streams([{ engine: "flv", url: SIGNED }], { is_live: true }),
      },
    ]);
    const { player, ui, loads } = makePlayer({ platform: "tiktok" });
    await player.start();
    assert.deepEqual(loads, ["/vendor/mpegts.min.js"]);
    assert.equal(m.instances[0].config.url, SIGNED);
    assert.equal(m.instances[0].config.isLive, true);
    assert.equal(ui.video.currentTime, 0);
  });

  test("sous-titres : pistes signées ajoutées et sélecteur affiché", async () => {
    const subUrl = `/s/${TOKEN}/p?u=sub&e=1&s=z`;
    mockApi([
      {
        match: "/streams",
        json: streams([{ engine: "progressive", url: SIGNED }], {
          subtitles: [{ lang: "fr", label: "Français", url: subUrl, auto: false }],
        }),
      },
    ]);
    const { player, ui } = makePlayer({ platform: "odysee" });
    await player.start();
    const tracks = ui.video.querySelectorAll("track");
    assert.equal(tracks.length, 1);
    assert.equal(tracks[0].src, subUrl);
    assert.equal(tracks[0].srclang, "fr");
    assert.equal(ui.subsLabel.hidden, false);
    assert.equal(ui.subs.children.length, 2); // « Désactivés » + fr
    assert.equal(ui.subs.value, "");
  });

  test("SponsorBlock : un segment sponsorisé est sauté (YouTube)", async () => {
    mockApi([
      { match: "/sponsor", json: { segments: [{ category: "sponsor", start: 10, end: 20 }] } },
      { match: "/streams", json: streams([{ engine: "progressive", url: SIGNED }]) },
    ]);
    const { player, ui } = makePlayer();
    await player.start();
    await tick();
    ui.video.emitTimeUpdate(12);
    assert.equal(ui.video.currentTime, 20);
    assert.match(ui.skip.textContent, /sponsorisé/);
  });

  test("SponsorBlock n'est pas interrogé hors YouTube", async () => {
    const api = mockApi([
      { match: "/streams", json: streams([{ engine: "progressive", url: SIGNED }]) },
    ]);
    const { player } = makePlayer({ platform: "odysee" });
    await player.start();
    await tick();
    assert.equal(api.find("/sponsor").length, 0);
  });

  test("langues de sous-titres : navigateur d'abord, puis fr/en, sans doublon", () => {
    assert.equal(mod.subtitleLangs(["en-US", "de", "en"]), "en,de,fr");
    assert.equal(mod.subtitleLangs(undefined), "fr,en");
  });

  test("pied de page : date d'expiration ou lien permanent", () => {
    assert.equal(mod.footerText(""), "Lien privé partagé via ytui");
    assert.match(mod.footerText("1893456000"), /valable jusqu'au .*2030/);
  });
});

process.on("exit", () => env?.restore?.());
