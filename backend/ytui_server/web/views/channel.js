// Page chaîne : dernières vidéos, recherche dans la chaîne, Suivre et Tout lire.

import { api, ApiError } from "../js/api.js";
import { navigate } from "../js/router.js";
import {
  el,
  spinner,
  emptyState,
  errorToast,
  toast,
  videoCard,
  playVideos,
  channelPath,
} from "../js/ui.js";

// Ref de follow — même construction que mobile/lib/screens/channel.dart :
// préfixe plateforme pour les plateformes non-YouTube, id brut pour YouTube.
function followRef(platform, channelId) {
  return platform === "youtube" ? channelId : `${platform}:${channelId}`;
}

export async function render(view, { params, query }) {
  const { platform, id } = params;
  const channelName = query.get("title") || "";
  // La recherche vit dans le hash : l'URL reste partageable et un nouveau terme
  // re-rend la vue depuis zéro, donc la pagination repart proprement.
  const q = (query.get("q") || "").trim();
  const title = el("h1", { text: channelName || "Chaîne" });
  const followBtn = el("button", { class: "btn", text: "Suivre", disabled: true });
  const playAllBtn = el("button", { class: "btn primary", text: "▶ Tout lire", disabled: true });
  const body = el("div");

  const searchInput = el("input", {
    class: "input",
    id: "search-input",
    type: "search",
    placeholder: "Rechercher dans la chaîne…",
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
    el("button", { class: "btn primary", type: "submit", text: "Rechercher" }),
    q ? el("button", { class: "btn", type: "button", text: "Effacer", onclick: () => {
      searchInput.value = "";
      navigate(channelPath(id, platform, channelName));
    } }) : null,
  );

  view.append(
    el("div", { class: "page-head" }, title, playAllBtn, followBtn),
    searchForm,
    body,
  );
  body.append(spinner());

  // État de suivi (non bloquant si /channels échoue).
  let followed = false;
  const followedCheck = api
    .channels()
    .then((chans) => {
      followed = chans.some((c) => c.channel_id === id);
      syncFollowBtn();
    })
    .catch(() => {});

  function syncFollowBtn() {
    followBtn.textContent = followed ? "Suivi ✓" : "Suivre";
    followBtn.disabled = followed;
  }

  followBtn.addEventListener("click", async () => {
    followBtn.disabled = true;
    try {
      await api.followChannel(followRef(platform, id));
      followed = true;
      toast("Chaîne suivie");
    } catch (err) {
      if (err instanceof ApiError && err.status === 409) {
        followed = true;
        toast("Chaîne déjà suivie");
      } else if (err instanceof ApiError && err.status === 404) {
        toast("Chaîne introuvable", { error: true });
      } else if (err instanceof ApiError && err.status === 502) {
        toast("Erreur de la plateforme", { error: true });
      } else {
        errorToast(err);
      }
    }
    syncFollowBtn();
  });

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
    await followedCheck;
    syncFollowBtn();
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
