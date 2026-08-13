// Batterie sur la file de lecture web (js/queue.js) — la lecture d'une
// playlist passe entièrement par elle. Sémantique de référence : le port
// mobile mobile/lib/state/queue.dart (QueueNotifier), dont les tests Dart
// vivent dans mobile/test/queue_test.dart.
//
// Règle centrale : le filtrage est POSITIONNEL. Deux fois la même vidéo dans
// une playlist doivent survivre, et sauter en arrière vers un élément déjà lu
// ne doit jamais le dupliquer.

import test from "node:test";
import assert from "node:assert/strict";

import { installDom, makeVideo, makePlaylist } from "./harness.mjs";

// queue.js n'a besoin que d'EventTarget/Event (natifs Node ≥ 15), mais le
// harness est installé pour rester homogène avec les autres suites et pour
// couvrir le smoke test d'import de player.js plus bas.
const env = installDom();

const { queue } = await import("../js/queue.js");

/// Repart d'une file vide sans compter l'évènement de remise à zéro.
function reset() {
  queue.items = [];
  queue.index = 0;
}

/// Compte les évènements "change" émis pendant `fn`.
function countChanges(fn) {
  let n = 0;
  const onChange = () => {
    n += 1;
  };
  queue.addEventListener("change", onChange);
  try {
    fn();
  } finally {
    queue.removeEventListener("change", onChange);
  }
  return n;
}

const ids = () => queue.items.map((v) => v.video_id);
const currentId = () => (queue.current ? queue.current.video_id : null);
const upcomingIds = () => queue.upcoming.map((v) => v.video_id);

test.beforeEach(reset);

// ─── État initial ───

test("file vide : current null, pas de suivant ni de précédent", () => {
  assert.equal(queue.current, null);
  assert.equal(queue.hasNext, false);
  assert.equal(queue.hasPrevious, false);
  assert.deepEqual(queue.upcoming, []);
  assert.equal(queue.index, 0);
});

// ─── play() ───

test("play() remplace la file et démarre à l'index 0 par défaut", () => {
  queue.play(makePlaylist(3));
  assert.deepEqual(ids(), ["v1", "v2", "v3"]);
  assert.equal(queue.index, 0);
  assert.equal(currentId(), "v1");
  assert.equal(queue.hasPrevious, false);
  assert.equal(queue.hasNext, true);
  assert.deepEqual(upcomingIds(), ["v2", "v3"]);
});

test("play() avec startIndex démarre à l'élément demandé", () => {
  queue.play(makePlaylist(5), 2);
  assert.equal(currentId(), "v3");
  assert.equal(queue.index, 2);
  assert.equal(queue.hasPrevious, true);
  assert.equal(queue.hasNext, true);
  assert.deepEqual(upcomingIds(), ["v4", "v5"]);
});

test("play() avec startIndex sur le dernier élément : aucun suivant", () => {
  queue.play(makePlaylist(4), 3);
  assert.equal(currentId(), "v4");
  assert.equal(queue.hasNext, false);
  assert.deepEqual(upcomingIds(), []);
});

test("play() copie la liste : muter le tableau source ne touche pas la file", () => {
  const source = makePlaylist(2);
  queue.play(source);
  source.push(makeVideo("intrus"));
  assert.deepEqual(ids(), ["v1", "v2"]);
});

test("play() écrase intégralement une file précédente et son index", () => {
  queue.play(makePlaylist(5), 3);
  queue.play([makeVideo("solo")]);
  assert.deepEqual(ids(), ["solo"]);
  assert.equal(queue.index, 0);
  assert.equal(currentId(), "solo");
});

test("play([]) laisse une file vide et cohérente", () => {
  queue.play(makePlaylist(3), 1);
  queue.play([]);
  assert.deepEqual(ids(), []);
  assert.equal(queue.current, null);
  assert.equal(queue.hasNext, false);
  assert.equal(queue.hasPrevious, false);
});

// ─── next / previous ───

