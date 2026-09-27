// Shared DOM helpers: element builder, formatters, video cards/rows,
// context menu, toasts and modals. All user-facing labels are French.

import { api, ApiError } from "./api.js";
import { watched } from "./state.js";
import { queue } from "./queue.js";
import { navigate } from "./router.js";
import { icon } from "./icons.js";

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
export const emptyState = (msg, iconName = null) =>
  el(
    "div",
    { class: "empty" },
    iconName ? icon(iconName, { size: 40, cls: "empty-icon" }) : null,
    el("div", { text: msg }),
  );

/// Bouton à icône. Sans libellé, l'icône seule s'affiche et `title` sert de
/// nom accessible. Le libellé vit dans un <span class="lbl"> : les bascules
/// le mettent à jour via setBtnLabel() sans effacer l'icône.
export function iconBtn(name, label, attrs = {}) {
  const { class: cls = "", ...rest } = attrs;
  if (!label) {
    return el(
      "button",
      {
        class: `btn icon ${cls}`.trim(),
        type: "button",
        "aria-label": rest.title,
        ...rest,
      },
      icon(name),
    );
  }
  return el(
    "button",
    { class: `btn ${cls}`.trim(), type: "button", ...rest },
    icon(name),
    el("span", { class: "lbl", text: label }),
  );
}

export function setBtnLabel(btn, label) {
  const span = btn.querySelector(".lbl");
  if (span) span.textContent = label;
  else btn.textContent = label;
}

// ─── Squelettes de chargement (à la place d'un spinner centré) ───

export function skeletonGrid(count = 8) {
  return el(
    "div",
    { class: "grid", "aria-busy": "true", "aria-label": "Chargement…" },
    Array.from({ length: count }, () =>
      el(
        "div",
        { class: "card skeleton" },
        el("div", { class: "thumb sk" }),
        el(
          "div",
          { class: "meta" },
          el("div", { class: "txt" }, el("div", { class: "sk sk-line" }), el("div", { class: "sk sk-line short" })),
        ),
      ),
    ),
  );
}

export function skeletonRows(count = 6) {
  return el(
    "div",
    { class: "rows", "aria-busy": "true", "aria-label": "Chargement…" },
    Array.from({ length: count }, () =>
      el(
        "div",
        { class: "row skeleton" },
        el("div", { class: "row-thumb sk" }),
        el("div", { class: "txt" }, el("div", { class: "sk sk-line" }), el("div", { class: "sk sk-line short" })),
      ),
    ),
  );
}

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

// Public page of a video on its own platform — what a user expects when they
// share a link. The backend already computes it (Video.url, a computed field),
// so it is only rebuilt here when a listing predates that field.
export function publicVideoUrl(v) {
  if (v.url) return v.url;
  const vid = v.video_id;
  switch (v.platform) {
    case "odysee":
      return `https://odysee.com/${vid}`;
    case "bitchute":
      return `https://www.bitchute.com/video/${vid}/`;
    case "crowdbunker":
      return `https://crowdbunker.com/v/${vid}`;
    case "twitch":
      if (vid.includes(":")) return `https://www.twitch.tv/${vid.split(":")[0]}`;
      if (/^v\d+$/.test(vid)) return `https://www.twitch.tv/videos/${vid.slice(1)}`;
      return `https://www.twitch.tv/${vid}`;
    case "tiktok":
      if (vid.includes(":")) return `https://www.tiktok.com/@${vid.split(":")[0]}/live`;
      if (v.channel_id) return `https://www.tiktok.com/@${v.channel_id}/video/${vid}`;
      return `https://www.tiktok.com/embed/${vid}`;
    default:
      return `https://www.youtube.com/watch?v=${vid}`;
  }
}

// Composite live ids ("login:stream_id" / "user:room_id") mark live content.
export const isLiveVideo = (v) =>
  (v.platform === "twitch" || v.platform === "tiktok") &&
  v.kind === "video" &&
  String(v.video_id).includes(":");

// ─── Video card (grid) ───

export const PLATFORM_LABELS = {
  odysee: "Odysee",
  bitchute: "BitChute",
  twitch: "Twitch",
  tiktok: "TikTok",
  crowdbunker: "CrowdBunker",
};

