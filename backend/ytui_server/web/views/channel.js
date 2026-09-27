// Page chaîne : dernières vidéos, recherche dans la chaîne, Suivre et Tout lire.

import { api } from "../js/api.js";
import { navigate } from "../js/router.js";
import {
  el,
  emptyState,
  errorToast,
  videoCard,
  playVideos,
  channelPath,
  iconBtn,
  skeletonGrid,
  followButton,
  PLATFORM_LABELS,
} from "../js/ui.js";
import { icon } from "../js/icons.js";

export async function render(view, { params, query }) {
  const { platform, id } = params;
  const channelName = query.get("title") || "";
  // La recherche vit dans le hash : l'URL reste partageable et un nouveau terme
  // re-rend la vue depuis zéro, donc la pagination repart proprement.
  const q = (query.get("q") || "").trim();
  const title = el("h1", { text: channelName || "Chaîne" });
  // Badge « en direct » : la chaîne apparaît dans /api/lives.
  const liveBadge = el("span", { class: `chip live-chip ${platform}`, text: "EN DIRECT" });
  liveBadge.hidden = true;
  const platformChip = PLATFORM_LABELS[platform]
    ? el("span", { class: `chip platform ${platform}`, text: PLATFORM_LABELS[platform] })
    : null;
  // Bascule : une chaîne suivie doit pouvoir être quittée d'ici, sans passer
  // par les Réglages.
  const followBtn = followButton(platform, id);
  const playAllBtn = iconBtn("play", "Tout lire", { class: "primary", disabled: true });
  const body = el("div");

  const searchInput = el("input", {
    class: "input page-search",
    type: "search",
    placeholder: "Rechercher dans la chaîne…",
    "aria-label": "Rechercher dans la chaîne",
    value: q,
  });
  const searchForm = el(
    "form",
    {
      class: "search-form",
      onsubmit: (e) => {
        e.preventDefault();
        const term = searchInput.value.trim();
        if (term === q) return;
        let target = channelPath(id, platform, channelName);
        if (term) target += (target.includes("?") ? "&" : "?") + `q=${encodeURIComponent(term)}`;
        navigate(target);
      },
    },
    searchInput,
    el("button", { class: "btn", type: "submit" }, icon("search"), el("span", { text: "Rechercher" })),
    q ? el("button", { class: "btn", type: "button", text: "Effacer", onclick: () => {
      searchInput.value = "";
      navigate(channelPath(id, platform, channelName));
    } }) : null,
  );

  view.append(
    el(
      "div",
      { class: "page-head" },
      el("div", { class: "head-title" }, title, platformChip, liveBadge),
      playAllBtn,
      followBtn,
    ),
  );
  if (platform === "tiktok") {
    // Les pages de vidéos d'une chaîne TikTok ne sont pas listables sans
    // auth : la chaîne ne se voit que via l'onglet Lives.
    view.append(
      el("div", { class: "channel-banner", text: "Chaîne en direct uniquement — voir l'onglet Lives" }),
    );
  }
  view.append(searchForm, body);

  // Non bloquant : le badge s'affiche si la chaîne est dans les lives.
  api
    .lives()
    .then((lives) => {
      if (
        (lives || []).some(
          (l) => l.video && l.video.channel_id === id && l.video.platform === platform,
        )
      ) {
        liveBadge.hidden = false;
      }
    })
    .catch(() => {});
  body.append(skeletonGrid(8));

  // Pagination : `videos` grandit sur place, donc « Tout lire » et les index
  // des cartes déjà rendues restent valides après chaque page.
  const PAGE_SIZE = 50;
  const videos = [];
  const grid = el("div", { class: "grid" });

  function appendPage(items) {
    const base = videos.length;
    videos.push(...items);
    grid.append(
      ...items.map((v, i) => videoCard(v, { onOpen: () => playVideos(videos, base + i) })),
    );
  }

  const moreBtn = el("button", {
    class: "btn load-more",
    text: "Charger plus",
    onclick: async () => {
      moreBtn.disabled = true;
      moreBtn.textContent = "Chargement…";
      try {
        const page = await api.channelVideos(id, platform, PAGE_SIZE, videos.length, q);
        appendPage(page.items || []);
        if (!page.has_more) {
          moreBtn.remove();
          return;
        }
      } catch (err) {
        errorToast(err);
      }
      moreBtn.textContent = "Charger plus";
      moreBtn.disabled = false;
    },
  });

  try {
    const out = await api.channelVideos(id, platform, PAGE_SIZE, 0, q);
    if (out.channel && out.channel.title) title.textContent = out.channel.title;
    if (!(out.items || []).length) {
      body.replaceChildren(
        emptyState(q ? `Aucune vidéo pour « ${q} »` : "Cette chaîne n'a aucune vidéo"),
      );
      return;
    }
    playAllBtn.disabled = false;
    playAllBtn.addEventListener("click", () => playVideos(videos));
    appendPage(out.items);
    body.replaceChildren(grid);
    if (out.has_more) body.append(moreBtn);
  } catch (err) {
    errorToast(err);
    body.replaceChildren(emptyState("Impossible de charger la chaîne"));
  }
}
