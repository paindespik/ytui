// Page chaîne : onglets Vidéos (plus récentes ou plus vues, recherche dans la
// chaîne, Tout lire) et Playlists (YouTube, BitChute), plus Suivre.

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

// Plateformes dont les chaînes exposent leurs playlists
// (GET /api/channels/{id}/playlists).
export const PLAYLIST_PLATFORMS = new Set(["youtube", "bitchute"]);

// Plateformes dont les vidéos se trient par nombre de vues (?sort=views).
export const SORT_PLATFORMS = new Set(["youtube", "bitchute", "crowdbunker"]);

const PAGE_SIZE = 50;

/// Chemin d'un onglet de la chaîne ; l'onglet vit dans le hash comme la
/// recherche et le tri, donc l'URL reste partageable et le retour arrière y
/// ramène.
export function channelTabPath(id, platform, title, tab, q = "", sort = "date") {
  let target = channelPath(id, platform, title);
  const params = [];
  if (tab === "playlists") params.push("tab=playlists");
  if (q) params.push(`q=${encodeURIComponent(q)}`);
  if (tab !== "playlists" && sort === "views") params.push("sort=views");
  if (params.length) target += (target.includes("?") ? "&" : "?") + params.join("&");
  return target;
}

export async function render(view, { params, query }) {
  const { platform, id } = params;
  const channelName = query.get("title") || "";
  const hasPlaylists = PLAYLIST_PLATFORMS.has(platform);
  const tab = hasPlaylists && query.get("tab") === "playlists" ? "playlists" : "videos";
  // La recherche vit dans le hash : l'URL reste partageable et un nouveau terme
  // re-rend la vue depuis zéro, donc la pagination repart proprement.
  const q = tab === "videos" ? (query.get("q") || "").trim() : "";
  // Tri des vidéos ; une recherche garde l'ordre de la plateforme, mais le
  // tri reste dans l'URL pour revenir au même ordre en l'effaçant.
  const canSort = tab === "videos" && SORT_PLATFORMS.has(platform);
  const sort = canSort && query.get("sort") === "views" ? "views" : "date";
  const title = el("h1", { text: channelName || "Chaîne" });
  // Nom de la chaîne tel que l'API le corrige, repris dans l'URL des onglets.
  let name = channelName;
  const onChannel = (channel) => {
    if (channel && channel.title) title.textContent = name = channel.title;
  };
  // Badge « en direct » : la chaîne apparaît dans /api/lives.
  const liveBadge = el("span", { class: `chip live-chip ${platform}`, text: "EN DIRECT" });
  liveBadge.hidden = true;
  const platformChip = PLATFORM_LABELS[platform]
    ? el("span", { class: `chip platform ${platform}`, text: PLATFORM_LABELS[platform] })
    : null;
  // Bascule : une chaîne suivie doit pouvoir être quittée d'ici, sans passer
  // par les Réglages.
  const followBtn = followButton(platform, id);
  // « Tout lire » enchaîne des vidéos : sans objet sur l'onglet Playlists.
  const playAllBtn =
    tab === "videos" ? iconBtn("play", "Tout lire", { class: "primary", disabled: true }) : null;
  const body = el("div");

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
  if (hasPlaylists) {
    const tabBtn = (label, value) =>
      el("button", {
        class: tab === value ? "chip-btn active" : "chip-btn",
        type: "button",
        role: "tab",
        "aria-selected": tab === value ? "true" : "false",
        text: label,
        onclick: () => {
          if (tab === value) return;
          navigate(channelTabPath(id, platform, name, value));
        },
      });
    view.append(
      el(
        "div",
        { class: "filter-bar channel-tabs", role: "tablist", "aria-label": "Contenu de la chaîne" },
        tabBtn("Vidéos", "videos"),
        tabBtn("Playlists", "playlists"),
      ),
    );
  }
  if (tab === "videos") view.append(searchForm(id, platform, channelName, q, sort));
  if (canSort && !q) {
    const sortBtn = (label, value) =>
      el("button", {
        class: sort === value ? "chip-btn active" : "chip-btn",
        type: "button",
        "aria-pressed": sort === value ? "true" : "false",
        text: label,
        onclick: () => {
          if (sort === value) return;
          navigate(channelTabPath(id, platform, name, "videos", "", value));
        },
      });
    view.append(
      el(
        "div",
        { class: "filter-bar channel-sort", role: "group", "aria-label": "Trier les vidéos" },
        sortBtn("Plus récentes", "date"),
        sortBtn("Plus vues", "views"),
      ),
    );
  }
  view.append(body);

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

  if (tab === "playlists") {
    await renderListing(body, onChannel, {
      fetchPage: (offset) => api.channelPlaylists(id, platform, PAGE_SIZE, offset),
      // Une carte de playlist ouvre sa page (openVideo par défaut).
      card: (v) => videoCard(v),
      empty: "Cette chaîne n'a aucune playlist",
      failure: "Impossible de charger les playlists",
    });
    return;
  }

  // Pagination : `videos` grandit sur place, donc « Tout lire » et les index
  // des cartes déjà rendues restent valides après chaque page.
  const videos = [];
  const shown = await renderListing(body, onChannel, {
    fetchPage: (offset) => api.channelVideos(id, platform, PAGE_SIZE, offset, q, q ? "date" : sort),
    collect: videos,
    card: (v, index) => videoCard(v, { onOpen: () => playVideos(videos, index) }),
    empty: q ? `Aucune vidéo pour « ${q} »` : "Cette chaîne n'a aucune vidéo",
    failure: "Impossible de charger la chaîne",
  });
  if (shown) {
    playAllBtn.disabled = false;
    playAllBtn.addEventListener("click", () => playVideos(videos));
  }
}

