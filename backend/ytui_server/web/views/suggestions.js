// Suggestions basées sur l'historique.

import { api } from "../js/api.js";
import { el, emptyState, errorToast, iconBtn, skeletonGrid, videoGrid } from "../js/ui.js";

export async function render(view) {
  const body = el("div");
  const refreshBtn = iconBtn("refresh-cw", "Actualiser", {
    title: "Recalculer les suggestions",
    onclick: () => load(true),
  });
  view.append(
    el("div", { class: "page-head" }, el("h1", { text: "Suggestions" }), refreshBtn),
    body,
  );

  async function load(refresh) {
    refreshBtn.disabled = true;
    body.replaceChildren(skeletonGrid(12));
    let out;
    try {
      out = await api.suggestions(refresh);
    } catch (err) {
      errorToast(err);
      body.replaceChildren(emptyState("Impossible de charger les suggestions", "refresh-cw"));
      refreshBtn.disabled = false;
      return;
    }
    refreshBtn.disabled = false;
    body.replaceChildren();
    for (const w of out.warnings || []) {
      body.append(el("div", { class: "notice", text: w }));
    }
    const videos = out.videos || [];
    body.append(
      videos.length
        ? videoGrid(videos)
        : emptyState("Aucune suggestion — regardez quelques vidéos d'abord.", "sparkles"),
    );
  }

  await load(false);
}
