// Détails d'une vidéo : métadonnées, description, actions YouTube,
// commentaires Odysee (lecture seule, paginés).

import { api, ApiError } from "../js/api.js";
import {
  el,
  spinner,
  emptyState,
  errorToast,
  toast,
  fmtCount,
  fmtDate,
  fmtRelative,
  channelPath,
  playVideos,
} from "../js/ui.js";

const PAGE_SIZE = 50;

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

  const asVideo = {
    video_id: d.video_id,
    platform,
    kind: "video",
    title: d.title,
    channel_title: d.channel_title,
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
      el("a", {
        href: "#" + channelPath(d.channel_id, platform, d.channel_title),
        text: d.channel_title || "",
      }),
    ),
    meta ? el("div", { class: "detail-meta", text: meta }) : null,
  );

  const actions = el(
    "div",
    { class: "detail-actions" },
    el("button", {
      class: "btn primary",
      text: "▶ Lire",
      onclick: () => playVideos([asVideo]),
    }),
  );
  if (platform === "youtube") {
    actions.append(
      el("button", {
        class: "btn",
        text: "👍 J'aime",
        onclick: async () => {
          try {
            await api.likeVideo(d.video_id);
            toast("Vidéo aimée 👍");
          } catch (err) {
            if (err instanceof ApiError && err.status === 409) {
              toast("Compte YouTube non connecté", { error: true });
            } else {
              errorToast(err);
            }
          }
        },
      }),
    );
  }
  detail.append(actions);

  if (platform === "youtube") {
    const input = el("input", {
      class: "input",
      type: "text",
      placeholder: "Ajouter un commentaire…",
    });
    detail.append(
      el(
        "form",
        {
          class: "comment-form",
          onsubmit: async (e) => {
            e.preventDefault();
            const text = input.value.trim();
            if (!text) return;
            try {
              await api.commentVideo(d.video_id, text);
              input.value = "";
              toast("Commentaire publié");
            } catch (err) {
              if (err instanceof ApiError && err.status === 409) {
                toast("Compte YouTube non connecté", { error: true });
              } else {
                errorToast(err);
              }
            }
          },
        },
        input,
        el("button", { class: "btn", type: "submit", text: "Commenter" }),
      ),
    );
  }

  if (d.description) {
    detail.append(el("div", { class: "desc" }, linkify(d.description)));
  }

  view.append(detail);

  if (platform === "odysee") {
    view.append(await odyseeComments(d.video_id));
  }
}

// ─── Commentaires Odysee ───

function commentCard(c) {
  const when = c.timestamp ? fmtRelative(new Date(c.timestamp * 1000).toISOString()) : "";
  const stats = [
    c.likes ? `👍 ${c.likes}` : "",
    c.dislikes ? `👎 ${c.dislikes}` : "",
    c.replies ? `${c.replies} réponse${c.replies > 1 ? "s" : ""}` : "",
  ]
    .filter(Boolean)
    .join(" · ");
  return el(
    "div",
    { class: "comment" },
    el(
      "div",
      { class: "comment-head" },
      el("span", { class: "comment-author", text: c.channel_name || "Anonyme" }),
      c.is_pinned ? el("span", { class: "comment-pin", text: "Épinglé" }) : null,
      when ? el("span", { class: "comment-when", text: when }) : null,
    ),
    el("div", { class: "comment-text", text: c.text }),
    stats ? el("div", { class: "comment-stats", text: stats }) : null,
  );
}

async function odyseeComments(videoId) {
  const wrap = el("div", { class: "comments" });
  const title = el("div", { class: "section-title", text: "Commentaires" });
  const list = el("div");
  wrap.append(title, list, spinner());
  let page = 1;

  async function loadPage() {
    try {
      const out = await api.videoComments(videoId, page, PAGE_SIZE);
      wrap.querySelector(".spinner")?.remove();
      wrap.querySelector(".btn.more")?.remove();
      title.textContent = `Commentaires (${out.total})`;
      const items = out.items || [];
      if (page === 1 && !items.length) {
        list.append(el("div", { class: "sub", text: "Aucun commentaire" }));
        return;
      }
      items.forEach((c) => list.append(commentCard(c)));
      if (items.length === PAGE_SIZE) {
        wrap.append(
          el("button", {
            class: "btn more",
            text: "Plus",
            onclick: (e) => {
              page += 1;
              e.target.disabled = true;
              loadPage();
            },
          }),
        );
      }
    } catch (err) {
      wrap.querySelector(".spinner")?.remove();
      errorToast(err, "Commentaires indisponibles");
      if (page === 1) list.append(el("div", { class: "sub", text: "Commentaires indisponibles" }));
    }
  }

  loadPage(); // async : la page s'affiche sans attendre les commentaires
  return wrap;
}
