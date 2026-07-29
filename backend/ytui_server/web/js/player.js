// Orchestration de la lecture (port des sémantiques de mobile/lib/screens/player.dart).
//
// Choix du moteur :
//   kind=hls               → URL .flv → mpegts.js ; sinon hls.js (Safari natif en
//                            secours) — toujours via le proxy same-origin (CORS).
//   YouTube VOD / split    → manifest DASH serveur (/mpd) joué par dash.js (>360p).
//   kind=progressive       → URL directe ; 1er échec → même URL via /api/proxy.
//   split sans MPD         → re-résolution en 360p progressive.
// Erreur fatale → une seule re-résolution complète (URL expirée), puis message.

import { api } from "./api.js";
import { prefs, watched } from "./state.js";
import { playerActions } from "./shortcuts.js";
import { toast } from "./ui.js";

export function isLiveId(platform, videoId) {
  return (platform === "twitch" || platform === "tiktok") && String(videoId).includes(":");
}

// Règle mobile (models.dart resumeStart) : 0 si position <= 0 ou >= 95 % de la durée.
export function resumeStart(position, duration) {
  if (!position || position <= 0) return 0;
  if (duration && duration > 0 && position / duration >= 0.95) return 0;
  return Math.floor(position);
}

const SKIP_THROTTLE_MS = 1000; // même throttle que le mobile
const HEARTBEAT_MS = 10_000;

export class Player {
  constructor(videoEl, callbacks = {}) {
    this.el = videoEl;
    this.cb = callbacks; // onMeta, onFatal, onAutoplayBlocked, onSubtitleChange, onRateChange
    this.video = null;
    this.streams = null;
    this.live = false;
    this.rate = 1;
    this._engine = null;
    this._hls = null;
    this._dash = null;
    this._mpegts = null;
    this._directUrl = null;
    this._heartbeat = null;
    this._deferT = null;
    this._retried = false;
    this._proxied = false;
    this._segments = [];
    this._lastSkip = 0;
    this._destroyed = false;

    this._onTimeUpdate = this._onTimeUpdate.bind(this);
    this._onElementError = this._onElementError.bind(this);
    this._onVolume = this._onVolume.bind(this);
    videoEl.addEventListener("timeupdate", this._onTimeUpdate);
    videoEl.addEventListener("error", this._onElementError);
    videoEl.addEventListener("volumechange", this._onVolume);
    videoEl.volume = prefs.volume;
    videoEl.muted = prefs.muted;

    this._registerShortcuts();
  }

  // ─── Cycle de vie ───

  async load(video) {
    this._teardownEngines();
    this.video = video;
    this.streams = null;
    this.live = isLiveId(video.platform, video.video_id);
    this._retried = false;
    this._proxied = false;
    this._segments = [];

    // Historique immédiat + ✓ optimiste (fire and forget, parité mobile).
    api.recordWatch(video).catch(() => {});
    watched.add(video.video_id);

    let start = 0;
    if (!this.live) {
      const r = await api.resume(video.video_id).catch(() => null);
      if (r) start = resumeStart(r.position, r.duration);
    }
    await this._attach(start);
    if (!this.live && video.platform === "youtube") this._loadSponsor();
    this._startHeartbeat();
    this._mediaSession();
  }

  destroy() {
    this._destroyed = true;
    this._savePosition(); // flush final avant démontage
    clearInterval(this._heartbeat);
    this._teardownEngines();
    this.el.removeEventListener("timeupdate", this._onTimeUpdate);
    this.el.removeEventListener("error", this._onElementError);
    this.el.removeEventListener("volumechange", this._onVolume);
    for (const k of Object.keys(playerActions)) delete playerActions[k];
    if ("mediaSession" in navigator) {
      navigator.mediaSession.metadata = null;
      for (const a of ["play", "pause", "nexttrack", "previoustrack"]) {
        try {
          navigator.mediaSession.setActionHandler(a, null);
        } catch {
          /* action non supportée */
        }
      }
    }
  }

  // ─── Résolution + choix du moteur ───

