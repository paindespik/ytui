// Contenu d'une playlist locale : lecture depuis un index + retrait d'items.

import { api } from "../js/api.js";
import {
  el,
  iconBtn,
  skeletonRows,
  emptyState,
  errorToast,
  toast,
  fmtDuration,
  playVideos,
} from "../js/ui.js";

export async function render(view, { params, query }) {
  const playlistId = params.id;
  const title = el("h1", { text: query.get("name") || "Playlist" });
  const playAllBtn = iconBtn("play", "Tout lire", { class: "primary", disabled: true });
  const body = el("div");
  view.append(el("div", { class: "page-head" }, title, playAllBtn), body);

  async function load() {
    body.replaceChildren(skeletonRows(6));
    let items;
    try {
      items = await api.playlistItems(playlistId);
    } catch (err) {
      errorToast(err);
      body.replaceChildren(emptyState("Impossible de charger la playlist"));
      return;
    }
    if (!items.length) {
      playAllBtn.disabled = true;
      body.replaceChildren(emptyState("Cette playlist est vide", "list-video"));
      return;
    }
    const videos = items.map((it) => it.video);
    playAllBtn.disabled = false;
    playAllBtn.onclick = () => playVideos(videos);
    body.replaceChildren(
      el(
        "div",
        { class: "rows" },
        items.map((it, index) => {
          const v = it.video;
          const sub = [v.channel_title, v.duration ? fmtDuration(v.duration) : ""]
            .filter(Boolean)
            .join(" · ");
          const row = el(
            "div",
            {
              class: "row",
              tabindex: "0",
              role: "button",
              // Comme le mobile : lit TOUTE la playlist à partir de cet index.
              onclick: () => playVideos(videos, index),
              onkeydown: (e) => {
                if (e.key === "Enter") playVideos(videos, index);
              },
            },
            el(
              "div",
              { class: "row-thumb" },
              v.thumbnail_url ? el("img", { src: v.thumbnail_url, loading: "lazy", alt: "" }) : null,
            ),
            el(
              "div",
              { class: "txt" },
              el("div", { class: "title", title: v.title, text: v.title }),
              sub ? el("div", { class: "sub", text: sub }) : null,
            ),
            el(
              "div",
              { class: "actions" },
              iconBtn("x", null, {
                class: "danger",
                title: "Retirer de la playlist",
                onclick: async (e) => {
                  e.stopPropagation();
                  try {
                    await api.removePlaylistItem(playlistId, it.position);
                    toast("Vidéo retirée");
                    await load();
                  } catch (err) {
                    errorToast(err);
                  }
                },
              }),
            ),
          );
          row._video = v; // raccourci q (file d'attente)
          return row;
        }),
      ),
    );
  }

  await load();
}