test("next() avance jusqu'au dernier puis ne fait plus rien", () => {
  queue.play(makePlaylist(3));
  queue.next();
  assert.equal(currentId(), "v2");
  queue.next();
  assert.equal(currentId(), "v3");
  assert.equal(queue.hasNext, false);
  queue.next(); // borne haute
  assert.equal(currentId(), "v3");
  assert.equal(queue.index, 2);
});

test("previous() recule jusqu'au premier puis ne fait plus rien", () => {
  queue.play(makePlaylist(3), 2);
  queue.previous();
  assert.equal(currentId(), "v2");
  queue.previous();
  assert.equal(currentId(), "v1");
  assert.equal(queue.hasPrevious, false);
  queue.previous(); // borne basse
  assert.equal(currentId(), "v1");
  assert.equal(queue.index, 0);
});

test("next() n'émet pas de 'change' en bout de file", () => {
  queue.play(makePlaylist(2), 1);
  assert.equal(countChanges(() => queue.next()), 0);
});

test("previous() n'émet pas de 'change' en tête de file", () => {
  queue.play(makePlaylist(2), 0);
  assert.equal(countChanges(() => queue.previous()), 0);
});

test("next() sur une file vide est un no-op silencieux", () => {
  assert.equal(countChanges(() => queue.next()), 0);
  assert.equal(queue.current, null);
  assert.equal(queue.index, 0);
});

// ─── enqueue ───

test("enqueue() ajoute en fin sans déplacer la lecture en cours", () => {
  queue.play(makePlaylist(2));
  queue.next(); // sur v2, dernier
  assert.equal(queue.hasNext, false);
  queue.enqueue(makeVideo("v3"));
  assert.equal(currentId(), "v2", "la vidéo en cours ne bouge pas");
  assert.equal(queue.index, 1);
  assert.equal(queue.hasNext, true);
  assert.deepEqual(upcomingIds(), ["v3"]);
});

test("enqueue() sur une file vide rend l'élément immédiatement courant", () => {
  queue.enqueue(makeVideo("seule"));
  assert.equal(currentId(), "seule");
  assert.equal(queue.index, 0);
  assert.equal(queue.hasNext, false);
});

test("enqueue() accepte un doublon de la vidéo en cours (jamais de dédup)", () => {
  queue.play(makePlaylist(2));
  queue.enqueue(makeVideo("v1"));
  assert.deepEqual(ids(), ["v1", "v2", "v1"]);
});

// ─── jumpTo ───

test("jumpTo() vers un élément à venir le joue et préserve l'ordre du reste", () => {
  queue.play(makePlaylist(5)); // v1 courant
  queue.jumpTo(3); // sauter sur v4
  assert.equal(currentId(), "v4");
  assert.deepEqual(ids(), ["v1", "v4", "v2", "v3", "v5"]);
  assert.equal(queue.index, 1);
  assert.deepEqual(upcomingIds(), ["v2", "v3", "v5"], "les sautés restent à venir");
  assert.equal(queue.hasPrevious, true);
});

test("jumpTo() en arrière vers un élément déjà lu ne le duplique pas", () => {
  queue.play(makePlaylist(5), 3); // v4 courant, v1..v3 déjà lus
  queue.jumpTo(1); // revenir sur v2
  assert.equal(currentId(), "v2");
  assert.deepEqual(ids(), ["v1", "v3", "v4", "v2", "v5"]);
  assert.equal(queue.index, 3);
  assert.equal(ids().filter((x) => x === "v2").length, 1, "v2 n'apparaît qu'une fois");
  assert.deepEqual(upcomingIds(), ["v5"]);
});

test("jumpTo() sur l'index courant est un no-op sans évènement", () => {
  queue.play(makePlaylist(4), 2);
  const before = ids();
  assert.equal(countChanges(() => queue.jumpTo(2)), 0);
  assert.deepEqual(ids(), before);
  assert.equal(queue.index, 2);
});

test("jumpTo() hors bornes est ignoré (négatif, égal à length, au-delà)", () => {
  queue.play(makePlaylist(3), 1);
  const before = ids();
  for (const bad of [-1, -10, 3, 99]) {
    assert.equal(countChanges(() => queue.jumpTo(bad)), 0, `index ${bad}`);
  }
  assert.deepEqual(ids(), before);
  assert.equal(queue.index, 1);
  assert.equal(currentId(), "v2");
});

