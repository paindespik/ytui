// Faux environnement navigateur pour tester le front web (ESM vanilla) sous
// `node --test`, sans aucune dépendance npm (pas de jsdom).
//
// Ce que le front touche réellement au chargement des modules — et que le
// harness doit donc fournir AVANT tout `import` de js/ :
//   ui.js       → document.createElement / getElementById / body /
//                 querySelectorAll / addEventListener("click"), Node,
//                 Intl.NumberFormat (natif Node)
//   state.js    → localStorage
//   api.js      → location.origin, fetch, URL (natif)
//   player.js   → prefs.gain (localStorage), navigator.mediaSession,
//                 MediaMetadata, window.AudioContext, window.Hls/dashjs/mpegts
//   shortcuts.js→ HTMLInputElement & co. (uniquement dans initShortcuts)
//
// Usage :
//   const env = installDom();
//   const { Player } = await import("../js/player.js");   // import DYNAMIQUE
//   ...
//   env.restore();
//
// L'import doit être dynamique : un `import` statique est hoisté et
// s'exécuterait avant installDom().

import { EventEmitter } from "node:events";

// ─── Noeuds DOM minimalistes ───

/// Noeud DOM suffisant pour el() de ui.js et les <track> de player.js.
export class FakeNode extends EventTarget {
  constructor(tagName = "div") {
    super();
    this.tagName = String(tagName).toUpperCase();
    this.children = [];
    this.parentNode = null;
    this.attributes = new Map();
    this.dataset = {};
    this.className = "";
    this.textContent = "";
    this.style = {};
    this.classList = {
      _set: new Set(),
      add: (...c) => c.forEach((x) => this.classList._set.add(x)),
      remove: (...c) => c.forEach((x) => this.classList._set.delete(x)),
      toggle: (c, on) => {
        const want = on === undefined ? !this.classList._set.has(c) : Boolean(on);
        if (want) this.classList._set.add(c);
        else this.classList._set.delete(c);
        return want;
      },
      contains: (c) => this.classList._set.has(c),
    };
  }

  setAttribute(k, v) {
    this.attributes.set(k, String(v));
    if (k === "class") this.className = String(v);
  }
  getAttribute(k) {
    return this.attributes.has(k) ? this.attributes.get(k) : null;
  }
  removeAttribute(k) {
    this.attributes.delete(k);
  }
  hasAttribute(k) {
    return this.attributes.has(k);
  }

  append(...nodes) {
    for (const n of nodes.flat(Infinity)) {
      if (n === null || n === undefined || n === false) continue;
      if (n instanceof FakeNode) n.parentNode = this;
      this.children.push(n);
    }
  }
  remove() {
    const p = this.parentNode;
    if (!p) return;
    const i = p.children.indexOf(this);
    if (i !== -1) p.children.splice(i, 1);
    this.parentNode = null;
  }
  replaceChildren(...nodes) {
    for (const c of this.children) if (c instanceof FakeNode) c.parentNode = null;
    this.children = [];
    this.append(...nodes);
  }

  get childElementCount() {
    return this.children.filter((c) => c instanceof FakeNode).length;
  }
  get firstElementChild() {
    return this.children.find((c) => c instanceof FakeNode) || null;
  }

  /// Descendants (profondeur d'abord) filtrés par un sélecteur simple :
  /// "tag", ".class", "#id" — tout ce dont le front se sert réellement.
  querySelectorAll(selector) {
    const out = [];
    const match = (n) => {
      if (!(n instanceof FakeNode)) return false;
      for (const sel of String(selector).split(",").map((s) => s.trim())) {
        if (!sel) continue;
        if (sel.startsWith(".") && n.classList.contains(sel.slice(1))) return true;
        if (sel.startsWith("#") && n.getAttribute("id") === sel.slice(1)) return true;
        if (!sel.startsWith(".") && !sel.startsWith("#") && n.tagName === sel.toUpperCase()) {
          return true;
        }
      }
      return false;
    };
    const walk = (n) => {
      for (const c of n.children) {
        if (match(c)) out.push(c);
        if (c instanceof FakeNode) walk(c);
      }
    };
    walk(this);
    return out;
  }
  querySelector(selector) {
    return this.querySelectorAll(selector)[0] || null;
  }
  closest() {
    return null;
  }
  focus() {}
  blur() {}
  scrollIntoView() {}
  getBoundingClientRect() {
    return { top: 0, left: 0, right: 0, bottom: 0, width: 0, height: 0, x: 0, y: 0 };
  }
  requestFullscreen() {
    return Promise.resolve();
  }
}

