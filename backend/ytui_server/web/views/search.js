// Recherche YouTube / Odysee. L'état (q, source) vit dans le hash — partageable.

import { api } from "../js/api.js";
import { navigate } from "../js/router.js";
import { el, spinner, emptyState, errorToast, videoGrid } from "../js/ui.js";

const SOURCES = [
  ["youtube", "YouTube"],
  ["odysee", "Odysee"],
  ["crowdbunker", "CrowdBunker"],
];

export async function render(view, { query }) {
  const q = (query.get("q") || "").trim();
  let source = query.get("source") || "youtube";
  if (!SOURCES.some(([s]) => s === source)) source = "youtube";

  const input = el("input", {
    class: "input",
    id: "search-input",
    type: "search",
    placeholder: "Rechercher…",
    "aria-label": "Rechercher",
    value: q,
  });
  const segButtons = SOURCES.map(([value, label]) =>
    el("button", {
      type: "button",
      class: value === source ? "active" : "",
      dataset: { source: value },
      text: label,
      onclick: () => {
        if (value === source) return;
        source = value;
        // Comparaison sur la valeur (le libellé n'est pas une clé), et hash mis
        // à jour même sans terme : la source choisie reste partageable et
        // survit à un rechargement.
        segButtons.forEach((b) => b.classList.toggle("active", b.dataset.source === source));
        const term = input.value.trim();
        navigate(`/search?source=${source}` + (term ? `&q=${encodeURIComponent(term)}` : ""));
      },
    }),
  );
  const results = el("div");

  function submit() {
    const term = input.value.trim();
    if (!term) return;
    const target = `/search?q=${encodeURIComponent(term)}&source=${source}`;
    if (location.hash === "#" + target) {
      runSearch(results, term, source); // même requête : relancer sans navigation
    } else {
      navigate(target);
    }
  }

  const form = el(
    "form",
    {
      class: "search-form",
      onsubmit: (e) => {
        e.preventDefault();
        submit();
      },
    },
    input,
    el("div", { class: "seg" }, segButtons),
    el("button", { class: "btn primary", type: "submit", text: "Rechercher" }),
  );

  view.append(el("div", { class: "page-head" }, el("h1", { text: "Recherche" })), form, results);

  if (q) {
    await runSearch(results, q, source);
  } else {
    results.append(emptyState("Recherchez des vidéos, chaînes ou playlists."));
    input.focus();
  }
}

async function runSearch(results, q, source) {
  results.replaceChildren(spinner());
  try {
    const out = await api.search(q, source, 20);
    const items = out.items || [];
    results.replaceChildren(
      items.length ? videoGrid(items) : emptyState(`Aucun résultat pour « ${q} »`),
    );
  } catch (err) {
    errorToast(err);
    results.replaceChildren(emptyState("Recherche impossible"));
  }
}
