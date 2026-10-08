// Lecteur public d'un lien de partage ytui (/s/{token}).
//
// Aucune session ici : la page ne parle qu'aux routes /s/{token}/…, qui ne
// servent que LA vidéo partagée — toutes les URL de média viennent du
// serveur, déjà signées pour ce lien. Le serveur renvoie des sources
// ordonnées (cf. routers/share_public.py) ; ce lecteur les essaie dans
// l'ordre, avec les mêmes moteurs que le lecteur principal (js/player.js) :
//   dash        → dash.js (manifeste MPD YouTube, seule voie > 360p)
//   hls         → hls.js (Safari natif en secours)
//   flv         → mpegts.js (lives TikTok)
//   progressive → élément <video> natif
// Les bibliothèques ne sont chargées qu'au besoin. Une source qui échoue
// passe à la suivante ; toutes épuisées → une re-résolution complète (URL
// expirées), puis message.

export const QUALITIES = [360, 480, 720, 1080, 1440, 2160];
export const DEFAULT_QUALITY = 1080;
const QUALITY_KEY = "ytui.share.quality";
const SKIP_THROTTLE_MS = 1000;
// Une re-résolution « URL expirées » au plus par minute : au-delà, la vidéo
// est vraiment indisponible et on le dit au lieu de boucler.
const RETRY_COOLDOWN_MS = 60_000;

const VENDOR = {
  dash: "/vendor/dash.all.min.js",
  hls: "/vendor/hls.min.js",
  flv: "/vendor/mpegts.min.js",
};

const loadedScripts = new Map();

/// Charge (une seule fois) un script vendor classique — window.dashjs etc.
export function loadScript(src) {
  if (!loadedScripts.has(src)) {
    loadedScripts.set(
      src,
      new Promise((resolve, reject) => {
        const s = document.createElement("script");
        s.src = src;
        s.onload = () => resolve();
        s.onerror = () => {
          loadedScripts.delete(src);
          reject(new Error(`chargement de ${src} impossible`));
        };
        document.head.append(s);
      }),
    );
  }
  return loadedScripts.get(src);
}

/// Langues de sous-titres demandées : celles du navigateur, puis fr/en (les
/// pistes manuelles arrivent toujours ; ceci ne règle que les automatiques).
export function subtitleLangs(languages) {
  const langs = (languages || []).map((l) => String(l).split("-")[0].toLowerCase());
  return [...new Set([...langs, "fr", "en"].filter(Boolean))].slice(0, 4).join(",");
}

const ERRORS = {
  404: "Ce lien n'est plus valable : il a expiré ou a été révoqué.",
  410: "Ce live est terminé.",
  415: "Ce contenu n'est pas une vidéo lisible.",
};

function store(storage, key, value) {
  try {
    if (value === null) storage.removeItem(key);
    else storage.setItem(key, String(value));
  } catch {
    /* stockage indisponible (navigation privée) */
  }
}

function read(storage, key) {
  try {
    return storage.getItem(key);
  } catch {
    return null;
  }
}

export class SharePlayer {
  /// ui : { video, message, title, quality, served, subs, skip, footer }
  /// (éléments de la page). `load` charge un script vendor (injectable en test).
  constructor({ token, platform = "", ui, storage = globalThis.localStorage, load = loadScript }) {
    this.base = `/s/${encodeURIComponent(token)}`;
    this.platform = platform;
    this.ui = ui;
    this.video = ui.video;
    this.storage = storage;
    this.load = load;
    this.positionKey = `ytui.share.pos.${token}`;
    this.maxHeight = Number(read(storage, QUALITY_KEY)) || DEFAULT_QUALITY;
    this.engine = null;
    this.sources = [];
    this.subtitles = [];
    this.index = 0;
    this.live = false;
    this.segments = [];
    this.lastSkip = 0;
    this.lastRetry = 0;
    this.resumeTarget = 0;
    this.attachToken = 0;
    this.deferT = null;
    this.skipT = null;
    this.destroyed = false;

    this._onError = () => {
      if (this.engine && this.engine.native) this._engineFailed();
    };
    this._onTimeUpdate = () => this._skipSponsor();
    this._onPlaying = () => this._settleResume();
    this._onResize = () => this._showServed();
    this._onPageHide = () => this.savePosition();
    this.video.addEventListener("error", this._onError);
    this.video.addEventListener("timeupdate", this._onTimeUpdate);
    this.video.addEventListener("playing", this._onPlaying);
    this.video.addEventListener("resize", this._onResize);
    this.video.addEventListener("loadedmetadata", this._onResize);
    globalThis.window?.addEventListener?.("pagehide", this._onPageHide);
    this._saveT = setInterval(() => this.savePosition(), 5000);
    this._setupQuality();
    this._setupSubtitles();
  }

