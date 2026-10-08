// « Ajouter à une playlist » (js/ui.js, bouton Playlist de la vue lecture et
// menu des cartes) : playlist existante ou nouvelle créée à la volée.
//
// Lancement : node --test "backend/ytui_server/web/tests/*.test.mjs"

import { test, describe, beforeEach } from "node:test";
import assert from "node:assert/strict";

import { installDom, mockApi, makeVideo, flush } from "./harness.mjs";

let ui;

const modalRoot = () => document.getElementById("modal-root");

/// Texte visible d'un noeud (textContent des feuilles concaténé).
function text(node) {
  if (typeof node !== "object") return String(node);
  const own = node.textContent || "";
  return own + (node.children || []).map(text).join("");
}

function buttons() {
  return modalRoot().querySelectorAll("button");
}

function click(node) {
  node.dispatchEvent(new Event("click"));
}

/// Remplit puis valide la boîte « Nouvelle playlist » ouverte.
function submitName(name) {
  const input = modalRoot().querySelector("input");
  assert.ok(input, "champ du nom affiché");
  input.value = name;
  modalRoot().querySelector("form").dispatchEvent(new Event("submit", { cancelable: true }));
}

function toasts() {
  return document.getElementById("toasts").children.map(text);
}

describe("ajout d'une vidéo à une playlist", () => {
  beforeEach(async () => {
    installDom();
    ui = await import("../js/ui.js");
    modalRoot().replaceChildren();
    document.getElementById("toasts").replaceChildren();
  });

  test("sans playlist : la modale s'ouvre quand même et propose d'en créer une", async () => {
    const video = makeVideo("new00000001");
    const api = mockApi([
      { match: "/api/playlists/7/items", method: "POST", status: 201, json: {} },
      {
        match: "/api/playlists",
        method: "POST",
        status: 201,
        json: { id: 7, name: "Ma liste", created_at: 0, count: 0 },
      },
      { match: "/api/playlists", method: "GET", json: [] },
    ]);

    await ui.addToPlaylistModal(video);
    const [newBtn, ...rest] = buttons();
    assert.match(text(newBtn), /Nouvelle playlist/);
    assert.equal(rest.length, 0);
    assert.match(text(modalRoot()), /Aucune playlist/);

    click(newBtn);
    submitName("Ma liste");
    await flush(10);

    assert.deepEqual(api.find("/api/playlists", "POST")[0].body, { name: "Ma liste" });
    const added = api.find("/api/playlists/7/items", "POST");
    assert.equal(added.length, 1);
    assert.equal(added[0].body.video.video_id, "new00000001");
    assert.deepEqual(toasts(), ["Ajouté à « Ma liste »"]);
    assert.equal(modalRoot().children.length, 0, "plus aucune modale ouverte");
  });

  test("avec des playlists : « Nouvelle playlist » en tête, puis les existantes", async () => {
    const api = mockApi([
      { match: "/api/playlists/3/items", method: "POST", status: 201, json: {} },
      {
        match: "/api/playlists",
        method: "GET",
        json: [
          { id: 3, name: "Musique", created_at: 0, count: 2 },
          { id: 4, name: "Plus tard", created_at: 0, count: 0 },
        ],
      },
    ]);

    await ui.addToPlaylistModal(makeVideo("old00000001"));
    const labels = buttons().map(text);
    assert.equal(labels.length, 3);
    assert.match(labels[0], /Nouvelle playlist/);
    assert.deepEqual(labels.slice(1), ["Musique (2)", "Plus tard (0)"]);

    click(buttons()[1]);
    await flush(10);
    assert.equal(api.find("/api/playlists/3/items", "POST").length, 1);
    assert.equal(api.find("/api/playlists", "POST").filter((r) => !r.url.includes("/items")).length, 0);
    assert.deepEqual(toasts(), ["Ajouté à « Musique »"]);
  });

  test("nom déjà pris : la vidéo va dans la playlist existante de ce nom", async () => {
    const api = mockApi([
      { match: "/api/playlists/3/items", method: "POST", status: 201, json: {} },
      {
        match: "/api/playlists",
        method: "POST",
        status: 409,
        json: { detail: "Playlist name already taken" },
      },
      {
        match: "/api/playlists",
        method: "GET",
        json: [{ id: 3, name: "Musique", created_at: 0, count: 2 }],
      },
    ]);

    await ui.addToPlaylistModal(makeVideo("dup00000001"));
    click(buttons()[0]);
    submitName("Musique");
    await flush(10);

    assert.equal(api.find("/api/playlists/3/items", "POST").length, 1);
    assert.deepEqual(toasts(), ["Ajouté à « Musique »"]);
  });

  test("création annulée : rien n'est envoyé", async () => {
    const api = mockApi([{ match: "/api/playlists", method: "GET", json: [] }]);

    await ui.addToPlaylistModal(makeVideo("can00000001"));
    click(buttons()[0]);
    const cancel = buttons().find((b) => text(b) === "Annuler");
    click(cancel);
    await flush(10);

    assert.equal(api.requests.filter((r) => r.method === "POST").length, 0);
    assert.equal(modalRoot().children.length, 0);
  });
});
