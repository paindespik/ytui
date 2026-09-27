// Accueil : lives épinglés en tête (carrousel) + feed des chaînes suivies,
// groupé par date et filtrable par plateforme ou par chaîne.

import { api } from "../js/api.js";
import {
  el,
  emptyState,
  errorToast,
  iconBtn,
  skeletonGrid,
  videoCard,
  PLATFORM_LABELS,
} from "../js/ui.js";
import { feedNewSince } from "../js/state.js";

const DAY_MS = 24 * 3600 * 1000;

/// Libellé du groupe de date d'une vidéo (jours calendaires locaux).
export function dateGroup(published, now = new Date()) {
  const d = published ? new Date(published) : null;
  if (!d || Number.isNaN(d.getTime())) return "Sans date";
  const startOfToday = new Date(now.getFullYear(), now.getMonth(), now.getDate()).getTime();
  const t = d.getTime();
  if (t >= startOfToday) return "Aujourd'hui";
  if (t >= startOfToday - DAY_MS) return "Hier";
  if (t >= startOfToday - 6 * DAY_MS) return "Cette semaine";
  if (t >= startOfToday - 29 * DAY_MS) return "Ce mois-ci";
  return "Plus ancien";
}

/// Groupes consécutifs dans l'ordre du flux (déjà trié du plus récent au plus
/// ancien) : [{ label, videos }].
export function groupByDate(videos, now = new Date()) {
  const groups = [];
  for (const v of videos) {
    const label = dateGroup(v.published, now);
    const last = groups[groups.length - 1];
    if (last && last.label === label) last.videos.push(v);
    else groups.push({ label, videos: [v] });
  }
  return groups;
}

const channelKey = (v) => `${v.platform}:${v.channel_id || v.channel_title}`;

export function filterVideos(videos, { platform = "", channel = "" } = {}) {
  return videos.filter(
    (v) => (!platform || v.platform === platform) && (!channel || channelKey(v) === channel),
  );
}

const platformLabel = (p) => PLATFORM_LABELS[p] || "YouTube";

// Filtres de la session (survivent à un aller-retour sur une vidéo).
const filters = { platform: "", channel: "" };

