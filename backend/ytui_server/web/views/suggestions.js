// Suggestions basées sur l'historique.

import { api } from "../js/api.js";
import { el, spinner, emptyState, errorToast, videoGrid } from "../js/ui.js";

export async function render(view) {
  const body = el("div");
  const refreshBtn = el("button", {
    class: "btn",
    text: "Actualiser",
    onclick: () => load(true),
  });
  view.append(
    el("div", { class: "page-head" }, el("h1", { text: "Suggestions" }), refreshBtn),
    body,
  );

  async function load(refresh) {
    refreshBtn.disabled = true;
    body.replaceChildren(spinner());
    let out;
    try {
      out = await api.suggestions(refresh);
    } catch (err) {
      errorToast(err);
      body.replaceChildren(emptyState("Impossible de charger les suggestions"));
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
        : emptyState("Aucune suggestion — regardez quelques vidéos d'abord."),
    );
  }

  await load(false);
}
