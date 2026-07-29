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
};

export const watched = {
  ids: new Set(),
  async load() {
    try {
      const out = await api.watchedIds();
      this.ids = new Set(out.ids);
    } catch {
      /* non-blocking: the ✓ badges simply stay hidden */
    }
  },
  has(videoId) { return this.ids.has(videoId); },
  add(videoId) { this.ids.add(videoId); },
};