export async function render(view) {
  const body = el("div");
  const refreshBtn = iconBtn("refresh-cw", "Actualiser", {
    title: "Actualiser (r)",
    onclick: () => load(true),
  });
  view.append(
    el("div", { class: "page-head" }, el("h1", { text: "Accueil" }), refreshBtn),
    body,
  );

  // Même intervalle que la TUI et le mobile (5 min) : plus court que le TTL de
  // cache du backend (15 min), donc ces rafraîchissements sont servis depuis le
  // cache et la latence de découverte reste bornée à ~5 min.
  const REFRESH_MS = 5 * 60 * 1000;
  // Référence du badge « Nouveau » : la visite précédente (figée pour l'onglet).
  const newSince = feedNewSince();

  // Signature du dernier rendu : un rafraîchissement silencieux identique ne
  // re-rend rien (sinon focus clavier et position de lecture sautaient toutes
  // les 5 min pour rien).
  let lastSignature = "";
  // Numéro du chargement en cours : seule la réponse du plus récent s'affiche
  // (un « Actualiser » lent doublé par le tick périodique arrivait après lui
  // et réaffichait l'ancien flux).
  let seq = 0;
  let busy = false;
  let videos = [];
  const listHost = el("div");

  async function load(refresh, silent = false) {
    if (silent && busy) return; // un chargement explicite est déjà en vol
    const mine = ++seq;
    busy = true;
    refreshBtn.disabled = true;
    if (!silent) body.replaceChildren(skeletonGrid(12));
    try {
      await fill(refresh, silent, mine);
    } finally {
      if (mine === seq) {
        busy = false;
        refreshBtn.disabled = false;
      }
    }
  }

  async function fill(refresh, silent, mine) {
    let lives = [];
    try {
      lives = await api.lives();
      // Tient à jour le compteur de la navigation sans requête de plus.
      window.dispatchEvent(new CustomEvent("ytui:lives", { detail: lives.length }));
    } catch {
      /* non bloquant : le feed reste affichable sans les lives */
    }
    let out;
    try {
      out = await api.feed(refresh);
    } catch (err) {
      if (silent || mine !== seq) return; // tick raté : garder la page telle quelle
      errorToast(err);
      body.replaceChildren(emptyState("Impossible de charger le flux", "refresh-cw"));
      return;
    }
    if (mine !== seq) return; // un chargement plus récent a la main
    const signature = JSON.stringify([
      lives.map((l) => `${l.video.platform}:${l.video.video_id}`),
      (out.videos || []).map((v) => `${v.platform}:${v.video_id}`),
      out.warnings || [],
    ]);
    if (silent && signature === lastSignature) return;
    lastSignature = signature;
    videos = out.videos || [];
    body.replaceChildren();
    for (const w of out.warnings || []) {
      body.append(el("div", { class: "notice", text: w }));
    }
    if (lives.length) {
      body.append(
        el("div", { class: "section-title live-title", text: `En direct (${lives.length})` }),
        el(
          "div",
          { class: "shelf-row live-row" },
          lives.map((l) => videoCard(l.video, { live: true })),
        ),
      );
    }
    if (!videos.length && !lives.length) {
      body.append(emptyState("Aucune vidéo — suivez des chaînes dans les Réglages.", "radio"));
      return;
    }
    if (videos.length) body.append(filterBar(), listHost);
    renderList();
  }

  // ─── Filtres ───

  const filterHost = el("div", { class: "filter-bar", role: "toolbar", "aria-label": "Filtres" });

  function filterBar() {
    const platforms = [...new Set(videos.map((v) => v.platform))];
    const channels = new Map();
    for (const v of videos) {
      const key = channelKey(v);
      if (!channels.has(key) && (v.channel_title || v.channel_id)) {
        channels.set(key, { key, name: v.channel_title || v.channel_id, platform: v.platform });
      }
    }
    // Filtre devenu sans objet (chaîne retirée, plateforme disparue) : oublié.
    if (filters.platform && !platforms.includes(filters.platform)) filters.platform = "";
    if (filters.channel && !channels.has(filters.channel)) filters.channel = "";

    const chip = (label, value) =>
      el("button", {
        class: filters.platform === value ? "chip-btn active" : "chip-btn",
        type: "button",
        "aria-pressed": filters.platform === value ? "true" : "false",
        text: label,
        onclick: () => {
          filters.platform = value;
          filters.channel = ""; // une chaîne d'une autre plateforme ne tient plus
          filterBar();
          renderList();
        },
      });

    const sortedChannels = [...channels.values()]
      .filter((c) => !filters.platform || c.platform === filters.platform)
      .sort((a, b) => a.name.localeCompare(b.name, "fr", { sensitivity: "base" }));
    const channelSel = el(
      "select",
      {
        class: "input channel-filter",
        "aria-label": "Filtrer par chaîne",
        onchange: (e) => {
          filters.channel = e.target.value;
          renderList();
        },
      },
      el("option", { value: "" }, "Toutes les chaînes"),
      sortedChannels.map((c) =>
        el("option", { value: c.key, selected: c.key === filters.channel }, c.name),
      ),
    );

    filterHost.replaceChildren(
      ...(platforms.length > 1
        ? [chip("Tout", ""), ...platforms.map((p) => chip(platformLabel(p), p))]
        : []),
      sortedChannels.length > 1 ? channelSel : null,
    );
    filterHost.hidden = !filterHost.childElementCount;
    return filterHost;
  }

  function renderList() {
    const shown = filterVideos(videos, filters);
    if (!shown.length) {
      listHost.replaceChildren(emptyState("Aucune vidéo pour ce filtre", "list-video"));
      return;
    }
    const isNew = (v) =>
      Boolean(newSince && v.published && Date.parse(v.published) > newSince);
    listHost.replaceChildren(
      ...groupByDate(shown).flatMap((g) => [
        el("div", { class: "section-title date-title", text: g.label }),
        el("div", { class: "grid" }, g.videos.map((v) => videoCard(v, { isNew: isNew(v) }))),
      ]),
    );
  }

  await load(false);
  // Rafraîchissement périodique, nettoyé au démontage de la vue (sinon il
  // survit à la navigation et écrit dans un DOM détaché).
  const timer = setInterval(() => load(false, true), REFRESH_MS);
  return () => clearInterval(timer);
}
