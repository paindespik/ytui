// Lives en cours sur les chaînes suivies.

import { api } from "../js/api.js";
import { el, spinner, emptyState, errorToast, videoCard } from "../js/ui.js";

// Même cadence que l'accueil, la TUI et le mobile : 5 min.
const REFRESH_MS = 5 * 60 * 1000;

export async function render(view) {
  const body = el("div");
  const refreshBtn = el("button", {
    class: "btn",
    text: "Actualiser",
    onclick: () => load(),
  });
  view.append(
    el("div", { class: "page-head" }, el("h1", { text: "Lives" }), refreshBtn),
    body,
  );

  // `silent` : tick périodique — ni spinner ni page vidée en cas d'échec.
  async function load(silent = false) {
    refreshBtn.disabled = true;
    if (!silent) body.replaceChildren(spinner());
    try {
      const lives = await api.lives();
      body.replaceChildren(
        lives.length
          ? el("div", { class: "grid" }, lives.map((l) => videoCard(l.video, { live: true })))
          : emptyState("Aucun live en cours"),
      );
    } catch (err) {
      if (!silent) {
        errorToast(err);
        body.replaceChildren(emptyState("Impossible de charger les lives"));
      }
    }
    refreshBtn.disabled = false;
  }

  await load();
  // Nettoyé au démontage : un intervalle orphelin continuerait d'interroger le
  // serveur et d'écrire dans un DOM détaché.
  const timer = setInterval(() => load(true), REFRESH_MS);
  return () => clearInterval(timer);
}