/// Élément <video>/<audio> factice : tout ce que player.js lit ou écrit.
///
/// `duration` vaut NaN par défaut (comme un vrai média non chargé), ce qui
/// suffit à faire court-circuiter _savePosition tant que loadedmetadata n'a
/// pas été simulé.
export class FakeMediaElement extends FakeNode {
  constructor(tagName = "video") {
    super(tagName);
    this.currentTime = 0;
    this.duration = NaN;
    this.readyState = 0;
    this.paused = true;
    this.ended = false;
    this.volume = 1;
    this.muted = false;
    this.playbackRate = 1;
    this.videoHeight = 0;
    this.videoWidth = 0;
    this.textTracks = [];
    this.src = "";
    this.autoplay = false;

    /// Journal des appels, pour les assertions.
    this.calls = { play: 0, pause: 0, load: 0, removeAttribute: [] };
    /// Mettre à true pour que play() rejette (autoplay bloqué).
    this.rejectPlay = false;
    /// Erreur renvoyée par le play() rejeté.
    this.playRejection = new Error("NotAllowedError");
  }

  play() {
    this.calls.play += 1;
    if (this.rejectPlay) return Promise.reject(this.playRejection);
    this.paused = false;
    this.ended = false;
    return Promise.resolve();
  }
  pause() {
    this.calls.pause += 1;
    this.paused = true;
  }
  load() {
    this.calls.load += 1;
  }
  removeAttribute(k) {
    this.calls.removeAttribute.push(k);
    super.removeAttribute(k);
    if (k === "src") this.src = "";
  }
  canPlayType() {
    return "";
  }
  querySelectorAll(selector) {
    // player.js ne cherche que les <track> qu'il a lui-même ajoutés.
    return super.querySelectorAll(selector);
  }
  closest() {
    return null;
  }
  requestPictureInPicture() {
    return Promise.resolve();
  }

  // ─── Simulation ───

  /// Métadonnées arrivées : durée connue, readyState ≥ 1 (HAVE_METADATA).
  emitLoadedMetadata({ duration = 600, videoHeight = 0 } = {}) {
    this.duration = duration;
    if (videoHeight) this.videoHeight = videoHeight;
    if (this.readyState < 1) this.readyState = 1;
    this.dispatchEvent(new Event("loadedmetadata"));
  }
  /// Le média est jouable et joue (readyState 4 = HAVE_ENOUGH_DATA).
  emitCanPlay() {
    this.readyState = 4;
    this.paused = false;
    this.dispatchEvent(new Event("canplay"));
  }
  emitTimeUpdate(t) {
    if (t !== undefined) this.currentTime = t;
    this.dispatchEvent(new Event("timeupdate"));
  }
  emitEnded() {
    this.ended = true;
    this.paused = true;
    this.dispatchEvent(new Event("ended"));
  }
  emitError() {
    this.dispatchEvent(new Event("error"));
  }
  emitPlay() {
    this.paused = false;
    this.dispatchEvent(new Event("play"));
  }
  emitVolumeChange() {
    this.dispatchEvent(new Event("volumechange"));
  }
}

// ─── localStorage ───

export class FakeStorage {
  constructor() {
    this.map = new Map();
  }
  getItem(k) {
    return this.map.has(String(k)) ? this.map.get(String(k)) : null;
  }
  setItem(k, v) {
    this.map.set(String(k), String(v));
  }
  removeItem(k) {
    this.map.delete(String(k));
  }
  clear() {
    this.map.clear();
  }
  key(i) {
    return [...this.map.keys()][i] ?? null;
  }
  get length() {
    return this.map.size;
  }
}

// ─── Web Audio factice ───

const audioParam = (value = 0) => ({ value, setValueAtTime(v) { this.value = v; } });