// Au-delà de cette fraction, une vidéo est « vue » (grisée) — même seuil
// que la règle de reprise (95 %) arrondi vers le bas pour les génériques.
const DONE_FRACTION = 0.9;

export function videoCard(video, { live = false, onOpen } = {}) {
  const isLive = live || isLiveVideo(video);
  const isVideo = video.kind === "video";
  const seen = !isLive && isVideo && watched.has(video);
  const fraction = !isLive && isVideo ? watched.fraction(video) : 0;
  const done = fraction >= DONE_FRACTION;
  const platformLabel = PLATFORM_LABELS[video.platform];
  const thumb = el(
    "div",
    { class: "thumb" },
    video.thumbnail_url
      ? el("img", { src: video.thumbnail_url, loading: "lazy", alt: "" })
      : el("div", { class: "ph" }, icon(video.kind === "channel" ? "user" : "play", { size: 30 })),
    platformLabel
      ? el("span", { class: `chip platform ${video.platform}`, text: platformLabel })
      : null,
    !isVideo
      ? el(
          "span",
          { class: "badge kind" },
          icon(video.kind === "channel" ? "user" : "list-video", { size: 13 }),
          video.kind === "channel" ? "Chaîne" : "Playlist",
        )
      : null,
    isLive ? el("span", { class: `badge live ${video.platform}`, text: "EN DIRECT" }) : null,
    !isLive && video.duration
      ? el("span", { class: "badge", text: fmtDuration(video.duration) })
      : null,
    seen && (done || !fraction)
      ? el(
          "span",
          { class: "badge seen", title: done ? "Déjà vue" : "Déjà ouverte" },
          icon("check", { size: 13 }),
          done ? "Vue" : null,
        )
      : null,
    fraction > 0
      ? el(
          "div",
          { class: "thumb-progress" },
          el("div", { style: `width:${Math.round(Math.min(1, fraction) * 100)}%` }),
        )
      : null,
  );
  const views =
    video.kind === "video" && video.view_count != null
      ? `${fmtCount(video.view_count)} vues`
      : "";
  const when = video.published ? fmtRelative(video.published) : "";
  const card = el(
    "div",
    {
      class: done ? "card done" : "card",
      tabindex: "0",
      role: "button",
      "aria-label": video.title,
    },
    thumb,
    el(
      "div",
      { class: "meta" },
      el(
        "div",
        { class: "txt" },
        el("div", { class: "title", title: video.title, text: video.title }),
        video.channel_title ? el("div", { class: "sub channel-name", text: video.channel_title }) : null,
        views || when ? el("div", { class: "sub", text: [views, when].filter(Boolean).join(" · ") }) : null,
      ),
      isVideo || video.kind === "playlist"
        ? el(
            "button",
            {
              class: "menu-btn",
              title: "Actions",
              "aria-label": "Actions",
              onclick: (e) => {
                e.stopPropagation();
                cardMenu(e, video);
              },
            },
            icon("ellipsis-vertical"),
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

// ─── Menus flottants (⋮, ⋯) et panneaux ───

function closeMenus() {
  document.querySelectorAll(".ctx-menu").forEach((m) => m.remove());
}
document.addEventListener("click", closeMenus);

/// Ferme le menu contextuel ou le panneau ouvert (Échap). Vrai si quelque
/// chose a été fermé : la touche n'a alors pas d'autre effet.
export function closeFloating() {
  const menus = document.querySelectorAll(".ctx-menu");
  if (menus.length) {
    closeMenus();
    return true;
  }
  const open = [...document.querySelectorAll(".popover")].find((p) => !p.hidden);
  if (open) {
    if (typeof open._close === "function") open._close();
    else open.hidden = true;
    return true;
  }
  return false;
}

/// Menu d'actions : `items` = [{ icon, label, onClick }] (les null sont
/// ignorés). Ancré au clic, ou sous le bouton quand il vient du clavier.
export function showMenu(evt, items) {
  closeMenus();
  const menu = el(
    "div",
    { class: "ctx-menu", role: "menu" },
    items.filter(Boolean).map(({ icon: name, label, onClick }) =>
      el(
        "button",
        {
          role: "menuitem",
          onclick: () => {
            closeMenus();
            onClick();
          },
        },
        name ? icon(name, { size: 16 }) : null,
        el("span", { text: label }),
      ),
    ),
  );
  document.body.append(menu);
  const rect = menu.getBoundingClientRect();
  let x = evt.clientX;
  let y = evt.clientY;
  const anchor = evt.currentTarget || evt.target;
  if ((!x && !y) && anchor && anchor.getBoundingClientRect) {
    const r = anchor.getBoundingClientRect();
    x = r.right - rect.width;
    y = r.bottom + 4;
  }
  menu.style.left = Math.max(8, Math.min(x, window.innerWidth - rect.width - 8)) + "px";
  menu.style.top = Math.max(8, Math.min(y, window.innerHeight - rect.height - 8)) + "px";
  menu.querySelector("button")?.focus();
  return menu;
}

export function cardMenu(evt, video) {
  const isPlaylist = video.kind === "playlist";
  showMenu(evt, [
    isPlaylist
      ? null
      : {
          icon: "list-end",
          label: "Ajouter à la file d'attente",
          onClick: () => {
            queue.enqueue(video);
            toast("Ajouté à la file d'attente");
          },
        },
    isPlaylist
      ? null
      : { icon: "info", label: "Détails", onClick: () => navigate(detailPath(video)) },
    isPlaylist
      ? {
          icon: "download",
          label: "Importer dans une playlist locale",
          onClick: () =>
            importPlaylistModal({
              source: video.video_id,
              platform: video.platform,
              defaultName: video.title,
            }),
        }
      : null,
    // Les cartes « chaîne » ouvrent déjà la chaîne au clic.
    video.kind !== "channel" && video.channel_id
      ? {
          icon: "user",
          label: "Ouvrir la chaîne",
          onClick: () =>
            navigate(channelPath(video.channel_id, video.platform, video.channel_title)),
        }
      : null,
    isPlaylist
      ? null
      : { icon: "list-plus", label: "Ajouter à une playlist", onClick: () => addToPlaylistModal(video) },
  ]);
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

// ─── Import d'une playlist distante ───

/// POST l'import puis résume le résultat ; renvoie la playlist locale ou null.
async function runImport(source, { platform = "youtube", name = "", targetId = null } = {}) {
  toast("Import en cours…");
  try {
    const out = await api.importPlaylist(source, { platform, name, targetId });
    const skipped = out.skipped ? `, ${out.skipped} ignorée(s)` : "";
    toast(`${out.added} vidéo(s) importée(s) dans « ${out.playlist.name} »${skipped}`);
    return out.playlist;
  } catch (err) {
    if (err instanceof ApiError && err.status === 409) {
      toast("Nom déjà pris — choisissez la playlist existante", { error: true });
    } else {
      errorToast(err);
    }
    return null;
  }
}

/// Choix de la cible (playlist existante ou nouvelle) puis import complet.
export async function importPlaylistModal({ source, platform = "youtube", defaultName = "" }) {
  if (!source) return null;
  let lists = [];
  try {
    lists = await api.playlists();
  } catch (err) {
    errorToast(err);
    return null;
  }
  return new Promise((resolve) => {
    const { close } = openModal(
      "Importer la playlist dans…",
      el(
        "div",
        { class: "list-pick" },
        el(
          "button",
          {
            class: "btn primary",
            type: "button",
            onclick: async () => {
              close();
              const name = await promptModal("Nouvelle playlist", {
                value: defaultName,
                placeholder: "Nom de la playlist",
                submitLabel: "Importer",
              });
              if (!name) return resolve(null);
              resolve(await runImport(source, { platform, name }));
            },
          },
          icon("plus"),
          el("span", { text: "Nouvelle playlist" }),
        ),
        lists.map((p) =>
          el(
            "button",
            {
              class: "btn",
              onclick: async () => {
                close();
                resolve(await runImport(source, { platform, targetId: p.id }));
              },
            },
            `${p.name} (${p.count})`,
          ),
        ),
      ),
    );
  });
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
