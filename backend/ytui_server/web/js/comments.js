// Panneau de commentaires réutilisable (page détails et panneau latéral de la
// page lecture). Seul endroit du front qui sait rendre un commentaire.

import { api, ApiError } from "./api.js";
import { el, spinner, errorToast, toast, fmtRelative } from "./ui.js";

const PAGE_SIZE = 50;

export function commentCard(c) {
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

// Bloc autonome : charge sa première page tout seul, gère la pagination par
// curseur opaque et (sur YouTube) la publication. Rien à nettoyer côté appelant.
export function createComments({ videoId, platform }) {
  const wrap = el("div", { class: "comments" });
  const title = el("div", { class: "section-title", text: "Commentaires" });
  const list = el("div");
  const moreBtn = el("button", {
    class: "btn more",
    text: "Plus",
    hidden: true,
    onclick: () => loadPage(),
  });
  const load = spinner();

  let total = 0;
  let cursor = "";
  let firstPage = true;
  let loading = false;

  function showTotal() {
    title.textContent = total > 0 ? `Commentaires (${total})` : "Commentaires";
  }

  let form = null;
  let input = null;
  if (platform === "youtube") {
    input = el("input", { class: "input", type: "text", placeholder: "Ajouter un commentaire…" });
    form = el(
      "form",
      {
        class: "comment-form",
        onsubmit: async (e) => {
          e.preventDefault();
          const text = input.value.trim();
          if (!text) return;
          try {
            const created = await api.commentVideo(videoId, text);
            input.value = "";
            if (created) list.prepend(commentCard(created));
            total += 1;
            showTotal();
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
    );
  }

  wrap.append(title, ...(form ? [form] : []), list, moreBtn, load);

  async function loadPage() {
    if (loading) return;
    loading = true;
    moreBtn.hidden = true;
    load.hidden = false;
    try {
      const out = await api.videoComments(videoId, platform, { cursor, pageSize: PAGE_SIZE });
      // `total` n'est renseigné que sur la première page : on garde le connu.
      if (out.total > 0) {
        total = out.total;
        showTotal();
      }
      const items = out.items || [];
      if (out.disabled) {
        if (form) form.hidden = true;
        if (firstPage) list.append(el("div", { class: "sub", text: "Commentaires désactivés" }));
        cursor = "";
      } else {
        items.forEach((c) => list.append(commentCard(c)));
        if (firstPage && !items.length) {
          list.append(el("div", { class: "sub", text: "Aucun commentaire" }));
        }
        cursor = out.next_cursor || "";
      }
      moreBtn.hidden = !cursor;
      firstPage = false;
    } catch (err) {
      errorToast(err, "Commentaires indisponibles");
      if (firstPage) {
        const msg =
          err instanceof ApiError && err.status === 409
            ? "Compte YouTube non connecté"
            : "Commentaires indisponibles";
        list.append(el("div", { class: "sub", text: msg }));
      }
    } finally {
      loading = false;
      load.hidden = true;
    }
  }

  loadPage(); // async : l'appelant affiche le bloc sans attendre le réseau
  return wrap;
}