export class FakeAudioContext {
  constructor() {
    /// "running" | "suspended" — mettre "suspended" pour tester la reprise au geste.
    this.state = FakeAudioContext.initialState;
    this.destination = { _id: "destination" };
    this.closed = false;
    this.resumeCalls = 0;
    this.nodes = [];
    FakeAudioContext.instances.push(this);
  }
  _node(kind, extra = {}) {
    const n = {
      kind,
      connections: [],
      connect(target) {
        this.connections.push(target);
        return target;
      },
      disconnect() {
        this.connections = [];
      },
      ...extra,
    };
    this.nodes.push(n);
    return n;
  }
  createGain() {
    return this._node("gain", { gain: audioParam(1) });
  }
  createDynamicsCompressor() {
    return this._node("compressor", {
      threshold: audioParam(-24),
      knee: audioParam(30),
      ratio: audioParam(12),
      attack: audioParam(0.003),
      release: audioParam(0.25),
    });
  }
  createMediaElementSource(el) {
    return this._node("mediaElementSource", { mediaElement: el });
  }
  resume() {
    this.resumeCalls += 1;
    if (FakeAudioContext.rejectResume) return Promise.reject(new Error("not allowed"));
    this.state = "running";
    return Promise.resolve();
  }
  suspend() {
    this.state = "suspended";
    return Promise.resolve();
  }
  close() {
    this.closed = true;
    this.state = "closed";
    return Promise.resolve();
  }
}
FakeAudioContext.instances = [];
FakeAudioContext.initialState = "running";
FakeAudioContext.rejectResume = false;

// ─── Faux moteurs de lecture ───

/// dash.js factice. Retourne { instances, events, MediaPlayer } où
/// `MediaPlayer()` .create() fabrique un lecteur enregistrant ses appels.
///
/// Chaque instance expose :
///   initialize(view, source, autoPlay, startTime) — arguments journalisés
///   on(event, handler) / off / seek / destroy / reset
///   emit(event, payload) — déclenche les handlers enregistrés (test)
export function fakeDashjs() {
  const instances = [];
  const events = {
    ERROR: "error",
    PLAYBACK_ENDED: "playbackEnded",
    PLAYBACK_ERROR: "playbackError",
    STREAM_INITIALIZED: "streamInitialized",
    PLAYBACK_METADATA_LOADED: "playbackMetaDataLoaded",
    CAN_PLAY: "canPlay",
  };

  function createInstance() {
    const handlers = new Map();
    const inst = {
      calls: { initialize: [], seek: [], destroy: 0, reset: 0, updateSettings: [] },
      destroyed: false,
      initialize(view, source, autoPlay, startTime) {
        this.calls.initialize.push({ view, source, autoPlay, startTime });
        this.view = view;
        this.source = source;
        this.autoPlay = autoPlay;
        this.startTime = startTime;
      },
      updateSettings(s) {
        this.calls.updateSettings.push(s);
      },
      on(event, handler) {
        if (!handlers.has(event)) handlers.set(event, []);
        handlers.get(event).push(handler);
      },
      off(event, handler) {
        const list = handlers.get(event) || [];
        const i = list.indexOf(handler);
        if (i !== -1) list.splice(i, 1);
      },
      seek(t) {
        this.calls.seek.push(t);
      },
      destroy() {
        this.calls.destroy += 1;
        this.destroyed = true;
      },
      reset() {
        this.calls.reset += 1;
        this.destroyed = true;
      },
      isReady() {
        return !this.destroyed;
      },
      /// Déclenche à la main un évènement dash.js.
      emit(event, payload = {}) {
        for (const h of [...(handlers.get(event) || [])]) h(payload);
      },
      handlers,
    };
    instances.push(inst);
    return inst;
  }

  const MediaPlayer = () => ({ create: createInstance });
  MediaPlayer.events = events;
  return { MediaPlayer, instances, events };
}

/// hls.js factice : classe Hls constructible, instances journalisées.
export function fakeHls({ supported = true } = {}) {
  const instances = [];
  const Events = { ERROR: "hlsError", MANIFEST_PARSED: "hlsManifestParsed" };

  class Hls {
    constructor(config = {}) {
      this.config = config;
      this.handlers = new Map();
      this.calls = { loadSource: [], attachMedia: [], destroy: 0, startLoad: [] };
      this.destroyed = false;
      instances.push(this);
    }
    on(event, handler) {
      if (!this.handlers.has(event)) this.handlers.set(event, []);
      this.handlers.get(event).push(handler);
    }
    off(event, handler) {
      const list = this.handlers.get(event) || [];
      const i = list.indexOf(handler);
      if (i !== -1) list.splice(i, 1);
    }
    loadSource(url) {
      this.calls.loadSource.push(url);
      this.url = url;
    }
    attachMedia(el) {
      this.calls.attachMedia.push(el);
      this.media = el;
    }
    startLoad(pos) {
      this.calls.startLoad.push(pos);
    }
    destroy() {
      this.calls.destroy += 1;
      this.destroyed = true;
    }
    /// Déclenche un évènement hls.js (signature (event, data)).
    emit(event, data = {}) {
      for (const h of [...(this.handlers.get(event) || [])]) h(event, data);
    }
  }
  Hls.isSupported = () => supported;
  Hls.Events = Events;
  return { Hls, instances, Events };
}

