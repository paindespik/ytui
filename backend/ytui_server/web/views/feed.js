// Accueil : lives épinglés en tête + feed des chaînes suivies.

import { api } from "../js/api.js";
import { el, spinner, emptyState, errorToast, videoCard, videoGrid } from "../js/ui.js";

export async function render(view) {
  const body = el("div");
  const refreshBtn = el("button", {
    class: "btn",
    text: "Actualiser",
    onclick: () => load(true),
  });
  view.append(
    el("div", { class: "page-head" }, el("h1", { text: "Accueil" }), refreshBtn),
    body,
  );

  async function load(refresh) {
    refreshBtn.disabled = true;
    body.replaceChildren(spinner());
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
      errorToast(err);
      body.replaceChildren(emptyState("Impossible de charger le flux"));
      refreshBtn.disabled = false;
      return;
    }
    refreshBtn.disabled = false;
    body.replaceChildren();
    for (const w of out.warnings || []) {
      body.append(el("div", { class: "notice", text: w }));
    }
    if (lives.length) {
      body.append(
        el("div", { class: "section-title", text: "En direct" }),
        el("div", { class: "grid" }, lives.map((l) => videoCard(l.video, { live: true }))),
      );
    }
    const videos = out.videos || [];
    if (!videos.length && !lives.length) {
      body.append(emptyState("Aucune vidéo — suivez des chaînes dans les Réglages."));
      return;
    }
    if (lives.length) body.append(el("div", { class: "section-title", text: "Dernières vidéos" }));
    if (videos.length) body.append(videoGrid(videos));
  }

  await load(false);
}