test("jumpTo() distingue deux occurrences de la même vidéo (positionnel)", () => {
  // Playlist contenant deux fois la même vidéo — cas réel d'une playlist
  // YouTube avec un doublon. Le filtrage étant positionnel, sauter sur la
  // seconde occurrence déplace CELLE-CI juste après le courant et laisse la
  // première en place (parité mobile : queue_test.dart, « duplicate Video
  // instances in the queue are not lost on jump »).
  queue.play([makeVideo("a"), makeVideo("dup"), makeVideo("b"), makeVideo("dup")]);
  queue.jumpTo(3); // la SECONDE occurrence de "dup"
  assert.equal(currentId(), "dup");
  assert.deepEqual(ids(), ["a", "dup", "dup", "b"], "aucune perte, aucun doublon créé");
  assert.equal(queue.index, 1, "la 2e occurrence est venue se placer juste après le courant");
  assert.deepEqual(upcomingIds(), ["dup", "b"]);
  assert.equal(ids().filter((x) => x === "dup").length, 2, "les deux occurrences survivent");
});

test("jumpTo() vers la première occurrence d'un doublon laisse la seconde intacte", () => {
  queue.play([makeVideo("a"), makeVideo("dup"), makeVideo("b"), makeVideo("dup")]);
  queue.next(); // "dup" (index 1) courant
  queue.jumpTo(3); // sauter sur la seconde occurrence
  assert.equal(queue.index, 2);
  assert.deepEqual(ids(), ["a", "dup", "dup", "b"]);
  assert.equal(ids().filter((x) => x === "dup").length, 2);
  // Revenir en arrière redonne bien la première occurrence.
  queue.previous();
  assert.equal(currentId(), "dup");
  assert.equal(queue.index, 1);
});

test("jumpTo() vers l'élément juste après le courant garde l'ordre intact", () => {
  queue.play(makePlaylist(4));
  queue.jumpTo(1);
  assert.deepEqual(ids(), ["v1", "v2", "v3", "v4"]);
  assert.equal(queue.index, 1);
  assert.equal(currentId(), "v2");
});

test("jumpTo() vers le dernier élément d'une longue file", () => {
  queue.play(makePlaylist(10));
  queue.jumpTo(9);
  assert.equal(currentId(), "v10");
  assert.equal(queue.index, 1);
  assert.deepEqual(upcomingIds(), ["v2", "v3", "v4", "v5", "v6", "v7", "v8", "v9"]);
});

// ─── removeUpcoming ───

test("removeUpcoming(0) retire la vidéo suivante", () => {
  queue.play(makePlaylist(4)); // v1 courant
  queue.removeUpcoming(0);
  assert.deepEqual(ids(), ["v1", "v3", "v4"]);
  assert.equal(currentId(), "v1");
  assert.equal(queue.index, 0);
});

test("removeUpcoming(n) retire la n-ième à venir, index courant inchangé", () => {
  queue.play(makePlaylist(5), 1); // v2 courant
  queue.removeUpcoming(2); // v5
  assert.deepEqual(ids(), ["v1", "v2", "v3", "v4"]);
  assert.equal(currentId(), "v2");
  assert.equal(queue.index, 1);
});

test("removeUpcoming() ne retire jamais l'élément courant", () => {
  queue.play(makePlaylist(4), 2); // v3 courant
  for (const bad of [-1, -3, -100]) {
    assert.equal(countChanges(() => queue.removeUpcoming(bad)), 0, `offset ${bad}`);
  }
  assert.deepEqual(ids(), ["v1", "v2", "v3", "v4"]);
  assert.equal(currentId(), "v3");
});