/// mpegts.js factice (lives FLV).
export function fakeMpegts({ supported = true } = {}) {
  const instances = [];
  const Events = { ERROR: "error", LOADING_COMPLETE: "loading_complete" };

  function createPlayer(config) {
    const handlers = new Map();
    const inst = {
      config,
      calls: { load: 0, destroy: 0, attachMediaElement: [], play: 0, unload: 0 },
      destroyed: false,
      on(event, handler) {
        if (!handlers.has(event)) handlers.set(event, []);
        handlers.get(event).push(handler);
      },
      off(event, handler) {
        const list = handlers.get(event) || [];
        const i = list.indexOf(handler);
        if (i !== -1) list.splice(i, 1);
      },
      attachMediaElement(el) {
        this.calls.attachMediaElement.push(el);
        this.media = el;
      },
      load() {
        this.calls.load += 1;
      },
      play() {
        this.calls.play += 1;
      },
      unload() {
        this.calls.unload += 1;
      },
      destroy() {
        this.calls.destroy += 1;
        this.destroyed = true;
      },
      emit(event, payload = {}) {
        for (const h of [...(handlers.get(event) || [])]) h(payload);
      },
      handlers,
    };
    instances.push(inst);
    return inst;
  }

  return { mpegts: { createPlayer, isSupported: () => supported, Events }, instances, Events };
}

// ─── fetch mockable ───

/// Installe un `fetch` global qui répond selon `routes` et journalise tout.
///
/// routes : tableau de { match, method, status, json, body, ok, delay } où
/// `match` est une string (sous-chaîne de l'URL), une RegExp ou une fonction
/// (url, init) => bool. La première route qui matche gagne ; sans route
/// correspondante la réponse est 404 { detail: "not found" }.
///
/// Renvoie { requests, routes, add(route), reset() } — `requests` contient
/// { method, url, body (parsé si JSON), raw }.
export function mockApi(routes = []) {
  const list = [...routes];
  const requests = [];

  const matches = (route, url, init) => {
    const method = (init.method || "GET").toUpperCase();
    if (route.method && route.method.toUpperCase() !== method) return false;
    const m = route.match;
    if (m === undefined) return true;
    if (typeof m === "function") return Boolean(m(url, init));
    if (m instanceof RegExp) return m.test(url);
    return url.includes(String(m));
  };

  const impl = async (input, init = {}) => {
    const url = typeof input === "string" ? input : String(input);
    const method = (init.method || "GET").toUpperCase();
    let parsed;
    if (init.body !== undefined) {
      try {
        parsed = JSON.parse(init.body);
      } catch {
        parsed = init.body;
      }
    }
    requests.push({ method, url, body: parsed, raw: init.body, init });

    const route = list.find((r) => matches(r, url, init));
    if (route && route.delay) await new Promise((r) => setTimeout(r, route.delay));
    if (route && route.throws) throw route.throws;

    const status = route ? (route.status ?? 200) : 404;
    const payload = route
      ? route.json !== undefined
        ? route.json
        : route.body
      : { detail: "not found" };
    const ok = route && route.ok !== undefined ? route.ok : status >= 200 && status < 300;

    return {
      ok,
      status,
      url,
      headers: new Map(),
      async json() {
        if (payload === undefined) throw new Error("no JSON body");
        return typeof payload === "function" ? payload() : payload;
      },
      async text() {
        return JSON.stringify(payload ?? "");
      },
    };
  };

  globalThis.fetch = impl;
  if (globalThis.window) globalThis.window.fetch = impl;

  return {
    requests,
    routes: list,
    add(route) {
      list.unshift(route);
      return this;
    },
    /// Requêtes filtrées par sous-chaîne d'URL (et méthode optionnelle).
    find(substr, method) {
      return requests.filter(
        (r) => r.url.includes(substr) && (!method || r.method === method.toUpperCase()),
      );
    },
    reset() {
      requests.length = 0;
    },
  };
}

// ─── Installation des globals ───

let installed = null;

