// Playlist distante (YouTube/Odysee) : titre + Tout lire + grille.

import { api } from "../js/api.js";
import { el, spinner, emptyState, errorToast, videoCard, playVideos } from "../js/ui.js";

export async function render(view, { params, query }) {
  const { platform, id } = params;
  const title = el("h1", { text: query.get("title") || "Playlist" });
  const playAllBtn = el("button", { class: "btn primary", text: "▶ Tout lire", disabled: true });
  const body = el("div");
  view.append(el("div", { class: "page-head" }, title, playAllBtn), body);
  body.append(spinner());

  try {
    const out = await api.playlistVideos(id, platform, 200);
    if (out.title) title.textContent = out.title;
    const videos = out.items || [];
    if (!videos.length) {
      body.replaceChildren(emptyState("Cette playlist est vide"));
      return;
    }
    playAllBtn.disabled = false;
    playAllBtn.addEventListener("click", () => playVideos(videos));
    body.replaceChildren(
      el(
        "div",
        { class: "grid" },
        videos.map((v, i) => videoCard(v, { onOpen: () => playVideos(videos, i) })),
      ),
    );
  } catch (err) {
    errorToast(err);
    body.replaceChildren(emptyState("Impossible de charger la playlist"));
  }
}
