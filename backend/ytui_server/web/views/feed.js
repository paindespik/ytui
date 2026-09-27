// Accueil : lives épinglés en tête + feed des chaînes suivies.

import { api } from "../js/api.js";
import {
  el,
  emptyState,
  errorToast,
  iconBtn,
  skeletonGrid,
  videoCard,
  videoGrid,
} from "../js/ui.js";

export async function render(view) {
  const body = el("div");
  const refreshBtn = iconBtn("refresh-cw", "Actualiser", {
    title: "Actualiser (r)",
    onclick: () => load(true),
  });
  view.append(
    el("div", { class: "page-head" }, el("h1", { text: "Accueil" }), refreshBtn),
    body,
  );

  // Même intervalle que la TUI et le mobile (5 min) : plus court que le TTL de
  // cache du backend (15 min), donc ces rafraîchissements sont servis depuis le
  // cache et la latence de découverte reste bornée à ~5 min.
  const REFRESH_MS = 5 * 60 * 1000;

  // Signature du dernier rendu : un rafraîchissement silencieux identique ne
  // re-rend rien (sinon focus clavier et position de lecture sautaient toutes
  // les 5 min pour rien).
  let lastSignature = "";
  // Numéro du chargement en cours : seule la réponse du plus récent s'affiche
  // (un « Actualiser » lent doublé par le tick périodique arrivait après lui
  // et réaffichait l'ancien flux).
  let seq = 0;
  let busy = false;

  async function load(refresh, silent = false) {
    if (silent && busy) return; // un chargement explicite est déjà en vol
    const mine = ++seq;
    busy = true;
    refreshBtn.disabled = true;
    if (!silent) body.replaceChildren(skeletonGrid(12));
    try {
      await fill(refresh, silent, mine);
    } finally {
      if (mine === seq) {
        busy = false;
        refreshBtn.disabled = false;
      }
    }
  }

  async function fill(refresh, silent, mine) {
    let lives = [];
    try {
      lives = await api.lives();
    } catch {
      /* non bloquant : le feed reste affichable sans les lives */
    }
    let out;
    try {
      out = await api.feed(refresh);
    } catch (err) {
      if (silent || mine !== seq) return; // tick raté : garder la page telle quelle
      errorToast(err);
      body.replaceChildren(emptyState("Impossible de charger le flux", "refresh-cw"));
      return;
    }
    if (mine !== seq) return; // un chargement plus récent a la main
    const signature = JSON.stringify([
      lives.map((l) => `${l.video.platform}:${l.video.video_id}`),
      (out.videos || []).map((v) => `${v.platform}:${v.video_id}`),
      out.warnings || [],
    ]);
    if (silent && signature === lastSignature) return;
    lastSignature = signature;
    body.replaceChildren();
    for (const w of out.warnings || []) {
      body.append(el("div", { class: "notice", text: w }));
    }
    if (lives.length) {
      body.append(
        el("div", { class: "section-title live-title", text: "En direct" }),
        el("div", { class: "grid" }, lives.map((l) => videoCard(l.video, { live: true }))),
      );
    }
    const videos = out.videos || [];
    if (!videos.length && !lives.length) {
      body.append(emptyState("Aucune vidéo — suivez des chaînes dans les Réglages.", "radio"));
      return;
    }
    if (lives.length) body.append(el("div", { class: "section-title", text: "Dernières vidéos" }));
    if (videos.length) body.append(videoGrid(videos));
  }

  await load(false);
  // Rafraîchissement périodique, nettoyé au démontage de la vue (sinon il
  // survit à la navigation et écrit dans un DOM détaché).
  const timer = setInterval(() => load(false, true), REFRESH_MS);
  return () => clearInterval(timer);
}
