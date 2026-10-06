// Vue chaîne (views/channel.js) : onglets Vidéos / Playlists (YouTube et
// BitChute seulement), chargement paresseux des playlists et ouverture d'une
// playlist au clic.
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

  test("channelTabPath : onglet et recherche dans le hash", () => {
    assert.equal(
      channel.channelTabPath("UC1", "youtube", "A B", "playlists"),
      "/channel/youtube/UC1?title=A%20B&tab=playlists",
    );
    assert.equal(
      channel.channelTabPath("UC1", "youtube", "", "videos", "linux"),
      "/channel/youtube/UC1?q=linux",
    );
  });
});
