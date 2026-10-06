// Vue chaîne (views/channel.js) : onglets Vidéos / Playlists (YouTube et
// BitChute seulement), chargement paresseux des playlists et ouverture d'une
// playlist au clic ; tri des vidéos (YouTube, BitChute, CrowdBunker).
//
// Lancement : node --test "backend/ytui_server/web/tests/*.test.mjs"

import { test, describe, beforeEach } from "node:test";
import assert from "node:assert/strict";

import { installDom, mockApi, flush, makeVideo } from "./harness.mjs";

let channel;

const PLAYLIST = makeVideo("PLabc", { kind: "playlist", title: "Une playlist", duration: null });

function routes({ videos = [makeVideo("v1")], playlists = [PLAYLIST] } = {}) {
  return [
    { match: "/api/lives", json: [] },
    { match: "/api/channels?", json: [] },
    { match: /\/api\/channels$/, json: [] },
    {
      match: "/playlists",
      json: { items: playlists, channel: { title: "Chaîne API" }, has_more: false },
    },
    {
      match: "/videos",
      json: { items: videos, channel: { title: "Chaîne API" }, has_more: false },
    },
  ];
}

async function mount(platform, search = "") {
  const view = document.getElementById("view");
  view.replaceChildren();
  await channel.render(view, {
    params: { platform, id: "UCchan" },
    query: new URLSearchParams(search),
  });
  await flush();
  return view;
}

const buttons = (view) => view.querySelectorAll("button");
const tabButtons = (view) =>
  buttons(view).filter((b) => b.getAttribute("role") === "tab");

describe("vue chaîne — onglet Playlists", () => {
  beforeEach(async () => {
    installDom();
    channel = await import("../views/channel.js");
  });

  test("YouTube : onglets Vidéos/Playlists, vidéos par défaut", async () => {
    const api = mockApi(routes());
    const view = await mount("youtube", "title=Ma%20cha%C3%AEne");
    const tabs = tabButtons(view);
    assert.deepEqual(tabs.map((b) => b.textContent), ["Vidéos", "Playlists"]);
    assert.equal(tabs[0].getAttribute("aria-selected"), "true");
    assert.equal(api.find("/playlists").length, 0, "playlists non chargées d'avance");
    assert.equal(api.find("/videos").length, 1);
    assert.ok(view.querySelector("form"), "recherche présente sur l'onglet Vidéos");

    tabs[1].dispatchEvent(new Event("click"));
    // Le nom corrigé par l'API est repris dans l'URL de l'onglet.
    assert.equal(location.hash, "#/channel/youtube/UCchan?title=Cha%C3%AEne%20API&tab=playlists");
  });

  test("onglet Playlists : liste paginée, sans recherche ni Tout lire, clic = ouverture", async () => {
    const api = mockApi(routes());
    const view = await mount("bitchute", "title=X&tab=playlists");
    const req = api.find("/api/channels/UCchan/playlists");
    assert.equal(req.length, 1);
    assert.ok(req[0].url.includes("platform=bitchute"));
    assert.equal(api.find("/videos").length, 0);
    assert.equal(view.querySelector("form"), null);
    assert.ok(!buttons(view).some((b) => (b.textContent || "").includes("Tout lire")));
    assert.equal(tabButtons(view)[1].getAttribute("aria-selected"), "true");

    const card = view.querySelectorAll("div").find((d) => d._video);
    assert.equal(card._video.video_id, "PLabc");
    card.dispatchEvent(new Event("click"));
    assert.equal(location.hash, "#/ytplaylist/youtube/PLabc");
  });

  test("chaîne sans playlist : état vide dédié", async () => {
    mockApi(routes({ playlists: [] }));
    const view = await mount("youtube", "tab=playlists");
    const text = view.querySelectorAll("div").map((d) => d.textContent).join("|");
    assert.ok(text.includes("Cette chaîne n'a aucune playlist"));
  });

  test("Odysee : pas d'onglets, ?tab=playlists ignoré", async () => {
    const api = mockApi(routes());
    const view = await mount("odysee", "tab=playlists");
    assert.equal(tabButtons(view).length, 0);
    assert.equal(api.find("/playlists").length, 0);
    assert.equal(api.find("/videos").length, 1);
  });

  test("channelTabPath : onglet, recherche et tri dans le hash", () => {
    assert.equal(
      channel.channelTabPath("UC1", "youtube", "A B", "playlists"),
      "/channel/youtube/UC1?title=A%20B&tab=playlists",
    );
    assert.equal(
      channel.channelTabPath("UC1", "youtube", "", "videos", "linux"),
      "/channel/youtube/UC1?q=linux",
    );
    assert.equal(
      channel.channelTabPath("UC1", "youtube", "A", "videos", "", "views"),
      "/channel/youtube/UC1?title=A&sort=views",
    );
    // Le tri ne concerne que les vidéos.
    assert.equal(
      channel.channelTabPath("UC1", "youtube", "", "playlists", "", "views"),
      "/channel/youtube/UC1?tab=playlists",
    );
  });
});

describe("vue chaîne — tri des vidéos", () => {
  beforeEach(async () => {
    installDom();
    channel = await import("../views/channel.js");
  });

  const SORT_LABELS = ["Plus récentes", "Plus vues"];
  const sortButtons = (view) => buttons(view).filter((b) => SORT_LABELS.includes(b.textContent));

  test("YouTube : plus récentes par défaut, clic sur Plus vues = tri dans le hash", async () => {
    const api = mockApi(routes());
    const view = await mount("youtube", "title=X");
    const sorts = sortButtons(view);
    assert.deepEqual(sorts.map((b) => b.textContent), ["Plus récentes", "Plus vues"]);
    assert.equal(sorts[0].getAttribute("aria-pressed"), "true");
    assert.ok(!api.find("/videos")[0].url.includes("sort="), "ordre par défaut non envoyé");

    sorts[1].dispatchEvent(new Event("click"));
    assert.equal(location.hash, "#/channel/youtube/UCchan?title=Cha%C3%AEne%20API&sort=views");
  });

  test("CrowdBunker : ?sort=views demandé à l'API, puce active", async () => {
    const api = mockApi(routes());
    const view = await mount("crowdbunker", "sort=views");
    assert.ok(api.find("/api/channels/UCchan/videos")[0].url.includes("sort=views"));
    assert.equal(sortButtons(view)[1].getAttribute("aria-pressed"), "true");
    assert.equal(tabButtons(view).length, 0, "pas d'onglet Playlists sur CrowdBunker");
  });

  test("Odysee : pas de tri, ?sort=views ignoré", async () => {
    const api = mockApi(routes());
    const view = await mount("odysee", "sort=views");
    assert.equal(sortButtons(view).length, 0);
    assert.ok(!api.find("/videos")[0].url.includes("sort="));
  });

  test("recherche : ordre de la plateforme, puces masquées, tri gardé pour Effacer", async () => {
    const api = mockApi(routes());
    const view = await mount("bitchute", "q=linux&sort=views");
    assert.equal(sortButtons(view).length, 0);
    const url = api.find("/videos")[0].url;
    assert.ok(url.includes("q=linux") && !url.includes("sort="));

    const clear = buttons(view).find((b) => b.textContent === "Effacer");
    clear.dispatchEvent(new Event("click"));
    assert.equal(location.hash, "#/channel/bitchute/UCchan?sort=views");
  });

  test("onglet Playlists : pas de tri", async () => {
    mockApi(routes());
    const view = await mount("youtube", "tab=playlists&sort=views");
    assert.equal(sortButtons(view).length, 0);
  });
});
