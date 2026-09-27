// Vue Musique (views/music.js) : étagères façon YouTube Music — ce qu'un clic
// lance ou ouvre selon le type de carte, et la forme des vignettes.
//
// Lancement : node --test "backend/ytui_server/web/tests/*.test.mjs"

import { test, describe, beforeEach } from "node:test";
import assert from "node:assert/strict";

import { installDom, makeVideo } from "./harness.mjs";

let music;
let queue;

const COVER = "https://yt3.googleusercontent.com/abc=w544-h544-l90-rj";
const CLIP = "https://i.ytimg.com/vi/clip0000001/hq720.jpg";

function shelf(items, extra = {}) {
  return { title: "Sélection rapide", strapline: "", items, ...extra };
}

/// Cartes de l'étagère (enfants du .shelf-row).
const cardsOf = (section) => section.children[1].children;

describe("étagères Musique", () => {
  beforeEach(async () => {
    installDom();
    music = await import("../views/music.js");
    ({ queue } = await import("../js/queue.js"));
    queue.items = [];
    queue.index = 0;
  });

  test("en-tête : surtitre puis titre", () => {
    const section = music.shelfSection(shelf([makeVideo("t1")], { strapline: "EN RAPPORT AVEC" }));
    const head = section.children[0];
    assert.equal(head.children[0].textContent, "EN RAPPORT AVEC");
    assert.equal(head.children[1].textContent, "Sélection rapide");
  });

  test("un titre lance la lecture en enchaînant le reste de l'étagère", () => {
    const tracks = [makeVideo("t1"), makeVideo("t2"), makeVideo("t3")];
    const mix = makeVideo("RDTMAK5uy_mix", { kind: "playlist" });
    const section = music.shelfSection(shelf([tracks[0], mix, tracks[1], tracks[2]]));
    cardsOf(section)[2].dispatchEvent(new Event("click"));
    assert.deepEqual(queue.items.map((v) => v.video_id), ["t1", "t2", "t3"]);
    assert.equal(queue.index, 1);
    assert.equal(location.hash, "#/watch/youtube/t2");
  });

  test("un album/mix s'ouvre comme une playlist, un artiste comme une chaîne", () => {
    const mix = makeVideo("RDTMAK5uy_mix", { kind: "playlist" });
    const artist = makeVideo("UCartist", { kind: "channel", title: "Artiste A" });
    const section = music.shelfSection(shelf([mix, artist]));
    cardsOf(section)[0].dispatchEvent(new Event("click"));
    assert.equal(location.hash, "#/ytplaylist/youtube/RDTMAK5uy_mix");
    cardsOf(section)[1].dispatchEvent(new Event("click"));
    assert.equal(location.hash, "#/channel/youtube/UCartist?title=Artiste%20A");
    assert.equal(queue.items.length, 0, "rien n'est lancé");
  });

  test("pochettes carrées, clips en 16:9, artistes ronds", () => {
    const section = music.shelfSection(
      shelf([
        makeVideo("t1", { thumbnail_url: COVER }),
        makeVideo("clip0000001", { thumbnail_url: CLIP }),
        makeVideo("UCartist", { kind: "channel", thumbnail_url: COVER }),
      ]),
    );
    const [track, clip, artist] = cardsOf(section);
    // Le harness tient classList à part de className (posé par el()).
    assert.ok(track.classList.contains("square"));
    assert.ok(!clip.classList.contains("square") && !clip.classList.contains("artist"));
    assert.ok(artist.classList.contains("artist") && !artist.classList.contains("square"));
  });
});