  // ─── Démarrage / résolution ───

  /// Point d'entrée : position mémorisée de ce lien (sinon le début).
  async start() {
    this._loadSponsor();
    await this.attach(Number(read(this.storage, this.positionKey)) || 0);
  }

  async attach(startAt = 0) {
    const token = ++this.attachToken;
    this._teardown();
    this._message("Chargement…", { loading: true });
    let data;
    try {
      data = await this._fetchStreams();
    } catch (err) {
      if (token === this.attachToken) this._message(err.message);
      return;
    }
    if (token !== this.attachToken || this.destroyed) return;
    this.live = Boolean(data.is_live);
    if (data.title) {
      this.ui.title.textContent = data.title;
      document.title = data.title;
    }
    this.sources = data.sources || [];
    this.subtitles = data.subtitles || [];
    this.index = 0;
    await this._trySources(this.live ? 0 : startAt, token);
  }

  async _fetchStreams() {
    const params = new URLSearchParams({
      max_height: String(this.maxHeight),
      sub_langs: subtitleLangs(globalThis.navigator?.languages),
    });
    let resp;
    try {
      resp = await fetch(`${this.base}/streams?${params}`);
    } catch {
      throw new Error("Serveur injoignable — réessayez dans un instant.");
    }
    if (!resp.ok) {
      throw new Error(ERRORS[resp.status] || "Vidéo momentanément indisponible — réessayez plus tard.");
    }
    return resp.json();
  }

  async _trySources(startAt, token) {
    while (this.index < this.sources.length) {
      const source = this.sources[this.index];
      let ok = false;
      try {
        ok = await this._attachSource(source, startAt, token);
      } catch (err) {
        console.warn("ytui share: source en échec", source.engine, err);
      }
      if (token !== this.attachToken || this.destroyed) return;
      if (ok) {
        this._message(null);
        this._addSubtitleTracks();
        return;
      }
      this.index += 1;
    }
    this._message("Lecture impossible pour le moment — réessayez plus tard.");
  }

  async _attachSource(source, startAt, token) {
    const stale = () => token !== this.attachToken || this.destroyed;
    this.resumeTarget = startAt > 0 ? startAt : 0;
    const el = this.video;
    if (source.engine === "dash") {
      // 404 sans paire split exploitable : inutile de charger dash.js.
      const ok = await fetch(source.url).then((r) => r.ok).catch(() => false);
      if (!ok || stale()) return false;
      await this.load(VENDOR.dash);
      if (stale()) return false;
      const dashjs = window.dashjs;
      const p = dashjs.MediaPlayer().create();
      p.on(dashjs.MediaPlayer.events.ERROR, () => this._deferFailure());
      // URL absolue : le module CMCD de dash.js fait new URL(source) sans base.
      const url = new URL(source.url, location.origin).href;
      p.initialize(el, url, true, startAt > 0 ? startAt : NaN);
      this.engine = { kind: "dash", destroy: () => (p.destroy || p.reset).call(p) };
    } else if (source.engine === "hls") {
      await this.load(VENDOR.hls);
      if (stale()) return false;
      const Hls = window.Hls;
      if (Hls && Hls.isSupported()) {
        const h = new Hls();
        h.on(Hls.Events.ERROR, (_evt, data) => {
          if (data && data.fatal) this._engineFailed();
        });
        h.loadSource(source.url);
        h.attachMedia(el);
        this.engine = { kind: "hls", destroy: () => h.destroy() };
      } else if (el.canPlayType("application/vnd.apple.mpegurl")) {
        el.src = source.url; // Safari
        this.engine = { kind: "native-hls", native: true, destroy: () => {} };
      } else {
        return false;
      }
      this._seekOnReady(startAt);
    } else if (source.engine === "flv") {
      await this.load(VENDOR.flv);
      if (stale()) return false;
      const mpegts = window.mpegts;
      if (!(mpegts && mpegts.isSupported())) return false;
      const m = mpegts.createPlayer({ type: "flv", isLive: true, url: source.url });
      m.on(mpegts.Events.ERROR, () => this._deferFailure());
      m.attachMediaElement(el);
      m.load();
      this.engine = { kind: "flv", destroy: () => m.destroy() };
    } else {
      el.src = source.url;
      this.engine = { kind: "progressive", native: true, destroy: () => {} };
      this._seekOnReady(startAt);
    }
    this._play();
    return true;
  }