/// Pose window/document/location/localStorage/navigator/fetch/... sur
/// globalThis. Idempotent : un second appel renvoie l'environnement existant.
/// `restore()` remet exactement les valeurs d'origine.
export function installDom({ href = "http://localhost:8000/" } = {}) {
  if (installed) return installed;

  const saved = new Map();
  const KEYS = [
    "window",
    "document",
    "location",
    "localStorage",
    "sessionStorage",
    "navigator",
    "fetch",
    "history",
    "MediaMetadata",
    "AudioContext",
    "webkitAudioContext",
    "Node",
    "HTMLElement",
    "HTMLInputElement",
    "HTMLTextAreaElement",
    "HTMLSelectElement",
    "Image",
  ];
  for (const k of KEYS) {
    saved.set(k, Object.getOwnPropertyDescriptor(globalThis, k));
  }
  const define = (k, value) => {
    Object.defineProperty(globalThis, k, {
      value,
      writable: true,
      configurable: true,
      enumerable: true,
    });
  };

  // location : hash mutable + replace() qui met à jour le href.
  const url = new URL(href);
  const location = {
    get href() {
      return url.href;
    },
    set href(v) {
      const next = new URL(v, url.href);
      url.href = next.href;
      location._notifyHash();
    },
    get origin() {
      return url.origin;
    },
    get pathname() {
      return url.pathname;
    },
    get search() {
      return url.search;
    },
    get hash() {
      return url.hash;
    },
    set hash(v) {
      const before = url.hash;
      url.hash = String(v);
      if (url.hash !== before) location._notifyHash();
    },
    replace(v) {
      location.replaceCalls.push(String(v));
      const next = new URL(String(v), url.href);
      const before = url.hash;
      url.href = next.href;
      if (url.hash !== before) location._notifyHash();
    },
    assign(v) {
      location.href = v;
    },
    reload() {},
    /// Journal des appels à replace() (avance de file d'attente).
    replaceCalls: [],
    _notifyHash() {
      globalThis.window?.dispatchEvent?.(new Event("hashchange"));
    },
    toString() {
      return url.href;
    },
  };

  // document
  const body = new FakeNode("body");
  const head = new FakeNode("head");
  const documentElement = new FakeNode("html");
  const byId = new Map();
  const docTarget = new EventTarget();

  const document = {
    body,
    head,
    documentElement,
    title: "",
    fullscreenElement: null,
    pictureInPictureEnabled: false,
    createElement(tag) {
      return tag === "video" || tag === "audio" ? new FakeMediaElement(tag) : new FakeNode(tag);
    },
    createTextNode(text) {
      const n = new FakeNode("#text");
      n.textContent = String(text);
      return n;
    },
    getElementById(id) {
      if (byId.has(id)) return byId.get(id);
      const found = body.querySelector("#" + id);
      return found || null;
    },
    /// Enregistre un noeud accessible par getElementById (ex. #toasts, #view).
    _register(id, node) {
      node.setAttribute("id", id);
      byId.set(id, node);
      return node;
    },
    querySelector(sel) {
      return body.querySelector(sel);
    },
    querySelectorAll(sel) {
      return body.querySelectorAll(sel);
    },
    addEventListener: docTarget.addEventListener.bind(docTarget),
    removeEventListener: docTarget.removeEventListener.bind(docTarget),
    dispatchEvent: docTarget.dispatchEvent.bind(docTarget),
    exitFullscreen() {
      document.fullscreenElement = null;
      return Promise.resolve();
    },
    exitPictureInPicture() {
      return Promise.resolve();
    },
  };

  // Cibles standard du front, disponibles d'office.
  document._register("view", new FakeNode("div"));
  document._register("toasts", new FakeNode("div"));
  document._register("modal-root", new FakeNode("div"));
  body.append(
    document.getElementById("view"),
    document.getElementById("toasts"),
    document.getElementById("modal-root"),
  );

  // window
  const winTarget = new EventTarget();
  const window = {
    location,
    document,
    innerWidth: 1280,
    innerHeight: 800,
    devicePixelRatio: 1,
    addEventListener: winTarget.addEventListener.bind(winTarget),
    removeEventListener: winTarget.removeEventListener.bind(winTarget),
    dispatchEvent: winTarget.dispatchEvent.bind(winTarget),
    scrollTo() {},
    matchMedia: () => ({
      matches: false,
      addEventListener() {},
      removeEventListener() {},
      addListener() {},
      removeListener() {},
    }),
    getComputedStyle: () => ({ getPropertyValue: () => "" }),
    requestAnimationFrame: (cb) => setTimeout(() => cb(Date.now()), 0),
    cancelAnimationFrame: (h) => clearTimeout(h),
    setTimeout,
    clearTimeout,
    setInterval,
    clearInterval,
    AudioContext: FakeAudioContext,
    webkitAudioContext: FakeAudioContext,
    // Moteurs : posés à la demande par les tests (window.Hls = ..., etc.)
    Hls: undefined,
    dashjs: undefined,
    mpegts: undefined,
  };

  // navigator + Media Session
  const mediaSession = {
    metadata: null,
    playbackState: "none",
    handlers: new Map(),
    setActionHandler(action, handler) {
      if (handler === null) this.handlers.delete(action);
      else this.handlers.set(action, handler);
    },
    /// Déclenche une touche média OS (play/pause/nexttrack/previoustrack).
    trigger(action, details = {}) {
      const h = this.handlers.get(action);
      if (!h) return false;
      h(details);
      return true;
    },
  };
  const navigator = {
    userAgent: "node-test",
    language: "fr-FR",
    languages: ["fr-FR", "fr"],
    mediaSession,
    onLine: true,
  };

  class MediaMetadata {
    constructor({ title = "", artist = "", album = "", artwork = [] } = {}) {
      this.title = title;
      this.artist = artist;
      this.album = album;
      this.artwork = artwork;
    }
  }

  const history = {
    entries: [],
    back() {
      this.entries.push("back");
    },
    forward() {
      this.entries.push("forward");
    },
    pushState() {},
    replaceState() {},
  };

  class HTMLElement extends FakeNode {}
  class HTMLInputElement extends HTMLElement {}
  class HTMLTextAreaElement extends HTMLElement {}
  class HTMLSelectElement extends HTMLElement {}

  define("window", window);
  define("document", document);
  define("location", location);
  define("localStorage", new FakeStorage());
  define("sessionStorage", new FakeStorage());
  define("navigator", navigator);
  define("history", history);
  define("MediaMetadata", MediaMetadata);
  define("AudioContext", FakeAudioContext);
  define("webkitAudioContext", FakeAudioContext);
  define("Node", FakeNode);
  define("HTMLElement", HTMLElement);
  define("HTMLInputElement", HTMLInputElement);
  define("HTMLTextAreaElement", HTMLTextAreaElement);
  define("HTMLSelectElement", HTMLSelectElement);
  define("Image", class Image extends FakeNode {});
  // fetch par défaut : 404 sur tout tant que mockApi() n'a pas été appelé.
  mockApi([]);

  FakeAudioContext.instances.length = 0;
  FakeAudioContext.initialState = "running";
  FakeAudioContext.rejectResume = false;

  installed = {
    window,
    document,
    location,
    navigator,
    mediaSession,
    history,
    localStorage: globalThis.localStorage,
    body,
    /// Crée un <video> factice déjà rattaché au document.
    videoElement() {
      const v = new FakeMediaElement("video");
      body.append(v);
      return v;
    },
    /// Remet les globals d'origine.
    restore() {
      for (const [k, desc] of saved) {
        if (desc) Object.defineProperty(globalThis, k, desc);
        else delete globalThis[k];
      }
      installed = null;
    },
  };
  return installed;
}