test("removeUpcoming() ne touche pas au passé", () => {
  queue.play(makePlaylist(5), 3); // v4 courant, v1..v3 lus
  queue.removeUpcoming(-2); // viserait v2 : doit être ignoré
  assert.deepEqual(ids(), ["v1", "v2", "v3", "v4", "v5"]);
  queue.removeUpcoming(0); // v5, seul élément à venir
  assert.deepEqual(ids(), ["v1", "v2", "v3", "v4"]);
  assert.equal(currentId(), "v4");
  assert.equal(queue.hasNext, false);
});

test("removeUpcoming() au-delà de la fin est ignoré", () => {
  queue.play(makePlaylist(3));
  for (const bad of [2, 5, 99]) {
    assert.equal(countChanges(() => queue.removeUpcoming(bad)), 0, `offset ${bad}`);
  }
  assert.deepEqual(ids(), ["v1", "v2", "v3"]);
});

test("removeUpcoming() successifs vident la file à venir sans casser l'index", () => {
  queue.play(makePlaylist(4), 1); // v2 courant
  queue.removeUpcoming(0); // v3
  queue.removeUpcoming(0); // v4
  assert.deepEqual(ids(), ["v1", "v2"]);
  assert.equal(currentId(), "v2");
  assert.equal(queue.index, 1);
  assert.equal(queue.hasNext, false);
  assert.equal(countChanges(() => queue.removeUpcoming(0)), 0);
});

test("removeUpcoming() sur un doublon ne retire que l'occurrence visée", () => {
  queue.play([makeVideo("a"), makeVideo("dup"), makeVideo("dup"), makeVideo("b")]);
  queue.removeUpcoming(0); // la première "dup" à venir
  assert.deepEqual(ids(), ["a", "dup", "b"]);
});

// ─── clear ───

test("clear() vide la file et remet l'index à 0", () => {
  queue.play(makePlaylist(4), 2);
  queue.clear();
  assert.deepEqual(ids(), []);
  assert.equal(queue.index, 0);
  assert.equal(queue.current, null);
  assert.equal(queue.hasNext, false);
  assert.equal(queue.hasPrevious, false);
});

// ─── Évènement "change" ───

test("chaque mutation effective émet exactement un 'change'", () => {
  assert.equal(countChanges(() => queue.play(makePlaylist(4))), 1, "play");
  assert.equal(countChanges(() => queue.enqueue(makeVideo("v5"))), 1, "enqueue");
  assert.equal(countChanges(() => queue.next()), 1, "next");
  assert.equal(countChanges(() => queue.previous()), 1, "previous");
  assert.equal(countChanges(() => queue.jumpTo(3)), 1, "jumpTo");
  assert.equal(countChanges(() => queue.removeUpcoming(0)), 1, "removeUpcoming");
  assert.equal(countChanges(() => queue.clear()), 1, "clear");
});

test("les no-op n'émettent aucun 'change'", () => {
  queue.play(makePlaylist(2), 1);
  const noop = () => {
    queue.next(); // déjà au bout
    queue.jumpTo(1); // index courant
    queue.jumpTo(42); // hors bornes
    queue.removeUpcoming(0); // rien à venir
    queue.removeUpcoming(-1); // le courant
  };
  assert.equal(countChanges(noop), 0);
});

test("plusieurs abonnés reçoivent le même 'change'", () => {
  queue.play(makePlaylist(2));
  let a = 0;
  let b = 0;
  const ha = () => (a += 1);
  const hb = () => (b += 1);
  queue.addEventListener("change", ha);
  queue.addEventListener("change", hb);
  queue.next();
  queue.removeEventListener("change", ha);
  queue.removeEventListener("change", hb);
  assert.equal(a, 1);
  assert.equal(b, 1);
});

test("l'état est déjà à jour quand le handler 'change' s'exécute", () => {
  queue.play(makePlaylist(3));
  let seen = null;
  const h = () => {
    seen = { id: currentId(), index: queue.index, upcoming: upcomingIds() };
  };
  queue.addEventListener("change", h);
  queue.next();
  queue.removeEventListener("change", h);
  assert.deepEqual(seen, { id: "v2", index: 1, upcoming: ["v3"] });
});

// ─── Scénario complet : playlist de 10 vidéos lue de bout en bout ───