  _play() {
    const p = this.video.play();
    // Autoplay bloqué (aucun geste encore) : les contrôles natifs prennent le relais.
    if (p && typeof p.catch === "function") p.catch(() => {});
  }

  // ─── Erreurs ───

  // dash.js / mpegts émettent du bruit non fatal pendant que la lecture
  // continue : différer 1 s et revérifier avant d'agir (leçon du lecteur web).
  _deferFailure() {
    clearTimeout(this.deferT);
    this.deferT = setTimeout(() => {
      const el = this.video;
      const playing = !el.paused && !el.ended && el.readyState >= 3;
      if (!playing) this._engineFailed();
    }, 1000);
  }

  _engineFailed() {
    if (!this.engine || this.destroyed) return;
    const pos = this.live ? 0 : Math.floor(this.video.currentTime || 0);
    const token = this.attachToken;
    this._teardown();
    this.index += 1;
    if (this.index < this.sources.length) {
      this._trySources(pos, token);
      return;
    }
    const now = Date.now();
    if (now - this.lastRetry > RETRY_COOLDOWN_MS) {
      this.lastRetry = now;
      this.attach(pos); // URL expirées : nouvelle résolution complète
      return;
    }
    this._message("Lecture impossible pour le moment — réessayez plus tard.");
  }

  _teardown() {
    clearTimeout(this.deferT);
    const engine = this.engine;
    this.engine = null;
    if (engine) {
      try {
        engine.destroy();
      } catch {
        /* déjà détruit */
      }
    }
    const el = this.video;
    el.removeAttribute("src");
    for (const t of el.querySelectorAll("track")) t.remove();
    try {
      el.load();
    } catch {
      /* reset best-effort */
    }
  }

  destroy() {
    this.destroyed = true;
    this.attachToken += 1;
    this.savePosition();
    clearInterval(this._saveT);
    clearTimeout(this.skipT);
    this._teardown();
    this.video.removeEventListener("error", this._onError);
    this.video.removeEventListener("timeupdate", this._onTimeUpdate);
    this.video.removeEventListener("playing", this._onPlaying);
    this.video.removeEventListener("resize", this._onResize);
    this.video.removeEventListener("loadedmetadata", this._onResize);
    globalThis.window?.removeEventListener?.("pagehide", this._onPageHide);
  }

  // ─── Position (reprise chez le destinataire, dans son navigateur) ───

  _seekOnReady(start) {
    if (!start || start <= 0) return;
    const el = this.video;
    const apply = () => {
      if (Math.abs(el.currentTime - start) > 2) {
        try {
          el.currentTime = start;
        } catch {
          /* pas encore seekable : _settleResume réessaiera */
        }
      }
    };
    if (el.readyState >= 1) apply();
    else el.addEventListener("loadedmetadata", apply, { once: true });
  }

  // dash.js remet parfois le temps à zéro entre le manifeste et le premier
  // segment : au premier `playing`, reposer la consigne si elle a été perdue.
  _settleResume() {
    const target = this.resumeTarget;
    this.resumeTarget = 0;
    if (target > 0 && Math.abs(this.video.currentTime - target) > 2) {
      try {
        this.video.currentTime = target;
      } catch {
        /* média non seekable */
      }
    }
  }

  savePosition() {
    const el = this.video;
    if (this.live || !el.duration || !Number.isFinite(el.duration)) return;
    const pos = el.currentTime || 0;
    // Début ou fin (≥ 95 %) : rien à reprendre (même règle que l'app).
    if (pos > 5 && pos / el.duration < 0.95) store(this.storage, this.positionKey, Math.floor(pos));
    else store(this.storage, this.positionKey, null);
  }

  // ─── Qualité ───

  _setupQuality() {
    const select = this.ui.quality;
    if (!select) return;
    for (const h of QUALITIES) {
      const opt = document.createElement("option");
      opt.value = String(h);
      opt.textContent = `${h}p`;
      select.append(opt);
    }
    select.value = String(this.maxHeight);
    select.addEventListener("change", () => this.setQuality(Number(select.value)));
  }

