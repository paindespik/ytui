// Shared DOM helpers: element builder, formatters, video cards/rows,
// context menu, toasts and modals. All user-facing labels are French.

import { api, ApiError } from "./api.js";
import { watched } from "./state.js";
import { queue } from "./queue.js";
import { navigate } from "./router.js";

// ─── Element builder ───

export function el(tag, attrs = {}, ...children) {
  const node = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs)) {
    if (v === undefined || v === null || v === false) continue;
    if (k === "class") node.className = v;
    else if (k === "dataset") Object.assign(node.dataset, v);
    else if (k.startsWith("on") && typeof v === "function") node.addEventListener(k.slice(2), v);
    else if (k === "text") node.textContent = v;
    else if (v === true) node.setAttribute(k, "");
    else node.setAttribute(k, String(v));
  }
  for (const child of children.flat(Infinity)) {
    if (child === null || child === undefined || child === false) continue;
    node.append(child instanceof Node ? child : document.createTextNode(String(child)));
  }
  return node;
}

export const spinner = () => el("div", { class: "spinner" });
export const emptyState = (msg) => el("div", { class: "empty", text: msg });

// ─── Formatters (French) ───

export function fmtDuration(sec) {
  if (sec === null || sec === undefined || Number.isNaN(sec)) return "";
  sec = Math.max(0, Math.round(sec));
  const h = Math.floor(sec / 3600);
  const m = Math.floor((sec % 3600) / 60);
  const s = sec % 60;
  const mm = h > 0 ? String(m).padStart(2, "0") : String(m);
  return (h > 0 ? `${h}:${mm}:` : `${mm}:`) + String(s).padStart(2, "0");
}

export function fmtRelative(iso) {
  if (!iso) return "";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return "";
  const s = Math.floor((Date.now() - d.getTime()) / 1000);
  if (s < 60) return "à l'instant";
  const min = Math.floor(s / 60);
  if (min < 60) return `il y a ${min} min`;
  const h = Math.floor(min / 60);
  if (h < 24) return `il y a ${h} h`;
  const j = Math.floor(h / 24);
  if (j === 1) return "hier";
  if (j < 7) return `il y a ${j} j`;
  if (j < 30) return `il y a ${Math.floor(j / 7)} sem.`;
  const mois = Math.floor(j / 30);
  if (mois < 12) return `il y a ${mois} mois`;
  const ans = Math.floor(j / 365);
  return `il y a ${ans} an${ans > 1 ? "s" : ""}`;
}

const compactFmt = new Intl.NumberFormat("fr-FR", {
  notation: "compact",
  maximumFractionDigits: 1,
});
export const fmtCount = (n) =>
  n === null || n === undefined ? "" : compactFmt.format(n);

export const fmtDate = (iso) => {
  if (!iso) return "";
  const d = new Date(iso);
  return Number.isNaN(d.getTime()) ? "" : d.toLocaleDateString("fr-FR");
};

// ─── Paths & navigation by kind ───

export const watchPath = (v) => `/watch/${v.platform}/${encodeURIComponent(v.video_id)}`;
export const channelPath = (channelId, platform, title = "") =>
  `/channel/${platform}/${encodeURIComponent(channelId)}` +
  (title ? `?title=${encodeURIComponent(title)}` : "");
export const ytPlaylistPath = (v) => `/ytplaylist/${v.platform}/${encodeURIComponent(v.video_id)}`;
export const detailPath = (v) => `/detail/${v.platform}/${encodeURIComponent(v.video_id)}`;

export function playVideos(list, startIndex = 0) {
  if (!list.length) return;
  queue.play(list, startIndex);
  navigate(watchPath(list[startIndex]));
}

// Default click behavior shared by every view (mirrors mobile VideoTile).
export function openVideo(video) {
  if (video.kind === "channel") {
    navigate(channelPath(video.video_id, video.platform, video.title));
  } else if (video.kind === "playlist") {
    navigate(ytPlaylistPath(video));
  } else {
    playVideos([video]);
  }
}

// Composite live ids ("login:stream_id" / "user:room_id") mark live content.
export const isLiveVideo = (v) =>
  (v.platform === "twitch" || v.platform === "tiktok") &&
  v.kind === "video" &&
  String(v.video_id).includes(":");

// ─── Video card (grid) ───

export function videoCard(video, { live = false, onOpen } = {}) {
  const isLive = live || isLiveVideo(video);
  const isVideo = video.kind === "video";
  const thumb = el(
    "div",
    { class: "thumb" },
    video.thumbnail_url
      ? el("img", { src: video.thumbnail_url, loading: "lazy", alt: "" })
      : el("div", { class: "ph", text: video.kind === "channel" ? "◉" : "▶" }),
    !isVideo
      ? el("span", {
          class: "badge kind",
          text: video.kind === "channel" ? "chaîne" : "playlist",
        })
      : null,
    isLive ? el("span", { class: `badge live ${video.platform}`, text: "LIVE" }) : null,
    !isLive && video.duration
      ? el("span", { class: "badge", text: fmtDuration(video.duration) })
      : null,
    !isLive && isVideo && watched.has(video.video_id)
      ? el("span", { class: "badge watched", text: "✓" })
      : null,
  );
  const sub = [video.channel_title, video.published ? fmtRelative(video.published) : ""]
    .filter(Boolean)
    .join(" · ");
  const card = el(
    "div",
    { class: "card", tabindex: "0", role: "button", "aria-label": video.title },
    thumb,
    el(
      "div",
      { class: "meta" },
      el(
        "div",
        { class: "txt" },
        el("div", { class: "title", title: video.title, text: video.title }),
        sub ? el("div", { class: "sub", text: sub }) : null,
      ),
      isVideo
        ? el(
            "button",
            {
              class: "menu-btn",
              title: "Actions",
              onclick: (e) => {
                e.stopPropagation();
                cardMenu(e, video);
              },
            },
            "⋮",
          )
        : null,
    ),
  );
  const open = () => (onOpen ? onOpen(video) : openVideo(video));
  card.addEventListener("click", open);
  card.addEventListener("keydown", (e) => {
    if (e.key === "Enter") {
      e.preventDefault();
      open();
    }
  });
  card._video = video; // used by j/k + q keyboard shortcuts
  return card;
}

