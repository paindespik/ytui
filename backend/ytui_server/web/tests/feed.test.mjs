// Accueil (views/feed.js) et description (js/ui.js) : fonctions pures —
// groupes par date, filtres, découpage liens / horodatages.
//
// Lancement : node --test "backend/ytui_server/web/tests/*.test.mjs"

import { test, describe, beforeEach } from "node:test";
import assert from "node:assert/strict";

import { installDom, makeVideo } from "./harness.mjs";

installDom();
const feed = await import("../views/feed.js");
const ui = await import("../js/ui.js");
const state = await import("../js/state.js");

const NOW = new Date(2026, 8, 27, 15, 0, 0); // 27 sept. 2026, 15 h locale
const at = (daysAgo, hour = 10) =>
  new Date(2026, 8, 27 - daysAgo, hour, 0, 0).toISOString();

describe("accueil — groupes par date", () => {
  test("jours calendaires locaux, pas des tranches de 24 h", () => {
    assert.equal(feed.dateGroup(at(0, 1), NOW), "Aujourd'hui");
    assert.equal(feed.dateGroup(at(1, 23), NOW), "Hier");
    assert.equal(feed.dateGroup(at(3), NOW), "Cette semaine");
    assert.equal(feed.dateGroup(at(20), NOW), "Ce mois-ci");
    assert.equal(feed.dateGroup(at(60), NOW), "Plus ancien");
    assert.equal(feed.dateGroup(null, NOW), "Sans date");
  });

  test("groupes consécutifs dans l'ordre du flux", () => {
    const vids = [at(0), at(0), at(1), at(3), at(40)].map((p, i) =>
      makeVideo(`v${i}`, { published: p }),
    );
    const groups = feed.groupByDate(vids, NOW);
    assert.deepEqual(
      groups.map((g) => [g.label, g.videos.length]),
      [["Aujourd'hui", 2], ["Hier", 1], ["Cette semaine", 1], ["Plus ancien", 1]],
    );
  });
});

describe("accueil — filtres", () => {
  const vids = [
    makeVideo("a", { channel_id: "UC1", channel_title: "Un" }),
    makeVideo("b", { channel_id: "UC2", channel_title: "Deux" }),
    makeVideo("c:1", { platform: "odysee", channel_id: "@x:1", channel_title: "X" }),
  ];

  test("par plateforme", () => {
    assert.deepEqual(
      feed.filterVideos(vids, { platform: "odysee" }).map((v) => v.video_id),
      ["c:1"],
    );
  });

  test("par chaîne (clé plateforme + id)", () => {
    assert.deepEqual(
      feed.filterVideos(vids, { channel: "youtube:UC2" }).map((v) => v.video_id),
      ["b"],
    );
  });

  test("sans filtre : tout", () => {
    assert.equal(feed.filterVideos(vids, {}).length, 3);
  });
});

describe("accueil — « Nouveau » depuis la visite précédente", () => {
  beforeEach(() => {
    globalThis.localStorage.clear();
    globalThis.sessionStorage.clear();
  });

  test("première visite : aucune référence (pas de badge partout)", () => {
    assert.equal(state.feedNewSince(1000), null);
  });

  test("visite suivante (nouvel onglet) : la précédente fait référence", () => {
    state.feedNewSince(1000);
    globalThis.sessionStorage.clear(); // nouvelle session
    assert.equal(state.feedNewSince(5000), 1000);
  });

  test("même onglet : la référence reste figée (retour depuis une vidéo)", () => {
    state.feedNewSince(1000);
    globalThis.sessionStorage.clear();
    assert.equal(state.feedNewSince(5000), 1000);
    assert.equal(state.feedNewSince(9000), 1000);
  });
});

describe("description — liens et horodatages", () => {
  test("horodatages m:ss et h:mm:ss convertis en secondes", () => {
    const segs = ui.parseDescription("Intro 0:00, suite 12:34, fin 1:02:03.");
    assert.deepEqual(
      segs.filter((s) => s.type === "time").map((s) => [s.value, s.seconds]),
      [["0:00", 0], ["12:34", 754], ["1:02:03", 3723]],
    );
    assert.equal(segs.map((s) => s.value).join(""), "Intro 0:00, suite 12:34, fin 1:02:03.");
  });

  test("les URL restent entières (pas d'horodatage dans un lien)", () => {
    const segs = ui.parseDescription("voir https://x.org/a?t=1:30 puis 2:00");
    assert.deepEqual(
      segs.map((s) => s.type),
      ["text", "link", "text", "time"],
    );
    assert.equal(segs[1].value, "https://x.org/a?t=1:30");
  });

  test("pas de faux positifs : minutes > 59, heures du jour, ratios", () => {
    const times = (t) => ui.parseDescription(t).filter((s) => s.type === "time");
    assert.equal(times("score 3-1, 20h30, 1:75, 12:345").length, 0);
    assert.equal(times("ratio 16:9").length, 0);
  });
});
