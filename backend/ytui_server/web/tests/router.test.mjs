// Le routeur doit garantir qu'une seule vue est montée à la fois — sinon un
// lecteur abandonné continue de jouer en fond et réécrit sa position périmée
// par-dessus la progression réelle (bug observé en E2E sous forte latence :
// la position d'une vidéo restait bloquée sur une ancienne valeur).

import { test, describe, beforeEach } from "node:test";
import assert from "node:assert/strict";

import { installDom } from "./harness.mjs";

let router;
let view;

const tick = () => new Promise((r) => setTimeout(r, 0));
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

describe("routeur — cycle de vie des vues", () => {
  beforeEach(async () => {
    installDom();
    router = await import(`../js/router.js?t=${Math.random()}`);
    view = globalThis.document.createElement("div");
    globalThis.location.hash = "";
  });

  test("le nettoyage de la vue précédente est appelé avant la suivante", async () => {
    const order = [];
    router.route("/a", () => {
      order.push("render-a");
      return () => order.push("cleanup-a");
    });
    router.route("/b", () => {
      order.push("render-b");
      return () => order.push("cleanup-b");
    });

    globalThis.location.hash = "#/a";
    await router.dispatch(view);
    globalThis.location.hash = "#/b";
    await router.dispatch(view);

    assert.deepEqual(order, ["render-a", "cleanup-a", "render-b"]);
  });

  test("un rendu lent dépassé est démonté immédiatement", async () => {
    const events = [];
    router.route("/slow", async () => {
      events.push("render-slow");
      await sleep(40); // la vue attend le réseau
      return () => events.push("cleanup-slow");
    });
    router.route("/fast", () => {
      events.push("render-fast");
      return () => events.push("cleanup-fast");
    });

    globalThis.location.hash = "#/slow";
    const slow = router.dispatch(view);
    await tick();
    globalThis.location.hash = "#/fast";
    await router.dispatch(view);
    await slow;

    // La vue lente doit s'être nettoyée elle-même…
    assert.ok(events.includes("cleanup-slow"), events.join(","));
    // …sans emporter celle qui est réellement affichée.
    assert.ok(!events.includes("cleanup-fast"), events.join(","));
  });

  test("le nettoyage de la vue affichée n'est pas écrasé par une vue dépassée", async () => {
    const events = [];
    router.route("/slow", async () => {
      await sleep(40);
      return () => events.push("cleanup-slow");
    });
    router.route("/fast", () => () => events.push("cleanup-fast"));
    router.route("/third", () => () => events.push("cleanup-third"));

    globalThis.location.hash = "#/slow";
    const slow = router.dispatch(view);
    await tick();
    globalThis.location.hash = "#/fast";
    await router.dispatch(view);
    await slow;

    events.length = 0;
    globalThis.location.hash = "#/third";
    await router.dispatch(view);

    // C'est bien /fast qui était monté : c'est lui qu'on démonte.
    assert.deepEqual(events, ["cleanup-fast"]);
  });

  test("navigations rapides en rafale : aucune vue ne reste montée", async () => {
    let mounted = 0;
    router.route("/x/:n", async () => {
      await sleep(5);
      mounted += 1;
      return () => (mounted -= 1);
    });

    const runs = [];
    for (let i = 0; i < 6; i++) {
      globalThis.location.hash = `#/x/${i}`;
      runs.push(router.dispatch(view));
      await tick();
    }
    await Promise.all(runs);

    assert.equal(mounted, 1, "une seule vue montée au bout du compte");
  });
});
