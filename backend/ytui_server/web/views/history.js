// Historique de visionnage : lignes avec position/progression, suppression directe.

import { api } from "../js/api.js";
import {
  el,
  iconBtn,
  skeletonRows,
  emptyState,
  errorToast,
  fmtCount,
  fmtDuration,
  fmtRelative,
  playVideos,
} from "../js/ui.js";

export async function render(view) {
  view.append(el("div", { class: "page-head" }, el("h1", { text: "Historique" })));
  const body = el("div");
  view.append(body);
  body.append(skeletonRows(8));

  let entries;
  try {
    entries = await api.history(200);
  } catch (err) {
    errorToast(err);
    body.replaceChildren(emptyState("Impossible de charger l'historique"));
    return;
  }
  if (!entries.length) {
    body.replaceChildren(emptyState("Historique vide", "history"));
    return;
  }

  const rows = el("div", { class: "rows" }, entries.map((entry) => historyRow(entry, body)));
  body.replaceChildren(rows);
}

function historyRow(entry, body) {
  const v = entry.video;
  const position = entry.position || 0;
  const duration = v.duration || 0;
  const timeLine =
    position > 0 && duration
      ? `${fmtDuration(position)} / ${fmtDuration(duration)}`
      : duration
        ? fmtDuration(duration)
        : "";
  const watchedDate = entry.watched_at ? new Date(entry.watched_at * 1000) : null;
  const views = v.view_count != null ? `${fmtCount(v.view_count)} vues` : "";
  const sub = [
    v.channel_title,
    views,
    timeLine,
    watchedDate ? fmtRelative(watchedDate) : "",
  ]
    .filter(Boolean)
    .join(" · ");
  const row = el(
    "div",
    {
      class: "row",
      tabindex: "0",
      role: "button",
      onclick: () => playVideos([v]),
      onkeydown: (e) => {
        if (e.key === "Enter") playVideos([v]);
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
      el("div", { class: "sub", text: sub }),
      position > 0 && duration
        ? el(
            "div",
            { class: "progressbar" },
            el("div", { style: `width:${Math.min(100, (position / duration) * 100)}%` }),
          )
        : null,
    ),
    el(
      "div",
      { class: "actions" },
      iconBtn("trash-2", null, {
        class: "danger",
        title: "Retirer de l'historique",
        onclick: async (e) => {
          e.stopPropagation();
          try {
            await api.removeWatch(v.video_id, v.platform);
            row.remove(); // pas de confirmation — parité avec le swipe mobile
            if (!body.querySelector(".row")) {
              body.replaceChildren(emptyState("Historique vide", "history"));
            }
          } catch (err) {
            errorToast(err);
          }
        },
      }),
    ),
  );
  row._video = v; // raccourci q (file d'attente)
  return row;
}
