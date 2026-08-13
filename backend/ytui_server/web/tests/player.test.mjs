// Batterie sur le lecteur navigateur : reprise d'une vidéo partiellement vue
// et enchaînement d'une playlist. Ce sont les deux chemins où une erreur
// intermittente est la plus coûteuse (la vidéo repart du début, ou la file
// saute un titre), donc chaque règle a son test.
//
// Lancement : node --test "backend/ytui_server/web/tests/*.test.mjs"

import { test, describe, beforeEach, afterEach } from "node:test";
import assert from "node:assert/strict";

import {
  installDom,
  FakeMediaElement,
  fakeDashjs,
  fakeHls,
  mockApi,
  flush,
  makeVideo,
} from "./harness.mjs";

let env;
let Player;
let resumeStart;
let queue;

// ─── Réponses serveur par défaut ───

const STREAMS_PROGRESSIVE = {
  kind: "progressive",
  url: "https://cdn.example/video.mp4",
  title: "Une vidéo",
  duration: 600,
  height: 720,
  subtitles: [],
};

const STREAMS_SPLIT = {
  kind: "split",
  url: "https://cdn.example/video-only.mp4",
  audio_url: "https://cdn.example/audio.m4a",
  title: "Une vidéo DASH",
  duration: 600,
  height: 1080,
  subtitles: [],
};

const STREAMS_HLS = {
  kind: "hls",
  url: "https://cdn.example/live.m3u8",
  title: "Un direct",
  subtitles: [],
};

/// Routes communes : historique, sponsor, streams. `resume` et `mpd` varient.
function routes({ resume = null, mpd = false, streams = STREAMS_PROGRESSIVE } = {}) {
  return [
    { match: "/api/history/watched-ids", json: { ids: [] } },
    {
      match: /\/api\/history\/[^/]+\/resume/,
      method: "GET",
      ...(resume === null ? { status: 404, json: { detail: "Video not in history" } } : { json: resume }),
    },
    { match: /\/api\/history\/[^/]+\/position/, method: "PUT", status: 204 },
    { match: "/api/history", method: "POST", status: 204 },
    { match: "/mpd", ...(mpd ? { status: 200, json: {} } : { status: 404, json: {} }) },
    { match: "/streams", json: streams },
    { match: "/sponsor", json: { segments: [] } },
  ];
}

const VIDEO = makeVideo("dQw4w9WgXcQ", { title: "Une vidéo", platform: "youtube" });

/// Lecteurs créés par le test courant : détruits en sortie, sinon leur
/// pulsation (setInterval) garde le processus de test en vie indéfiniment.
const livePlayers = [];

/// Crée un lecteur prêt à l'emploi + le journal des callbacks.
function makePlayer(el, overrides = {}) {
  const events = { ended: 0, fatal: [], autoplayBlocked: 0, meta: [] };
  const player = new Player(el, {
    onEnded: () => (events.ended += 1),
    onFatal: (m) => events.fatal.push(m),
    onAutoplayBlocked: () => (events.autoplayBlocked += 1),
    onMeta: (s) => events.meta.push(s),
    ...overrides,
  });
  livePlayers.push(player);
  return { player, events };
}

/// Positions PUT sur le serveur, dans l'ordre.
const savedPositions = (api) =>
  api.find("/position", "PUT").map((r) => Math.round(r.body.position));