  async _attach(start) {
    const v = this.video;
    const streamsP = api.videoStreams(v.video_id, {
      platform: v.platform,
      maxHeight: prefs.maxHeight,
      subLangs: prefs.subLangs,
    });
    const mpdUrl = api.mpdUrl(v.video_id, v.platform, prefs.maxHeight);
    // YouTube VOD : /streams ne renvoie que du 360p progressive — le MPD est
    // tenté systématiquement, en parallèle (le cache serveur évite la double
    // extraction yt-dlp).
    const mpdP =
      v.platform === "youtube" && !this.live
        ? fetch(mpdUrl).then((r) => r.ok).catch(() => false)
        : Promise.resolve(false);

    let streams;
    try {
      streams = await streamsP;
    } catch (err) {
      if (await mpdP) {
        this._useDash(mpdUrl, start);
        return;
      }
      throw err;
    }
    this.streams = streams;
    this.cb.onMeta?.(streams);

    if (streams.kind === "hls") {
      // Lives (YouTube/Twitch/TikTok). Un room TikTok FLV-only arrive ici
      // avec une URL .flv (chemin chaud, cf. backend).
      let isFlv = false;
      try {
        isFlv = new URL(streams.url).pathname.includes(".flv");
      } catch {
        isFlv = streams.url.split("?")[0].includes(".flv");
      }
      if (isFlv) this._useMpegts(streams.url);
      else this._useHls(streams.url, start); // start=0 pour un vrai live, VOD Twitch sinon
    } else {
      let mpdOk = await mpdP;
      if (!mpdOk && streams.kind === "split") {
        mpdOk = await fetch(mpdUrl).then((r) => r.ok).catch(() => false);
      }
      if (mpdOk) {
        this._useDash(mpdUrl, start);
      } else if (streams.kind === "progressive") {
        this._useProgressive(streams.url, start);
      } else {
        // split injouable sans MPD : retomber en 360p progressive.
        const low = await api.videoStreams(v.video_id, {
          platform: v.platform,
          maxHeight: 360,
          subLangs: prefs.subLangs,
        });
        if (low.kind === "progressive") {
          this._useProgressive(low.url, start);
        } else {
          this.cb.onFatal?.("Format non lisible dans le navigateur");
          return;
        }
      }
    }
    this._addSubtitleTracks(streams.subtitles || []);
    this._applyRate();
  }

  _useProgressive(url, start) {
    this._engine = "progressive";
    this._directUrl = url;
    this.el.src = url; // jouable inter-IP sans CORS (élément natif)
    this._seekOnReady(start);
    this._play();
  }

  _useHls(url, start = 0) {
    const src = api.proxyHlsUrl(url);
    if (window.Hls && window.Hls.isSupported()) {
      this._engine = "hls";
      const h = new window.Hls();
      this._hls = h;
      h.on(window.Hls.Events.ERROR, (_evt, data) => {
        if (data && data.fatal) this._fatalOrRetry("Erreur du flux HLS");
      });
      h.loadSource(src);
      h.attachMedia(this.el);
      this._seekOnReady(start);
      this._play();
    } else if (this.el.canPlayType("application/vnd.apple.mpegurl")) {
      this._engine = "native-hls"; // Safari
      this.el.src = src;
      this._seekOnReady(start);
      this._play();
    } else {
      this.cb.onFatal?.("HLS non pris en charge par ce navigateur");
    }
  }

  _useDash(mpdUrl, start) {
    this._engine = "dash";
    const dashjs = window.dashjs;
    const p = dashjs.MediaPlayer().create();
    this._dash = p;
    p.on(dashjs.MediaPlayer.events.ERROR, () => this._deferFatal("Erreur du flux DASH"));
    p.initialize(this.el, mpdUrl, true);
    this._seekOnReady(start);
    this._play();
  }

  _useMpegts(url) {
    const mpegts = window.mpegts;
    if (!(mpegts && mpegts.isSupported())) {
      this.cb.onFatal?.("FLV non pris en charge par ce navigateur (MSE requis)");
      return;
    }
    this._engine = "mpegts";
    const m = mpegts.createPlayer({ type: "flv", isLive: true, url: api.proxyUrl(url) });
    this._mpegts = m;
    m.on(mpegts.Events.ERROR, () => this._deferFatal("Erreur du flux live"));
    m.attachMediaElement(this.el);
    m.load();
    this._play();
  }

