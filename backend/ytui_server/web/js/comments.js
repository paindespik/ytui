// Panneau de commentaires réutilisable (page détails et panneau latéral de la
// page lecture). Seul endroit du front qui sait rendre un commentaire.

import { api, ApiError } from "./api.js";
import { el, spinner, errorToast, toast, fmtRelative } from "./ui.js";

const PAGE_SIZE = 50;

const repliesLabel = (n) => `${n} réponse${n > 1 ? "s" : ""}`;

// `ctx` ({ videoId, platform }) rend la carte interactive : bascule du fil de
// réponses et, sur YouTube, composeur. Sans `ctx` la carte est inerte — c'est
// ainsi que sont rendues les réponses elles-mêmes, jamais imbriquées plus loin
// (comme sur youtube.com).
export function commentCard(c, ctx = null) {
  const when = c.timestamp ? fmtRelative(new Date(c.timestamp * 1000).toISOString()) : "";
  const counts = [c.likes ? `👍 ${c.likes}` : "", c.dislikes ? `👎 ${c.dislikes}` : ""]
    .filter(Boolean)
    .join(" · ");
  const card = el(
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
  );

  if (ctx === null) {
    const stats = [counts, c.replies ? repliesLabel(c.replies) : ""].filter(Boolean).join(" · ");
    if (stats) card.append(el("div", { class: "comment-stats", text: stats }));
    return card;
  }

  const stats = el("div", { class: "comment-stats" }, counts ? el("span", { text: counts }) : null);
  const thread = el("div", { class: "comment-replies", hidden: true });
  const moreBtn = el("button", {
    class: "btn more",
    text: "Plus de réponses",
    hidden: true,
    onclick: () => loadReplies(),
  });
  thread.append(moreBtn);

  let cursor = "";
  let loading = false;
  let loaded = false;
  let expanded = false;
  let form = null;
  let input = null;

  const toggleText = () => (expanded ? "▴ Masquer les réponses" : `▾ ${repliesLabel(c.replies)}`);
  const makeToggle = () =>
    el("button", {
      class: "btn small",
      text: toggleText(),
      onclick: () => setExpanded(!expanded),
    });
  let toggleBtn = c.replies > 0 ? makeToggle() : null;
  const replyBtn =
    ctx.platform === "youtube"
      ? el("button", { class: "btn small", text: "Répondre", onclick: () => openComposer() })
      : null;
  stats.append(...[toggleBtn, replyBtn].filter(Boolean));

  // Le bouton peut naître tard : première réponse sous un commentaire qui n'en
  // avait aucune.
  function refreshToggle() {
    if (toggleBtn) {
      toggleBtn.textContent = toggleText();
      return;
    }
    toggleBtn = makeToggle();
    if (replyBtn) replyBtn.before(toggleBtn);
    else stats.append(toggleBtn);
  }

  function setExpanded(next) {
    expanded = next;
    thread.hidden = !expanded;
    if (toggleBtn) toggleBtn.textContent = toggleText();
    if (!expanded || loaded) return;
    // Le composeur peut ouvrir un fil vide : rien à charger, et surtout ne pas
    // recharger plus tard une réponse déjà insérée localement.
    if (c.replies > 0) loadReplies();
    else loaded = true;
  }

  async function loadReplies() {
    if (loading) return;
    loading = true;
    loaded = true;
    moreBtn.hidden = true;
    const load = spinner();
    moreBtn.before(load);
    try {
      const out = await api.commentReplies(ctx.videoId, c.comment_id, ctx.platform, {
        cursor,
        pageSize: PAGE_SIZE,
      });
      (out.items || []).forEach((r) => moreBtn.before(commentCard(r)));
      cursor = out.next_cursor || "";
    } catch (err) {
      errorToast(err, "Réponses indisponibles");
      const msg =
        err instanceof ApiError && err.status === 409
          ? "Compte YouTube non connecté"
          : "Réponses indisponibles";
      moreBtn.before(el("div", { class: "sub", text: msg }));
    } finally {
      load.remove();
      loading = false;
      moreBtn.hidden = !cursor;
    }
  }

  function openComposer() {
    if (!expanded) setExpanded(true);
    if (!form) {
      input = el("input", { class: "input", type: "text", placeholder: "Répondre…" });
      form = el(
        "form",
        {
          class: "comment-form reply-form",
          onsubmit: async (e) => {
            e.preventDefault();
            const text = input.value.trim();
            if (!text) return;
            try {
              const created = await api.replyComment(ctx.videoId, c.comment_id, text, ctx.platform);
              input.value = "";
              if (created) form.before(commentCard(created));
              c.replies = (c.replies || 0) + 1;
              refreshToggle();
              toast("Réponse publiée");
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
        el("button", { class: "btn", type: "submit", text: "Répondre" }),
      );
      thread.append(form);
    }
    input.focus();
  }

  card.append(stats, thread);
  return card;
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
            if (created) list.prepend(commentCard(created, { videoId, platform }));
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
        items.forEach((c) => list.append(commentCard(c, { videoId, platform })));
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