/// Environnement courant (null si installDom() n'a pas encore été appelé).
export function currentEnv() {
  return installed;
}

/// Laisse tourner la boucle d'évènements (promesses + timers déjà échus).
export async function flush(times = 3) {
  for (let i = 0; i < times; i++) await new Promise((r) => setTimeout(r, 0));
}

/// Attend `ms` ms réels (pour les temporisations de 1 s de player.js, préférer
/// un faux timer côté test si la durée devient gênante).
export const wait = (ms) => new Promise((r) => setTimeout(r, ms));

/// Vidéo de test au format attendu par le front (snake_case, comme l'API).
export function makeVideo(videoId, overrides = {}) {
  return {
    video_id: videoId,
    platform: "youtube",
    title: `Vidéo ${videoId}`,
    channel_title: "Chaîne de test",
    channel_id: "UCtest000000000000000000",
    thumbnail_url: `https://example.invalid/${videoId}.jpg`,
    kind: "video",
    duration: 600,
    published: null,
    playlist_id: "",
    ...overrides,
  };
}

/// Playlist de n vidéos (ids v1..vn).
export function makePlaylist(n, overrides = {}) {
  return Array.from({ length: n }, (_, i) => makeVideo(`v${i + 1}`, overrides));
}

export { EventEmitter };