test("playlist de 10 vidéos : lecture intégrale, un 'change' par avance", () => {
  const playlist = makePlaylist(10);
  const visited = [];
  const onChange = () => visited.push(currentId());
  queue.addEventListener("change", onChange);

  queue.play(playlist); // v1
  for (let i = 0; i < 9; i++) queue.next();

  queue.removeEventListener("change", onChange);

  assert.equal(currentId(), "v10");
  assert.equal(queue.hasNext, false, "fin de playlist");
  assert.deepEqual(upcomingIds(), []);
  assert.equal(queue.index, 9);
  assert.deepEqual(
    visited,
    ["v1", "v2", "v3", "v4", "v5", "v6", "v7", "v8", "v9", "v10"],
    "chaque vidéo devient courante une fois et une seule",
  );
  assert.deepEqual(ids(), playlist.map((v) => v.video_id), "l'ordre de la playlist est intact");
});

test("playlist de 10 : parcours réaliste (saut, retrait, retour arrière, ajout)", () => {
  queue.play(makePlaylist(10));

  // On regarde v1 puis v2.
  queue.next();
  assert.equal(currentId(), "v2");

  // L'utilisateur clique sur v7 dans le panneau « File d'attente » : dans la
  // vue, l'offset i=4 de `upcoming` correspond à l'index absolu index+1+i = 6.
  const absolute = queue.index + 1 + 4;
  assert.equal(queue.items[absolute].video_id, "v7");
  queue.jumpTo(absolute);
  assert.equal(currentId(), "v7");
  assert.deepEqual(ids(), ["v1", "v2", "v7", "v3", "v4", "v5", "v6", "v8", "v9", "v10"]);

  // Il retire la suivante (v3) de la file.
  queue.removeUpcoming(0);
  assert.deepEqual(upcomingIds(), ["v4", "v5", "v6", "v8", "v9", "v10"]);

  // Il ajoute une suggestion en fin de file.
  queue.enqueue(makeVideo("suggestion"));
  assert.equal(queue.items.at(-1).video_id, "suggestion");

  // Retour arrière : la lecture précédente reste accessible.
  queue.previous();
  assert.equal(currentId(), "v2");
  queue.previous();
  assert.equal(currentId(), "v1");
  assert.equal(queue.hasPrevious, false);

  // Puis lecture jusqu'au bout de ce qui reste.
  const remaining = queue.items.length - queue.index - 1;
  for (let i = 0; i < remaining; i++) queue.next();
  assert.equal(currentId(), "suggestion");
  assert.equal(queue.hasNext, false);
  assert.equal(queue.items.length, 10, "9 restantes + la suggestion");
  assert.equal(new Set(ids()).size, 10, "aucun doublon accidentel");
});

test("playlist de 10 : jumpTo répétés ne perdent ni ne dupliquent d'élément", () => {
  queue.play(makePlaylist(10));
  for (const target of [5, 2, 8, 1, 9, 3]) {
    queue.jumpTo(target);
    assert.equal(queue.items.length, 10, `taille stable après jumpTo(${target})`);
    assert.equal(new Set(ids()).size, 10, `aucun doublon après jumpTo(${target})`);
    assert.equal(queue.current, queue.items[queue.index]);
  }
});

// ─── Smoke : le harness suffit à charger player.js ───

test("smoke : player.js s'importe et s'instancie sous le harness", async () => {
  const { Player, isLiveId, resumeStart } = await import("../js/player.js");
  assert.equal(typeof Player, "function");

  // Les helpers purs exportés par le lecteur (règle de reprise partagée
  // avec le mobile).
  assert.equal(isLiveId("twitch", "chan:123"), true);
  assert.equal(isLiveId("youtube", "abc"), false);
  assert.equal(resumeStart(0, 600), 0);
  assert.equal(resumeStart(42.7, 600), 42);
  assert.equal(resumeStart(590, 600), 0, "≥95 % ⇒ redémarrer au début");

  const el = env.videoElement();
  const player = new Player(el, {});
  assert.equal(player.el, el);
  assert.equal(player.rate, 1);
  player.destroy();
});
