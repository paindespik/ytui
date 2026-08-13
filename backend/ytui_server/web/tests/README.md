# Tests du front web

Tests du front web vanilla (`web/js/`, `web/views/`) avec le **lanceur de tests
intégré à Node** (`node:test` + `node:assert`).

**Zéro dépendance npm** : pas de jsdom, pas de package.json, rien à installer.
Le faux environnement navigateur est fourni par `harness.mjs`.

Prérequis : **Node ≥ 20** (vérifié sous v22). Node 18 refuse les ESM écrits en
`.js` sans `"type": "module"` — or le front est servi tel quel au navigateur,
qui n'a pas besoin de ce fichier. La CI utilise donc l'image `node:22`.

## Lancer les tests

Depuis la racine du dépôt :

```sh
node --test "backend/ytui_server/web/tests/*.test.mjs"
```

Les guillemets sont importants : c'est Node qui doit recevoir le motif, pas le
shell.

Depuis `backend/ytui_server/web/` (Node découvre les fichiers `*.test.mjs`
tout seul) :

```sh
cd backend/ytui_server/web && node --test
```

Un seul fichier :

```sh
node --test backend/ytui_server/web/tests/queue.test.mjs
```

Options utiles :

```sh
# filtrer par nom de test
node --test --test-name-pattern="jumpTo" "backend/ytui_server/web/tests/*.test.mjs"

# sortie lisible plutôt que TAP
node --test --test-reporter=spec "backend/ytui_server/web/tests/*.test.mjs"

# surveiller les fichiers
node --test --watch "backend/ytui_server/web/tests/*.test.mjs"
```

> ⚠️ `node --test backend/ytui_server/web/tests/` (un **répertoire** en argument
> positionnel) échoue avec `MODULE_NOT_FOUND` sous Node 22 : Node tente de
> résoudre le chemin comme un module. Utiliser une des formes ci-dessus.

> ℹ️ Un avertissement `MODULE_TYPELESS_PACKAGE_JSON` peut s'afficher : les fichiers de
> `web/js/` sont des ESM en `.js` sans `package.json` déclarant
> `"type": "module"`. C'est purement cosmétique (Node les reparse en ESM), les
> tests passent et le code de production est servi par le navigateur en
> `<script type="module">`. Ne pas ajouter de `package.json` juste pour ça.

Le code de sortie vaut 0 si tout passe, non nul sinon : utilisable tel quel en CI.

## Fichiers

| Fichier | Rôle |
|---|---|
| `harness.mjs` | Faux environnement navigateur réutilisable (DOM, `location`, `localStorage`, `fetch`, Web Audio, moteurs de lecture). Aucun test. |
| `queue.test.mjs` | File de lecture (`js/queue.js`) : lecture de playlist, `jumpTo` positionnel, `removeUpcoming`, évènements `change`. |
| `player.test.mjs` | Lecteur (`js/player.js`) : reprise d'une vidéo partiellement vue (progressif et dash.js), protection du point de reprise, fin de média dédoublonnée, chargements dépassés. |
| `router.test.mjs` | Routeur (`js/router.js`) : une seule vue montée à la fois, même quand un rendu lent se termine après une autre navigation. |
| `e2e/` | Scénarios en vrai navigateur sur un vrai backend (manuels, hors CI) — voir `e2e/README.md`. |

## Écrire un nouveau test

`installDom()` doit être appelé **avant** d'importer un module du front, donc
l'import doit être **dynamique** (`await import(...)`) : un `import` statique est
hoisté et s'exécuterait avant la pose des globals.

```js
import test from "node:test";
import assert from "node:assert/strict";
import { installDom, mockApi, makeVideo } from "./harness.mjs";

const env = installDom();
const { Player } = await import("../js/player.js");

test("reprend la lecture à la position enregistrée", async () => {
  mockApi([
    { match: "/api/history", method: "POST", status: 204 },
    { match: "/resume", json: { position: 120.5, duration: 600, playlist_id: "" } },
    { match: "/streams", json: { kind: "progressive", url: "https://cdn.invalid/a.mp4", subtitles: [] } },
    { match: "/mpd", status: 404, ok: false },
  ]);

  const el = env.videoElement();
  const player = new Player(el, {});
  await player.load(makeVideo("vid1"));

  el.emitLoadedMetadata({ duration: 600 });
  assert.equal(el.currentTime, 120); // resumeStart tronque à l'entier

  player.destroy();
});
```

## API de `harness.mjs`

### Environnement