  _teardownEngines() {
    clearTimeout(this._deferT);
    this._engine = null;
    if (this._hls) {
      try {
        this._hls.destroy();
      } catch {
        /* déjà détruit */
      }
      this._hls = null;
    }
    if (this._dash) {
      try {
        (this._dash.destroy || this._dash.reset).call(this._dash);
      } catch {
        /* déjà détruit */
      }
      this._dash = null;
    }
    if (this._mpegts) {
      try {
        this._mpegts.destroy();
      } catch {
        /* déjà détruit */
      }
      this._mpegts = null;
    }
    this.el.removeAttribute("src");
    [...this.el.querySelectorAll("track")].forEach((t) => t.remove());
    try {
      this.el.load();
    } catch {
      /* reset best-effort */
    }
  }

  // ─── Erreurs ───

  _onElementError() {
    if (this._destroyed || !this._engine) return;
    if (this._engine === "progressive" && !this._proxied && this._directUrl) {
      // 1er échec : même URL mais via le proxy serveur (IP/CORS).
      this._proxied = true;
      const pos = this.el.currentTime;
      this.el.src = api.proxyUrl(this._directUrl);
      this._seekOnReady(pos);
      this._play();
      return;
    }
    if (this._engine === "progressive" || this._engine === "native-hls") {
      this._fatalOrRetry("Flux interrompu");
    }
  }

  // dash.js / mpegts émettent aussi du bruit non fatal pendant que la lecture
  // continue (leçon mobile) : différer 1 s et revérifier avant d'agir.
  _deferFatal(msg) {
    if (this._destroyed) return;
    clearTimeout(this._deferT);
    this._deferT = setTimeout(() => {
      if (this._destroyed || !this._engine) return;
      const playing = !this.el.paused && !this.el.ended && this.el.readyState >= 3;
      if (!playing) this._fatalOrRetry(msg);
    }, 1000);
  }

  async _fatalOrRetry(msg) {
    if (this._destroyed) return;
    if (this._retried) {
      this.cb.onFatal?.(`${msg} — flux expiré ou indisponible`);
      return;
    }
    this._retried = true;
    const pos = this.live ? 0 : Math.floor(this.el.currentTime || 0);
    this._teardownEngines();
    try {
      await this._attach(pos); // URLs re-résolues (expiration)
    } catch {
      this.cb.onFatal?.("Flux expiré ou indisponible — réessayer");
    }
  }

  // ─── Position / reprise ───

  _seekOnReady(start) {
    if (!start || start <= 0) return;
    const apply = () => {
      if (Math.abs(this.el.currentTime - start) > 2) this.el.currentTime = start;
    };
    if (this.el.readyState >= 1) apply();
    else this.el.addEventListener("loadedmetadata", apply, { once: true });
  }

  _play() {
    const p = this.el.play();
    if (p && p.catch) {
      p.catch(() => {
        if (!this._destroyed) this.cb.onAutoplayBlocked?.();
      });
    }
  }

  _startHeartbeat() {
    clearInterval(this._heartbeat);
    this._heartbeat = setInterval(() => this._savePosition(), HEARTBEAT_MS);
  }

  _savePosition() {
    if (!this.video || this.live) return;
    const duration = this.el.duration;
    if (!duration || !Number.isFinite(duration)) return;
    const position = this.el.currentTime;
    if (position > 0) {
      api.savePosition(this.video.video_id, position, duration).catch(() => {});
    }
  }

  // ─── SponsorBlock ───

  async _loadSponsor() {
    try {
      const out = await api.sponsorSegments(this.video.video_id);
      this._segments = out.segments || [];
    } catch {
      this._segments = [];
    }
  }

  _onTimeUpdate() {
    if (!this._segments.length || !prefs.sponsorblock) return;
    const now = Date.now();
    if (now - this._lastSkip < SKIP_THROTTLE_MS) return;
    const t = this.el.currentTime;
    for (const seg of this._segments) {
      if (t >= seg.start && t < seg.end - 0.5) {
        this._lastSkip = now;
        this.el.currentTime = seg.end;
        toast("Segment sponsorisé sauté");
        break;
      }
    }
  }

