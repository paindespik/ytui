// Recherche YouTube / Odysee / CrowdBunker. Le terme se saisit dans la barre
// du haut (toujours visible) ; cette page choisit la source et affiche les
// résultats. L'état (q, source) vit dans le hash — partageable.

import { api } from "../js/api.js";
import { navigate } from "../js/router.js";
import { el, emptyState, errorToast, skeletonGrid, videoGrid } from "../js/ui.js";
import { prefs } from "../js/state.js";

const SOURCES = [
  ["youtube", "YouTube"],
  ["odysee", "Odysee"],
  ["crowdbunker", "CrowdBunker"],
];

export async function render(view, { query }) {
  const q = (query.get("q") || "").trim();
  let source = query.get("source") || prefs.searchSource;
  if (!SOURCES.some(([s]) => s === source)) source = "youtube";
  prefs.searchSource = source; // la barre du haut reprend cette source

  const segButtons = SOURCES.map(([value, label]) =>
    el("button", {
      type: "button",
      class: value === source ? "active" : "",
      "aria-pressed": value === source ? "true" : "false",
      dataset: { source: value },
      text: label,
      onclick: () => {
        if (value === source) return;
        // Hash mis à jour même sans terme : la source choisie reste
        // partageable et survit à un rechargement.
        navigate(`/search?source=${value}` + (q ? `&q=${encodeURIComponent(q)}` : ""));
      },
    }),
  );
  const results = el("div");

  view.append(
    el(
      "div",
      { class: "page-head" },
      el("h1", { text: q ? `Résultats pour « ${q} »` : "Recherche" }),
      el("div", { class: "seg", role: "group", "aria-label": "Source" }, segButtons),
    ),
    results,
  );

  if (q) {
    await runSearch(results, q, source);
  } else {
    results.append(
      emptyState("Recherchez des vidéos, chaînes ou playlists dans la barre du haut.", "search"),
    );
    document.getElementById("search-input")?.focus();
  }
}

async function runSearch(results, q, source) {
  results.replaceChildren(skeletonGrid(8));
  try {
    const out = await api.search(q, source, 20);
    const items = out.items || [];
    results.replaceChildren(
      items.length ? videoGrid(items) : emptyState(`Aucun résultat pour « ${q} »`, "search"),
    );
  } catch (err) {
    errorToast(err);
    results.replaceChildren(emptyState("Recherche impossible"));
  }
}
