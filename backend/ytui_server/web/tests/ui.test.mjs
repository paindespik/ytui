// Cartes vidéo (js/ui.js) : progression, « vue », plateforme — ce que la
// grille dit d'une vidéo sans l'ouvrir.
//
// Lancement : node --test "backend/ytui_server/web/tests/*.test.mjs"

import { test, describe, beforeEach } from "node:test";
import assert from "node:assert/strict";

import { installDom, makeVideo } from "./harness.mjs";

let ui;
let watched;

/// Descendants dont l'attribut class contient `cls` (el() pose className).
function byClass(root, cls) {
  const out = [];
  const walk = (n) => {
    for (const c of n.children || []) {
      if (typeof c === "object" && String(c.className || "").split(/\s+/).includes(cls)) out.push(c);
      if (typeof c === "object") walk(c);
    }
  };
  walk(root);
  return out;
}

describe("cartes vidéo", () => {
  beforeEach(async () => {
    installDom();
    ui = await import("../js/ui.js");
    ({ watched } = await import("../js/state.js"));
    watched.ids = new Set();
    watched.progress = new Map();
  });

  test("vidéo entamée : barre de progression à la bonne largeur, pas grisée", () => {
    const v = makeVideo("half0000001");
    watched.add(v);
    watched.setProgress(v, 0.42);
    const card = ui.videoCard(v);
    assert.equal(card.className, "card");
    const bar = byClass(card, "thumb-progress")[0];
    assert.ok(bar, "barre présente");
    assert.equal(bar.children[0].getAttribute("style"), "width:42%");
    assert.equal(byClass(card, "seen").length, 0, "pas de badge « vue » à 42 %");
  });

  test("vidéo finie : carte grisée + badge « Vue »", () => {
    const v = makeVideo("done0000001");
    watched.add(v);
    watched.setProgress(v, 0.97);
    const card = ui.videoCard(v);
    assert.equal(card.className, "card done");
    assert.equal(byClass(card, "seen").length, 1);
  });

  test("vidéo ouverte sans position connue : simple coche", () => {
    const v = makeVideo("open0000001");
    watched.add(v);
    const card = ui.videoCard(v);
    assert.equal(byClass(card, "seen").length, 1);
    assert.equal(byClass(card, "thumb-progress").length, 0);
  });

  test("plateforme non-YouTube : pastille nommée ; YouTube : aucune", () => {
    const odysee = ui.videoCard(makeVideo("name:abc", { platform: "odysee" }));
    const chip = byClass(odysee, "platform")[0];
    assert.equal(chip.textContent, "Odysee");
    assert.equal(byClass(ui.videoCard(makeVideo("yt000000001")), "platform").length, 0);
  });

  test("un direct n'affiche ni durée, ni progression", () => {
    const v = makeVideo("login:123", { platform: "twitch" });
    watched.add(v);
    watched.setProgress(v, 0.5);
    const card = ui.videoCard(v);
    assert.equal(byClass(card, "live").length, 1);
    assert.equal(byClass(card, "thumb-progress").length, 0);
  });
});