  /// Nouveau plafond : re-résolution à la position courante.
  setQuality(height) {
    if (!height || height === this.maxHeight) return;
    this.maxHeight = height;
    store(this.storage, QUALITY_KEY, height);
    const pos = this.live ? 0 : Math.floor(this.video.currentTime || 0);
    return this.attach(pos);
  }

  _showServed() {
    if (!this.ui.served) return;
    const h = this.video.videoHeight;
    this.ui.served.textContent = h ? `lu en ${h}p` : "";
  }

  // ─── Sous-titres ───

  _setupSubtitles() {
    const select = this.ui.subs;
    if (!select) return;
    select.addEventListener("change", () => this._applySubtitle(select.value));
  }

  _addSubtitleTracks() {
    const el = this.video;
    for (const s of this.subtitles) {
      const track = document.createElement("track");
      track.kind = "subtitles";
      track.srclang = s.lang;
      track.label = s.label || s.lang;
      track.src = s.url; // relais signé, même origine (pas de CORS)
      el.append(track);
    }
    const select = this.ui.subs;
    if (!select) return;
    const previous = select.value;
    const off = document.createElement("option");
    off.value = "";
    off.textContent = "Désactivés";
    const options = this.subtitles.map((s) => {
      const opt = document.createElement("option");
      opt.value = s.lang;
      opt.textContent = s.label || s.lang;
      return opt;
    });
    select.replaceChildren(off, ...options);
    const label = this.ui.subsLabel || select;
    label.hidden = this.subtitles.length === 0;
    // Le choix survit à un changement de qualité (pistes reconstruites).
    select.value = this.subtitles.some((s) => s.lang === previous) ? previous : "";
    this._applySubtitle(select.value);
  }

  _applySubtitle(lang) {
    for (const t of Array.from(this.video.textTracks || [])) {
      t.mode = lang && t.language === lang ? "showing" : "disabled";
    }
  }

  // ─── SponsorBlock (YouTube) ───

  async _loadSponsor() {
    if (this.platform !== "youtube") return;
    try {
      const resp = await fetch(`${this.base}/sponsor`);
      if (resp.ok) this.segments = (await resp.json()).segments || [];
    } catch {
      this.segments = [];
    }
  }

  _skipSponsor() {
    if (!this.segments.length || this.live) return;
    const now = Date.now();
    if (now - this.lastSkip < SKIP_THROTTLE_MS) return;
    const t = this.video.currentTime;
    for (const seg of this.segments) {
      if (t >= seg.start && t < seg.end - 0.5) {
        this.lastSkip = now;
        this.video.currentTime = seg.end;
        this._flashSkip();
        break;
      }
    }
  }

  _flashSkip() {
    const node = this.ui.skip;
    if (!node) return;
    node.textContent = "Segment sponsorisé sauté";
    clearTimeout(this.skipT);
    this.skipT = setTimeout(() => {
      node.textContent = "";
    }, 3000);
  }

  // ─── Message par-dessus le lecteur ───

  _message(text, { loading = false } = {}) {
    const node = this.ui.message;
    if (!node) return;
    node.hidden = !text;
    node.textContent = text || "";
    node.classList.toggle("loading", Boolean(text) && loading);
  }
}

/// Pied de page : date d'expiration du lien (epoch secondes, vide = permanent).
export function footerText(expires) {
  const base = "Lien privé partagé via ytui";
  const seconds = Number(expires);
  if (!expires || !seconds) return base;
  const date = new Date(seconds * 1000).toLocaleDateString("fr-FR", {
    day: "numeric",
    month: "long",
    year: "numeric",
  });
  return `${base} · valable jusqu'au ${date}`;
}

/// Démarrage dans la vraie page (rien à faire sous le harness de tests).
function boot() {
  const body = typeof document !== "undefined" ? document.body : null;
  const token = body && body.dataset && body.dataset.token;
  const video = token ? document.getElementById("video") : null;
  if (!video) return;
  const $ = (id) => document.getElementById(id);
  const footer = $("footer");
  if (footer) footer.textContent = footerText(body.dataset.expires);
  const player = new SharePlayer({
    token,
    platform: body.dataset.platform || "",
    ui: {
      video,
      message: $("message"),
      title: $("title"),
      quality: $("quality"),
      served: $("served"),
      subs: $("subs"),
      subsLabel: $("subs-label"),
      skip: $("skip"),
    },
  });
  player.start();
}

boot();
