// localStorage preferences + in-memory watched-ids cache.

import { api } from "./api.js";

const P = "ytui.";

function getBool(key, dflt) {
  const v = localStorage.getItem(P + key);
  return v === null ? dflt : v === "true";
}
function getNum(key, dflt) {
  const v = localStorage.getItem(P + key);
  const n = Number(v);
  return v === null || Number.isNaN(n) ? dflt : n;
}
function getStr(key, dflt) {
  const v = localStorage.getItem(P + key);
  return v === null ? dflt : v;
}
function set(key, value) {
  localStorage.setItem(P + key, String(value));
}

// Échelle de qualité partagée (Réglages + sélecteur du lecteur).
export const QUALITIES = [360, 480, 720, 1080, 1440, 2160];
// Amplification audio du lecteur : au-delà de ×1 il faut passer par Web Audio,
// l'élément <video> plafonnant à 100 % du niveau de la source.
export const GAINS = [1, 1.25, 1.5, 2, 2.5, 3];

export const prefs = {
  get sponsorblock() { return getBool("sponsorblock", true); },
  set sponsorblock(v) { set("sponsorblock", v); },
  get maxHeight() { return getNum("max_height", 1440); },
  set maxHeight(v) { set("max_height", v); },
  get subLangs() { return getStr("sub_langs", "fr,en"); },
  set subLangs(v) { set("sub_langs", v); },
  get volume() { return getNum("volume", 1); },
  set volume(v) { set("volume", v); },
  get muted() { return getBool("muted", false); },
  set muted(v) { set("muted", v); },
  get gain() { return getNum("gain", 1); },
  set gain(v) { set("gain", v); },
  // Autoplay: when off, the end of a video stops playback instead of pulling
  // the next queue item (or a suggestion). Same default as the mobile app.
  get autoplay() { return getBool("autoplay", true); },
  set autoplay(v) { set("autoplay", v); },
  // Mode cinéma : lecteur sur toute la largeur, panneau latéral dessous.
  get cinema() { return getBool("cinema", false); },
  set cinema(v) { set("cinema", v); },
  // Dernière source de recherche choisie (la barre du haut la réutilise).
  get searchSource() { return getStr("search_source", "youtube"); },
  set searchSource(v) { set("search_source", v); },
};

// « Nouveau » sur l'accueil : ce qui est sorti depuis la visite précédente.
// La référence est figée pour toute la session de l'onglet (sessionStorage) :
// revenir sur l'accueil après une vidéo ne fait pas disparaître les badges des
// autres. Première visite : aucune référence, donc aucun badge (sinon tout le
// flux serait « nouveau »).
const SEEN_KEY = P + "feed_seen_at";
export function feedNewSince(now = Date.now()) {
  let since = null;
  try {
    const session = sessionStorage.getItem(SEEN_KEY);
    if (session !== null) {
      since = Number(session) || null;
    } else {
      since = Number(localStorage.getItem(SEEN_KEY)) || null;
      sessionStorage.setItem(SEEN_KEY, String(since || 0));
    }
    localStorage.setItem(SEEN_KEY, String(now));
  } catch {
    /* stockage indisponible : pas de badge */
  }
  return since;
}

// Clé qualifiée '{platform}:{video_id}' : le même id nu peut exister sur
// deux plateformes (uid CrowdBunker au format id YouTube).
export function watchKey(video) {
  return `${video.platform || "youtube"}:${video.video_id}`;
}

export const watched = {
  ids: new Set(),
  // Fraction vue (0-1) par clé, pour la barre de progression des miniatures.
  progress: new Map(),
  async load() {
    try {
      const out = await api.watchedIds();
      this.ids = new Set(out.ids);
      this.progress = new Map(Object.entries(out.progress || {}));
    } catch {
      /* non-blocking: the ✓ badges simply stay hidden */
    }
  },
  has(video) { return this.ids.has(watchKey(video)); },
  add(video) { this.ids.add(watchKey(video)); },
  fraction(video) { return this.progress.get(watchKey(video)) || 0; },
  setProgress(video, fraction) {
    if (fraction > 0) this.progress.set(watchKey(video), Math.min(1, fraction));
  },
};