export function videoGrid(videos, opts = {}) {
  return el("div", { class: "grid" }, videos.map((v) => videoCard(v, opts)));
}

// ─── Context menu (⋮) ───

function closeMenus() {
  document.querySelectorAll(".ctx-menu").forEach((m) => m.remove());
}
document.addEventListener("click", closeMenus);

export function cardMenu(evt, video) {
  closeMenus();
  const menu = el(
    "div",
    { class: "ctx-menu" },
    el(
      "button",
      {
        onclick: () => {
          closeMenus();
          queue.enqueue(video);
          toast("Ajouté à la file d'attente");
        },
      },
      "Ajouter à la file d'attente",
    ),
    el(
      "button",
      {
        onclick: () => {
          closeMenus();
          navigate(detailPath(video));
        },
      },
      "Détails",
    ),
    // Les cartes « chaîne » ouvrent déjà la chaîne au clic.
    video.kind !== "channel" && video.channel_id
      ? el(
          "button",
          {
            onclick: () => {
              closeMenus();
              navigate(channelPath(video.channel_id, video.platform, video.channel_title));
            },
          },
          "Ouvrir la chaîne",
        )
      : null,
    el(
      "button",
      {
        onclick: () => {
          closeMenus();
          addToPlaylistModal(video);
        },
      },
      "Ajouter à une playlist",
    ),
  );
  document.body.append(menu);
  const rect = menu.getBoundingClientRect();
  menu.style.left = Math.min(evt.clientX, window.innerWidth - rect.width - 8) + "px";
  menu.style.top = Math.min(evt.clientY, window.innerHeight - rect.height - 8) + "px";
}

export async function addToPlaylistModal(video) {
  let lists;
  try {
    lists = await api.playlists();
  } catch (err) {
    errorToast(err);
    return;
  }
  if (!lists.length) {
    toast("Aucune playlist — créez-en une dans l'onglet Playlists");
    return;
  }
  const { close } = openModal(
    "Ajouter à une playlist",
    el(
      "div",
      { class: "list-pick" },
      lists.map((p) =>
        el(
          "button",
          {
            class: "btn",
            onclick: async () => {
              close();
              try {
                await api.addPlaylistItem(p.id, video);
                toast(`Ajouté à « ${p.name} »`);
              } catch (err) {
                if (err instanceof ApiError && err.status === 409) {
                  toast("Déjà dans cette playlist");
                } else {
                  errorToast(err);
                }
              }
            },
          },
          `${p.name} (${p.count})`,
        ),
      ),
    ),
  );
}

// ─── Toasts ───

export function toast(message, { error = false, ms = 3500 } = {}) {
  const root = document.getElementById("toasts");
  const t = el("div", { class: "toast" + (error ? " error" : ""), text: message });
  root.append(t);
  setTimeout(() => t.remove(), ms);
}

export function errorToast(err, fallback = "Une erreur est survenue") {
  const msg = (err && (err.detail || err.message)) || fallback;
  toast(msg, { error: true });
}

// ─── Modals ───

export function anyModalOpen() {
  return Boolean(document.querySelector(".modal-backdrop"));
}

export function openModal(title, content, actions = []) {
  const host = document.getElementById("modal-root");
  const backdrop = el("div", { class: "modal-backdrop" });
  const close = () => {
    backdrop.remove();
    document.removeEventListener("keydown", onKey, true);
  };
  const onKey = (e) => {
    if (e.key === "Escape") {
      e.stopPropagation();
      close();
    }
  };
  document.addEventListener("keydown", onKey, true);
  backdrop.addEventListener("click", (e) => {
    if (e.target === backdrop) close();
  });
  const box = el(
    "div",
    { class: "modal" },
    el("h2", { text: title }),
    content,
    actions.length ? el("div", { class: "modal-actions" }, actions) : null,
  );
  backdrop.append(box);
  host.append(backdrop);
  return { close, box };
}

export function confirmModal(message, { confirmLabel = "Supprimer" } = {}) {
  return new Promise((resolve) => {
    const content = el("p", { text: message });
    const cancel = el(
      "button",
      { class: "btn", onclick: () => { close(); resolve(false); } },
      "Annuler",
    );
    const ok = el(
      "button",
      { class: "btn primary", onclick: () => { close(); resolve(true); } },
      confirmLabel,
    );
    const { close } = openModal("Confirmation", content, [cancel, ok]);
    ok.focus();
  });
}

export function promptModal(title, { placeholder = "", value = "", submitLabel = "Valider" } = {}) {
  return new Promise((resolve) => {
    const input = el("input", { class: "input field", placeholder, value });
    const form = el("form", {}, input);
    const cancel = el(
      "button",
      { class: "btn", type: "button", onclick: () => { close(); resolve(null); } },
      "Annuler",
    );
    const ok = el("button", { class: "btn primary", type: "submit" }, submitLabel);
    form.append(el("div", { class: "modal-actions" }, cancel, ok));
    form.addEventListener("submit", (e) => {
      e.preventDefault();
      const v = input.value.trim();
      close();
      resolve(v || null);
    });
    const { close } = openModal(title, form);
    input.focus();
  });
}