  // ─── Sous-titres ───

  _addSubtitleTracks(subs) {
    [...this.el.querySelectorAll("track")].forEach((t) => t.remove());
    for (const s of subs) {
      const track = document.createElement("track");
      track.kind = "subtitles";
      track.srclang = s.lang;
      track.label = s.label || s.lang;
      // timedtext sans CORS → passage obligatoire par le proxy same-origin.
      track.src = api.proxyUrl(s.url);
      this.el.append(track);
    }
    for (const t of this.el.textTracks) t.mode = "disabled";
    this.cb.onSubtitleChange?.(-1);
  }

  _showingIndex() {
    return [...this.el.textTracks].findIndex((t) => t.mode === "showing");
  }

  setSubtitle(index) {
    const tracks = [...this.el.textTracks];
    tracks.forEach((t, i) => {
      t.mode = i === index ? "showing" : "disabled";
    });
    this.cb.onSubtitleChange?.(this._showingIndex());
  }

  cycleSubtitles() {
    const tracks = [...this.el.textTracks];
    if (!tracks.length) {
      toast("Aucun sous-titre disponible");
      return;
    }
    const next = this._showingIndex() + 1;
    if (next < tracks.length) {
      this.setSubtitle(next);
      toast(`Sous-titres : ${tracks[next].label}`);
    } else {
      this.setSubtitle(-1);
      toast("Sous-titres désactivés");
    }
  }

  // ─── Vitesse / volume / raccourcis ───

  setRate(r) {
    this.rate = Math.min(3, Math.max(0.5, Math.round(r * 4) / 4));
    this.el.playbackRate = this.rate;
    this.cb.onRateChange?.(this.rate);
    toast(`Vitesse ×${this.rate.toLocaleString("fr-FR")}`);
  }

  // Le plafond est lu par _attach (prefs.maxHeight) : l'écrire puis réattacher
  // suffit, et la préférence persiste pour les lectures suivantes.
  async setMaxHeight(height) {
    prefs.maxHeight = height;
    const pos = this.live ? 0 : Math.floor(this.el.currentTime || 0);
    this._teardownEngines();
    this._retried = false;
    this._proxied = false;
    try {
      await this._attach(pos);
    } catch (err) {
      this.cb.onFatal?.(err.detail || "Lecture impossible");
      return;
    }
    toast(`Qualité : ≤${height}p`);
  }

  _applyRate() {
    this.el.playbackRate = this.rate;
  }

  _onVolume() {
    prefs.volume = this.el.volume;
    prefs.muted = this.el.muted;
  }

  fullscreen() {
    if (document.fullscreenElement) {
      document.exitFullscreen();
    } else {
      const host = this.el.closest(".player-box") || this.el;
      host.requestFullscreen?.();
    }
  }

  _registerShortcuts() {
    playerActions.toggle = () => (this.el.paused ? this._play() : this.el.pause());
    playerActions.seekBy = (d) => {
      if (!this.live) this.el.currentTime = Math.max(0, this.el.currentTime + d);
    };
    playerActions.speedBy = (d) => this.setRate(this.rate + d);
    playerActions.cycleSubtitles = () => this.cycleSubtitles();
    playerActions.mute = () => {
      this.el.muted = !this.el.muted;
    };
    playerActions.fullscreen = () => this.fullscreen();
    // next/previous : posés par la vue watch (dépendent de la file d'attente).
  }

  // ─── Media Session (touches média OS) ───

  _mediaSession() {
    if (!("mediaSession" in navigator)) return;
    const v = this.video;
    navigator.mediaSession.metadata = new MediaMetadata({
      title: this.streams?.title || v.title || "ytui",
      artist: v.channel_title || "",
      artwork: v.thumbnail_url ? [{ src: v.thumbnail_url }] : [],
    });
    navigator.mediaSession.setActionHandler("play", () => this._play());
    navigator.mediaSession.setActionHandler("pause", () => this.el.pause());
    navigator.mediaSession.setActionHandler("nexttrack", () => playerActions.next?.());
    navigator.mediaSession.setActionHandler("previoustrack", () => playerActions.previous?.());
  }
}