- **`installDom({ href = "http://localhost:8000/" }) → env`**
  Pose `window`, `document`, `location`, `localStorage`, `sessionStorage`,
  `navigator` (avec `mediaSession`), `history`, `fetch`, `MediaMetadata`,
  `AudioContext`, `Node`, `HTMLElement` & co. sur `globalThis`.
  **Idempotent** : un second appel renvoie l'environnement existant.
  `env` expose `{ window, document, location, navigator, mediaSession, history,
  localStorage, body, videoElement(), restore() }`.
  - `env.videoElement()` → un `FakeMediaElement` déjà rattaché au `body`.
  - `env.location.replaceCalls` → journal des `location.replace()` (l'avance de
    file d'attente y passe).
  - `env.mediaSession.trigger(action)` → simule une touche média de l'OS.
  - `env.restore()` → restaure exactement les globals d'origine.
  `#view`, `#toasts` et `#modal-root` existent d'office (`ui.js` en dépend).
- **`currentEnv() → env | null`** — environnement courant.
- **`flush(times = 3)`** — laisse tourner la boucle d'évènements.
- **`wait(ms)`** — attente réelle.

### Élément média

- **`class FakeMediaElement extends FakeNode`**
  Propriétés : `currentTime` (0), `duration` (**NaN**), `readyState` (0),
  `paused`, `ended`, `volume`, `muted`, `playbackRate`, `videoHeight`,
  `textTracks` (tableau), `src`.
  Méthodes : `play()` → `Promise` résolue, `pause()`, `load()`,
  `removeAttribute()`, `append()`, `querySelectorAll()`, `closest()` → `null`,
  `canPlayType()` → `''`.
  Journal : `el.calls = { play, pause, load, removeAttribute[] }`.
  Autoplay bloqué : `el.rejectPlay = true` (rejette `el.playRejection`).
  Simulation : `emitLoadedMetadata({ duration, videoHeight })`, `emitCanPlay()`,
  `emitTimeUpdate(t)`, `emitEnded()`, `emitError()`, `emitPlay()`,
  `emitVolumeChange()`.
- **`class FakeNode extends EventTarget`** — noeud DOM minimal
  (`append`/`remove`/`replaceChildren`/`querySelectorAll` sur `tag`, `.class`,
  `#id`).
- **`class FakeStorage`** — `localStorage` en mémoire (`Map`).

### Réseau

- **`mockApi(routes) → { requests, routes, add(route), find(substr, method), reset() }`**
  Installe un `fetch` global répondant selon `routes` et journalisant tout.
  Une route : `{ match, method, status, json, ok, delay, throws }` où `match` est
  une sous-chaîne d'URL, une `RegExp` ou `(url, init) => bool`. Première route
  qui matche ; sans correspondance → `404 { detail: "not found" }`.
  `requests[]` contient `{ method, url, body (JSON parsé), raw, init }`.

### Moteurs de lecture

- **`fakeDashjs() → { MediaPlayer, instances, events }`**
  `MediaPlayer().create()` → instance avec
  `initialize(view, source, autoPlay, startTime)`, `on`/`off`, `seek`,
  `destroy`, `reset`, `updateSettings`, `isReady()`, et `emit(event, payload)`
  pour déclencher un évènement à la main. Appels journalisés dans
  `inst.calls`. `events` contient `ERROR`, `PLAYBACK_ENDED`, `PLAYBACK_ERROR`,
  `STREAM_INITIALIZED`, `PLAYBACK_METADATA_LOADED`, `CAN_PLAY`.
- **`fakeHls({ supported = true }) → { Hls, instances, Events }`**
  Classe `Hls` constructible avec `loadSource`, `attachMedia`, `startLoad`,
  `destroy`, `on`/`off`, `emit(event, data)`. `Hls.isSupported()` pilotable.
- **`fakeMpegts({ supported = true }) → { mpegts, instances, Events }`**
  `mpegts.createPlayer(config)` → instance avec `attachMediaElement`, `load`,
  `play`, `unload`, `destroy`, `on`/`off`, `emit`.

À poser sur `window` avant `player.load()` :

```js
const dash = fakeDashjs();
window.dashjs = dash;                  // → moteur "dash"
window.Hls = fakeHls().Hls;            // → moteur "hls"
window.mpegts = fakeMpegts().mpegts;   // → moteur "mpegts" (lives FLV)
```

### Web Audio

- **`class FakeAudioContext`** — `createGain`, `createDynamicsCompressor`,
  `createMediaElementSource`, `resume`, `suspend`, `close`.
  Statiques pilotables : `FakeAudioContext.instances`,
  `FakeAudioContext.initialState` (`"running"` par défaut, mettre `"suspended"`
  pour tester la reprise au premier geste) et `FakeAudioContext.rejectResume`.

### Fixtures

- **`makeVideo(videoId, overrides)`** — vidéo au format API (snake_case).
- **`makePlaylist(n, overrides)`** — playlist de `n` vidéos (`v1`…`vn`).