function searchForm(id, platform, channelName, q, sort) {
  const searchInput = el("input", {
    class: "input page-search",
    type: "search",
    placeholder: "Rechercher dans la chaîne…",
    "aria-label": "Rechercher dans la chaîne",
    value: q,
  });
  return el(
    "form",
    {
      class: "search-form",
      onsubmit: (e) => {
        e.preventDefault();
        const term = searchInput.value.trim();
        if (term === q) return;
        navigate(channelTabPath(id, platform, channelName, "videos", term, sort));
      },
    },
    searchInput,
    el("button", { class: "btn", type: "submit" }, icon("search"), el("span", { text: "Rechercher" })),
    q ? el("button", { class: "btn", type: "button", text: "Effacer", onclick: () => {
      searchInput.value = "";
      navigate(channelTabPath(id, platform, channelName, "videos", "", sort));
    } }) : null,
  );
}

/// Grille paginée (« Charger plus ») d'un listing de chaîne. Les éléments
/// reçus s'ajoutent à `collect` ; renvoie vrai si au moins un est affiché.
async function renderListing(body, onChannel, { fetchPage, collect = [], card, empty, failure }) {
  const grid = el("div", { class: "grid" });

  function appendPage(items) {
    const base = collect.length;
    collect.push(...items);
    grid.append(...items.map((v, i) => card(v, base + i)));
  }

  const moreBtn = el("button", {
    class: "btn load-more",
    text: "Charger plus",
    onclick: async () => {
      moreBtn.disabled = true;
      moreBtn.textContent = "Chargement…";
      try {
        const page = await fetchPage(collect.length);
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
    const out = await fetchPage(0);
    onChannel(out.channel);
    if (!(out.items || []).length) {
      body.replaceChildren(emptyState(empty));
      return false;
    }
    appendPage(out.items);
    body.replaceChildren(grid);
    if (out.has_more) body.append(moreBtn);
    return true;
  } catch (err) {
    errorToast(err);
    body.replaceChildren(emptyState(failure));
    return false;
  }
}
