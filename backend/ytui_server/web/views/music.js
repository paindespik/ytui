// Accueil YouTube Music du compte : les étagères de music.youtube.com
// (« Sélection rapide », « Mixés pour vous », « Albums pour vous »…), dans
// l'ordre du site. Albums et mixes s'ouvrent comme des playlists, les
// artistes comme des chaînes.

import { api } from "../js/api.js";
import {
  el,
  emptyState,
  errorToast,
  iconBtn,
  openVideo,
  playVideos,
  skeletonGrid,
  videoCard,
} from "../js/ui.js";

// Pochette carrée (titre, album, mix) plutôt qu'image de clip 16:9 : seules
// les vignettes de vidéos viennent de i.ytimg.com.
export const isCoverArt = (v) =>
  v.kind !== "channel" && !!v.thumbnail_url && !/(^|\.)ytimg\.com\//.test(v.thumbnail_url);

export function shelfSection(shelf) {
  const tracks = shelf.items.filter((v) => v.kind === "video");
  const cards = shelf.items.map((item) => {
    // Un artiste n'a pas de « chaîne » à afficher sous son propre nom.
    const shown = item.kind === "channel" ? { ...item, channel_title: "Artiste" } : item;
    const card = videoCard(shown, {
      onOpen: (video) => {
        if (video.kind !== "video") return openVideo(video);
        // Comme sur le site, la lecture enchaîne la suite de l'étagère.
        playVideos(tracks, tracks.indexOf(video));
      },
    });
    if (item.kind === "channel") card.classList.add("artist");
    else if (isCoverArt(item)) card.classList.add("square");
    return card;
  });
  return el(
    "section",
    { class: "shelf" },
    el(
      "div",
      { class: "shelf-head" },
      shelf.strapline ? el("div", { class: "strapline", text: shelf.strapline }) : null,
      el("h2", { text: shelf.title }),
    ),
    el("div", { class: "shelf-row" }, cards),
  );
}

export async function render(view) {
  const body = el("div");
  const refreshBtn = iconBtn("refresh-cw", "Actualiser", {
    title: "Recharger l'accueil YouTube Music",
    onclick: () => load(true),
  });
  view.append(
    el("div", { class: "page-head" }, el("h1", { text: "Musique" }), refreshBtn),
    body,
  );

  async function load(refresh) {
    refreshBtn.disabled = true;
    body.replaceChildren(skeletonGrid(8));
    let out;
    try {
      out = await api.music(refresh);
    } catch (err) {
      errorToast(err);
      body.replaceChildren(emptyState("Impossible de charger YouTube Music", "refresh-cw"));
      refreshBtn.disabled = false;
      return;
    }
    refreshBtn.disabled = false;
    body.replaceChildren();
    for (const w of out.warnings || []) {
      body.append(el("div", { class: "notice", text: w }));
    }
    const shelves = (out.shelves || []).filter((s) => s.items && s.items.length);
    if (shelves.length) {
      body.append(...shelves.map(shelfSection));
    } else if (!(out.warnings || []).length) {
      body.append(emptyState("YouTube Music n'a rien renvoyé.", "music"));
    }
  }

  await load(false);
}