describe("lecteur web — reprise et playlist", () => {
  beforeEach(async () => {
    env = installDom();
    globalThis.localStorage.clear();
    ({ Player, resumeStart } = await import("../js/player.js"));
    ({ queue } = await import("../js/queue.js"));
    queue.clear();
  });

  afterEach(() => {
    for (const p of livePlayers.splice(0)) {
      try {
        p.destroy();
      } catch {
        /* déjà détruit par le test */
      }
    }
    delete globalThis.window.dashjs;
    delete globalThis.window.Hls;
    delete globalThis.window.mpegts;
  });

  // ─── Règle de reprise (pure) ───

  describe("resumeStart — où la lecture doit démarrer", () => {
    test("aucune position connue → début", () => {
      assert.equal(resumeStart(0, 600), 0);
      assert.equal(resumeStart(null, 600), 0);
      assert.equal(resumeStart(undefined, 600), 0);
      assert.equal(resumeStart(-4, 600), 0);
    });

    test("milieu de vidéo → on y retourne (secondes entières)", () => {
      assert.equal(resumeStart(1, 600), 1);
      assert.equal(resumeStart(300, 600), 300);
      assert.equal(resumeStart(42.9, 600), 42);
    });

    test("94 % vu → on reprend, 95 % → la vidéo est finie", () => {
      assert.equal(resumeStart(564, 600), 564);
      assert.equal(resumeStart(570, 600), 0);
      assert.equal(resumeStart(599, 600), 0);
      assert.equal(resumeStart(900, 600), 0);
    });

    test("durée inconnue → la position stockée fait foi", () => {
      assert.equal(resumeStart(300, null), 300);
      assert.equal(resumeStart(300, 0), 300);
      assert.equal(resumeStart(300, undefined), 300);
    });

    test("parité exacte avec la règle mobile (models.dart)", () => {
      // Même seuil, mêmes bornes : une vidéo laissée à 50 % se reprend au même
      // endroit sur les deux surfaces.
      for (const [pos, dur, want] of [
        [0, 100, 0],
        [1, 100, 1],
        [50, 100, 50],
        [94, 100, 94],
        [95, 100, 0],
        [96, 100, 0],
      ]) {
        assert.equal(resumeStart(pos, dur), want, `${pos}/${dur}`);
      }
    });
  });

  // ─── Reprise, moteur progressif ───

  describe("reprise — flux progressif", () => {
    test("la position stockée est appliquée dès les métadonnées", async () => {
      const api = mockApi(routes({ resume: { position: 300, duration: 600 } }));
      const el = new FakeMediaElement();
      const { player } = makePlayer(el);

      await player.load(VIDEO);
      assert.equal(el.currentTime, 0, "rien avant les métadonnées");
      el.emitLoadedMetadata({ duration: 600 });
      assert.equal(el.currentTime, 300);
      assert.equal(api.find("/resume", "GET").length, 1);
      player.destroy();
    });

    test("les métadonnées déjà là → seek immédiat", async () => {
      mockApi(routes({ resume: { position: 120, duration: 600 } }));
      const el = new FakeMediaElement();
      el.readyState = 1;
      el.duration = 600;
      const { player } = makePlayer(el);

      await player.load(VIDEO);
      assert.equal(el.currentTime, 120);
      player.destroy();
    });

    test("vidéo jamais vue (404) → démarrage au début", async () => {
      mockApi(routes({ resume: null }));
      const el = new FakeMediaElement();
      const { player } = makePlayer(el);

      await player.load(VIDEO);
      el.emitLoadedMetadata({ duration: 600 });
      assert.equal(el.currentTime, 0);
      player.destroy();
    });

    test("vidéo finie à 98 % → recommence au début", async () => {
      mockApi(routes({ resume: { position: 588, duration: 600 } }));
      const el = new FakeMediaElement();
      const { player } = makePlayer(el);

      await player.load(VIDEO);
      el.emitLoadedMetadata({ duration: 600 });
      assert.equal(el.currentTime, 0);
      player.destroy();
    });

    test("un direct ne demande ni n'applique de reprise", async () => {
      globalThis.window.Hls = fakeHls().Hls;
      const api = mockApi(routes({ streams: STREAMS_HLS }));
      const el = new FakeMediaElement();
      const { player } = makePlayer(el);

      await player.load(makeVideo("channel:12345", { platform: "twitch" }));
      assert.equal(api.find("/resume", "GET").length, 0);
      el.emitLoadedMetadata({ duration: 600 });
      el.emitTimeUpdate(30);
      player.destroy();
      await flush();
      assert.deepEqual(savedPositions(api), [], "un direct n'a pas de position");
    });
  });

  // ─── Le point de reprise ne doit jamais être écrasé ───

  describe("protection du point de reprise", () => {
    test("la pulsation n'écrit pas une position d'avant le seek", async () => {
      const api = mockApi(routes({ resume: { position: 300, duration: 600 } }));
      const el = new FakeMediaElement();
      const { player } = makePlayer(el);
      await player.load(VIDEO);

      // Le média s'ouvre et joue quelques secondes AVANT que le seek atterrisse
      // (dash.js/hls.js bufferisent) : ces positions-là effaçaient le signet.
      el.duration = 600;
      el.currentTime = 2;
      player._savePosition();
      await flush();
      assert.deepEqual(savedPositions(api), []);

      player.destroy(); // le flush final est protégé lui aussi
      await flush();
      assert.deepEqual(savedPositions(api), []);
    });

    test("une fois le seek atterri, les positions repartent", async () => {
      const api = mockApi(routes({ resume: { position: 300, duration: 600 } }));
      const el = new FakeMediaElement();
      const { player } = makePlayer(el);
      await player.load(VIDEO);
      el.emitLoadedMetadata({ duration: 600 });
      assert.equal(el.currentTime, 300);

      el.emitTimeUpdate(305); // la lecture avance vraiment
      player._savePosition();
      await flush();
      assert.deepEqual(savedPositions(api), [305]);
    });

    test("un seek utilisateur n'est jamais repris pour un seek du lecteur", async () => {
      // Le navigateur fusionne les seeks rapprochés en un seul évènement : la
      // reprise ne doit pas se reconnaître dans le seek de l'utilisateur, sans
      // quoi la surveillance le ramènerait de force à l'ancienne position.
      mockApi(routes({ resume: { position: 300, duration: 600 } }));
      const el = new FakeMediaElement();
      const { player } = makePlayer(el);
      await player.load(VIDEO);
      el.emitLoadedMetadata({ duration: 600 });
      el.readyState = 4;

      el.currentTime = 10; // l'utilisateur tire la barre en arrière
      el.dispatchEvent(new Event("seeked"));
      for (let i = 0; i < 5; i++) el.emitTimeUpdate(10 + i);
      assert.ok(el.currentTime < 20, `ramené de force à ${el.currentTime}`);
    });

    test("un seek utilisateur en arrière est enregistré", async () => {
      const api = mockApi(routes({ resume: { position: 300, duration: 600 } }));
      const el = new FakeMediaElement();
      const { player } = makePlayer(el);
      await player.load(VIDEO);
      el.emitLoadedMetadata({ duration: 600 });

      // L'utilisateur revient au début : sa position fait foi.
      el.currentTime = 10;
      el.dispatchEvent(new Event("seeked"));
      player._savePosition();
      await flush();
      assert.deepEqual(savedPositions(api), [10]);
    });

    test("position 404 → réenregistrement dans l'historique puis nouvelle tentative", async () => {
      let positionCalls = 0;
      const api = mockApi([
        {
          match: /\/position/,
          method: "PUT",
          match2: null,
          ok: false,
          status: 404,
          json: { detail: "Video not in history" },
        },
        ...routes({ resume: null }),
      ]);
      // Le premier PUT échoue en 404 (l'enregistrement d'historique n'a pas
      // encore atterri) : le lecteur doit réenregistrer puis réessayer.
      const el = new FakeMediaElement();
      const { player } = makePlayer(el);
      await player.load(VIDEO);
      el.emitLoadedMetadata({ duration: 600 });
      el.emitTimeUpdate(45);
      player._savePosition();
      await flush(6);
      positionCalls = api.find("/position", "PUT").length;
      assert.equal(positionCalls, 2, "une seule nouvelle tentative");
      assert.equal(api.find("/api/history", "POST").length, 2, "historique réécrit");
      player.destroy();
    });
  });

  // ─── Reprise sur DASH (toutes les VOD YouTube passent par là) ───

  describe("reprise — DASH (VOD YouTube)", () => {
    beforeEach(() => {
      globalThis.window.dashjs = undefined;
    });

    test("la position est passée à dash.js à l'initialisation", async () => {
      const dash = fakeDashjs();
      globalThis.window.dashjs = dash;
      mockApi(routes({ resume: { position: 300, duration: 600 }, mpd: true, streams: STREAMS_SPLIT }));
      const el = new FakeMediaElement();
      const { player } = makePlayer(el);

      await player.load(VIDEO);
      const init = dash.instances[0].calls.initialize[0];
      // Le 4ᵉ argument est le seul endroit où dash.js accepte un temps de
      // départ : posé sur l'élément, il était écrasé au chargement du manifeste.
      assert.equal(init.startTime, 300);
      assert.equal(init.autoPlay, true);
      player.destroy();
    });

    test("sans reprise, aucun temps de départ n'est imposé", async () => {
      const dash = fakeDashjs();
      globalThis.window.dashjs = dash;
      mockApi(routes({ resume: null, mpd: true, streams: STREAMS_SPLIT }));
      const el = new FakeMediaElement();
      const { player } = makePlayer(el);

      await player.load(VIDEO);
      assert.ok(Number.isNaN(dash.instances[0].calls.initialize[0].startTime));
      player.destroy();
    });

    test("si dash.js redémarre à zéro, la consigne est réappliquée", async () => {
      const dash = fakeDashjs();
      globalThis.window.dashjs = dash;
      mockApi(routes({ resume: { position: 300, duration: 600 }, mpd: true, streams: STREAMS_SPLIT }));
      const el = new FakeMediaElement();
      const { player } = makePlayer(el);
      await player.load(VIDEO);

      // Le manifeste est chargé mais la lecture démarre au début.
      el.readyState = 4;
      el.currentTime = 0;
      dash.instances[0].emit(dash.events.STREAM_INITIALIZED);
      assert.equal(el.currentTime, 300, "reposé dès que dash.js peut chercher");
      player.destroy();
    });

    test("la surveillance ne boucle pas indéfiniment", async () => {
      const dash = fakeDashjs();
      globalThis.window.dashjs = dash;
      mockApi(routes({ resume: { position: 300, duration: 600 }, mpd: true, streams: STREAMS_SPLIT }));
      const el = new FakeMediaElement();
      const { player } = makePlayer(el);
      await player.load(VIDEO);
      el.readyState = 4;

      // Un flux qui refuse obstinément de bouger : au plus 3 tentatives, et
      // jamais plus d'une par seconde (sinon on ferait ramer le décodeur).
      for (let i = 0; i < 20; i++) {
        el.currentTime = 0;
        el.emitTimeUpdate(0);
      }
      assert.ok(player._resumeTries <= 3, `${player._resumeTries} tentatives`);
      player.destroy();
    });

    test("le média démarré au bon endroit est considéré comme réglé", async () => {
      const dash = fakeDashjs();
      globalThis.window.dashjs = dash;
      const api = mockApi(routes({ resume: { position: 300, duration: 600 }, mpd: true, streams: STREAMS_SPLIT }));
      const el = new FakeMediaElement();
      const { player } = makePlayer(el);
      await player.load(VIDEO);

      el.duration = 600;
      el.readyState = 4;
      el.emitTimeUpdate(300);
      assert.equal(player._resumeSettled, true);
      el.emitTimeUpdate(311);
      player._savePosition();
      await flush();
      assert.deepEqual(savedPositions(api), [311]);
      player.destroy();
    });
  });

  // ─── Fin de média : l'enchaînement de la file ───

  describe("fin de lecture — enchaînement de la file", () => {
    test("`ended` natif déclenche exactement un enchaînement", async () => {
      mockApi(routes({ resume: null }));
      const el = new FakeMediaElement();
      const { player, events } = makePlayer(el);
      await player.load(VIDEO);
      el.emitLoadedMetadata({ duration: 600 });

      el.emitEnded();
      assert.equal(events.ended, 1);
      player.destroy();
    });

    test("dash.js : PLAYBACK_ENDED + `ended` natif = un seul enchaînement", async () => {
      const dash = fakeDashjs();
      globalThis.window.dashjs = dash;
      mockApi(routes({ resume: null, mpd: true, streams: STREAMS_SPLIT }));
      const el = new FakeMediaElement();
      const { player, events } = makePlayer(el);
      await player.load(VIDEO);

      // MSE endOfStream fait émettre `ended` à l'élément ET dash.js émet son
      // propre PLAYBACK_ENDED : sans dédoublonnage la file sautait une vidéo.
      dash.instances[0].emit(dash.events.PLAYBACK_ENDED);
      el.emitEnded();
      dash.instances[0].emit(dash.events.PLAYBACK_ENDED);
      assert.equal(events.ended, 1);
      player.destroy();
    });

    test("la fin marque la vidéo comme vue jusqu'au bout", async () => {
      const api = mockApi(routes({ resume: { position: 300, duration: 600 } }));
      const el = new FakeMediaElement();
      const { player } = makePlayer(el);
      await player.load(VIDEO);
      el.emitLoadedMetadata({ duration: 600 });

      el.currentTime = 600;
      el.emitEnded();
      await flush();
      // Position ≈ durée → la prochaine ouverture repart du début.
      const saved = savedPositions(api);
      assert.ok(saved.length >= 1);
      assert.equal(resumeStart(saved.at(-1), 600), 0);
      player.destroy();
    });

    test("la vidéo suivante réarme la fin de lecture", async () => {
      mockApi(routes({ resume: null }));
      const el = new FakeMediaElement();
      const { player, events } = makePlayer(el);
      await player.load(VIDEO);
      el.emitEnded();
      assert.equal(events.ended, 1);

      await player.load(makeVideo("secondvideo1", { platform: "youtube" }));
      el.emitEnded();
      assert.equal(events.ended, 2, "chaque vidéo de la file enchaîne à son tour");
      player.destroy();
    });

    test("plus aucun enchaînement après destruction", async () => {
      mockApi(routes({ resume: null }));
      const el = new FakeMediaElement();
      const { player, events } = makePlayer(el);
      await player.load(VIDEO);
      player.destroy();

      el.emitEnded();
      assert.equal(events.ended, 0);
    });

    test("playlist de bout en bout : une seule avance par vidéo", async () => {
      const dash = fakeDashjs();
      globalThis.window.dashjs = dash;
      mockApi(routes({ resume: null, mpd: true, streams: STREAMS_SPLIT }));
      const el = new FakeMediaElement();
      let advances = 0;
      const { player } = makePlayer(el, { onEnded: () => (advances += 1) });

      for (let i = 0; i < 5; i++) {
        await player.load(makeVideo(`playlistvid${i}`, { platform: "youtube" }));
        el.emitLoadedMetadata({ duration: 300 });
        el.currentTime = 300;
        // Les deux sources de « fin » à chaque titre.
        dash.instances.at(-1).emit(dash.events.PLAYBACK_ENDED);
        el.emitEnded();
      }
      assert.equal(advances, 5, "5 vidéos = 5 avances, ni plus ni moins");
      player.destroy();
    });
  });

  // ─── Chargements dépassés (le lecteur zombie) ───

  describe("chargements dépassés", () => {
    test("quitter la page pendant la résolution ne laisse aucun moteur", async () => {
      const dash = fakeDashjs();
      globalThis.window.dashjs = dash;
      const api = mockApi([
        { match: "/streams", json: STREAMS_SPLIT, delay: 30 },
        ...routes({ resume: { position: 300, duration: 600 }, mpd: true, streams: STREAMS_SPLIT }),
      ]);
      const el = new FakeMediaElement();
      const { player } = makePlayer(el);

      const loading = player.load(VIDEO);
      player.destroy(); // navigation avant la fin de la résolution
      await loading;
      await flush(4);

      assert.equal(dash.instances.length, 0, "aucun moteur ressuscité");
      assert.equal(player._engine, null);
      // Une pulsation zombie réécrirait indéfiniment une position périmée.
      el.duration = 600;
      el.currentTime = 500;
      api.reset();
      await flush(4);
      assert.deepEqual(savedPositions(api), []);
    });

    test("une résolution lente ne double pas la vidéo suivante", async () => {
      const dash = fakeDashjs();
      globalThis.window.dashjs = dash;
      mockApi([
        { match: "/streams", json: STREAMS_SPLIT, delay: 30 },
        ...routes({ resume: null, mpd: true, streams: STREAMS_SPLIT }),
      ]);
      const el = new FakeMediaElement();
      const { player } = makePlayer(el);

      const first = player.load(VIDEO);
      const second = player.load(makeVideo("secondvideo1", { platform: "youtube" }));
      await Promise.all([first, second]);
      await flush(4);

      assert.equal(dash.instances.length, 1, "un seul moteur pour la vidéo affichée");
      assert.equal(player.video.video_id, "secondvideo1");
    });

    test("la position enregistrée est celle de la vidéo affichée", async () => {
      const api = mockApi([
        { match: "/streams", json: STREAMS_PROGRESSIVE, delay: 30 },
        ...routes({ resume: null }),
      ]);
      const el = new FakeMediaElement();
      const { player } = makePlayer(el);

      const first = player.load(VIDEO);
      const second = player.load(makeVideo("secondvideo1", { platform: "youtube" }));
      await Promise.all([first, second]);
      el.emitLoadedMetadata({ duration: 600 });
      el.emitTimeUpdate(75);
      api.reset();
      player._savePosition();
      await flush();

      const puts = api.find("/position", "PUT");
      assert.equal(puts.length, 1);
      assert.ok(puts[0].url.includes("secondvideo1"), puts[0].url);
    });
  });

  // ─── La position survit aux rechargements internes ───

  describe("rechargements internes", () => {
    test("changement de qualité : la lecture reprend où elle en était", async () => {
      const dash = fakeDashjs();
      globalThis.window.dashjs = dash;
      mockApi(routes({ resume: null, mpd: true, streams: STREAMS_SPLIT }));
      const el = new FakeMediaElement();
      const { player } = makePlayer(el);
      await player.load(VIDEO);
      el.emitLoadedMetadata({ duration: 600 });
      el.currentTime = 250;

      await player.setMaxHeight(720);
      assert.equal(dash.instances.at(-1).calls.initialize[0].startTime, 250);
      player.destroy();
    });

    test("flux expiré : la re-résolution repart de la position courante", async () => {
      const dash = fakeDashjs();
      globalThis.window.dashjs = dash;
      mockApi(routes({ resume: null, mpd: true, streams: STREAMS_SPLIT }));
      const el = new FakeMediaElement();
      const { player } = makePlayer(el);
      await player.load(VIDEO);
      el.emitLoadedMetadata({ duration: 600 });
      el.currentTime = 412;

      await player._fatalOrRetry("Flux interrompu");
      assert.equal(dash.instances.at(-1).calls.initialize[0].startTime, 412);
      player.destroy();
    });

    test("pendant un rechargement, la position n'est pas écrasée par zéro", async () => {
      const dash = fakeDashjs();
      globalThis.window.dashjs = dash;
      const api = mockApi(routes({ resume: null, mpd: true, streams: STREAMS_SPLIT }));
      const el = new FakeMediaElement();
      const { player } = makePlayer(el);
      await player.load(VIDEO);
      el.emitLoadedMetadata({ duration: 600 });
      el.currentTime = 412;
      api.reset();

      await player._fatalOrRetry("Flux interrompu");
      el.currentTime = 0; // le nouveau média redémarre du début
      player._savePosition();
      await flush();
      assert.deepEqual(savedPositions(api), []);
      player.destroy();
    });
  });
});
