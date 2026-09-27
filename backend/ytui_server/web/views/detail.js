// Détails d'une vidéo : métadonnées, description, actions YouTube,
// commentaires (module partagé js/comments.js).

import { api, ApiError } from "../js/api.js";
import {
  el,
  spinner,
  emptyState,
  errorToast,
  toast,
  fmtCount,
  fmtDate,
  channelPath,
  playVideos,
  iconBtn,
  setBtnLabel,
} from "../js/ui.js";
import { createComments } from "../js/comments.js";

// Autolink sans innerHTML : le texte distant reste du texte.
function linkify(text) {
  const parts = String(text || "").split(/(https?:\/\/[^\s<>"']+)/g);
  return parts.map((part, i) =>
    i % 2 === 1
      ? el("a", { href: part, target: "_blank", rel: "noopener noreferrer", text: part })
      : part,
  );
}

function fmtUploadDate(raw) {
  if (!raw) return "";
  let iso = raw;
  if (/^\d{8}$/.test(raw)) iso = `${raw.slice(0, 4)}-${raw.slice(4, 6)}-${raw.slice(6, 8)}`;
  if (/^\d{4}-\d{2}-\d{2}/.test(iso)) {
    const out = fmtDate(iso);
    if (out) return out;
  }
  return raw;
}

export async function render(view, { params }) {
  const { platform, id } = params;
  view.append(spinner());

  let d;
  try {
    d = await api.videoDetails(id, platform);
  } catch (err) {
    errorToast(err);
    view.replaceChildren(emptyState("Impossible de charger les détails"));
    return;
  }
  view.replaceChildren();

  // Tout ce que la lecture (et donc l'historique) doit garder : sans
  // channel_id ni miniature, la ligne d'historique perdait l'un et l'autre.
  const asVideo = {
    video_id: d.video_id,
    platform,
    kind: "video",
    title: d.title,
    channel_title: d.channel_title,
    channel_id: d.channel_id || "",
    thumbnail_url: d.thumbnail_url || "",
    view_count: d.view_count ?? null,
    duration: d.duration,
  };

  const meta = [
    d.view_count !== null && d.view_count !== undefined ? `${fmtCount(d.view_count)} vues` : "",
    d.like_count !== null && d.like_count !== undefined ? `${fmtCount(d.like_count)} j'aime` : "",
    fmtUploadDate(d.upload_date),
  ]
    .filter(Boolean)
    .join(" · ");

  const detail = el("div", { class: "detail" });
  detail.append(
    el("h1", { class: "detail-title", text: d.title }),
    el(
      "div",
      { class: "detail-channel" },
      // Sans id, le lien menait à « #/channel/youtube/ » : route inconnue,
      // retour silencieux à l'accueil.
      d.channel_id
        ? el("a", {
            href: "#" + channelPath(d.channel_id, platform, d.channel_title),
            text: d.channel_title || "",
          })
        : el("span", { text: d.channel_title || "" }),
    ),
    meta ? el("div", { class: "detail-meta", text: meta }) : null,
  );

  const actions = el(
    "div",
    { class: "detail-actions" },
    iconBtn("play", "Lire", { class: "primary", onclick: () => playVideos([asVideo]) }),
  );
  if (platform === "youtube") {
    actions.append(likeToggle(d.video_id));
  }
  detail.append(actions);

  if (d.description) {
    detail.append(el("div", { class: "desc" }, linkify(d.description)));
  }

  view.append(detail);

  if (platform === "youtube" || platform === "odysee") {
    view.append(createComments({ videoId: d.video_id, platform }));
  }
}

// ─── Bouton J'aime (bascule) ───

function likeToggle(videoId) {
  let liked = false;
  const btn = iconBtn("thumbs-up", "J'aime", {
    onclick: async () => {
      btn.disabled = true;
      try {
        await api.likeVideo(videoId, liked ? "none" : "like");
        liked = !liked;
        sync();
        toast(liked ? "Vidéo aimée" : "J'aime retiré");
      } catch (err) {
        if (err instanceof ApiError && err.status === 409) {
          toast("Compte YouTube non connecté", { error: true });
        } else {
          errorToast(err);
        }
      } finally {
        btn.disabled = false;
      }
    },
  });

  function sync() {
    setBtnLabel(btn, liked ? "Aimé" : "J'aime");
    btn.setAttribute("aria-pressed", liked ? "true" : "false");
    btn.classList.toggle("on", liked);
  }
  sync();

  // État initial : échec silencieux (bouton utilisable quand même).
  api
    .videoRating(videoId)
    .then((out) => {
      liked = !!(out && out.rating === "like");
      sync();
    })
    .catch(() => {});

  return btn;
}
