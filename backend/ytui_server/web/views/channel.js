// Page chaîne : dernières vidéos + bouton Suivre + Tout lire.

import { api, ApiError } from "../js/api.js";
import {
  el,
  spinner,
  emptyState,
  errorToast,
  toast,
  videoCard,
  playVideos,
} from "../js/ui.js";

// Ref de follow — même construction que mobile/lib/screens/channel.dart :
// préfixe plateforme pour les plateformes non-YouTube, id brut pour YouTube.
function followRef(platform, channelId) {
  return platform === "youtube" ? channelId : `${platform}:${channelId}`;
}

export async function render(view, { params, query }) {
  const { platform, id } = params;
  const title = el("h1", { text: query.get("title") || "Chaîne" });
  const followBtn = el("button", { class: "btn", text: "Suivre", disabled: true });
  const playAllBtn = el("button", { class: "btn primary", text: "▶ Tout lire", disabled: true });
  const body = el("div");
  view.append(el("div", { class: "page-head" }, title, playAllBtn, followBtn), body);
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

  try {
    const out = await api.channelVideos(id, platform, 50);
    await followedCheck;
    syncFollowBtn();
    if (out.channel && out.channel.title) title.textContent = out.channel.title;
    const videos = out.items || [];
    if (!videos.length) {
      body.replaceChildren(emptyState("Cette chaîne n'a aucune vidéo"));
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
    body.replaceChildren(emptyState("Impossible de charger la chaîne"));
  }
}
