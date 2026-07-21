// Lives en cours sur les chaînes suivies.

import { api } from "../js/api.js";
import { el, spinner, emptyState, errorToast, videoCard } from "../js/ui.js";

export async function render(view) {
  view.append(el("div", { class: "page-head" }, el("h1", { text: "Lives" })));
  const body = el("div");
  view.append(body);
  body.append(spinner());
  try {
    const lives = await api.lives();
    body.replaceChildren(
      lives.length
        ? el("div", { class: "grid" }, lives.map((l) => videoCard(l.video, { live: true })))
        : emptyState("Aucun live en cours"),
    );
  } catch (err) {
    errorToast(err);
    body.replaceChildren(emptyState("Impossible de charger les lives"));
  }
}
