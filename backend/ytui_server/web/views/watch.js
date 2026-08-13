// Page lecture : lecteur + infos + file d'attente + « À suivre ».

import { api, ApiError } from "../js/api.js";
import {
  el,
  toast,
  errorToast,
  fmtDuration,
  watchPath,
  channelPath,
  detailPath,
  addToPlaylistModal,
} from "../js/ui.js";
import { queue } from "../js/queue.js";
import { replace, navigate } from "../js/router.js";
import { Player, isLiveId } from "../js/player.js";
import { playerActions } from "../js/shortcuts.js";
import { prefs, QUALITIES, GAINS } from "../js/state.js";
import { createComments } from "../js/comments.js";

export async function render(view, { params }) {
  const { platform, id } = params;
  const live = isLiveId(platform, id);

  let video =
    queue.current && queue.current.video_id === id && queue.current.platform === platform
      ? queue.current
      : null;
  if (!video) {
    // Retour arrière / avant du navigateur pendant une playlist : la vidéo est
    // déjà dans la file, il faut s'y repositionner — la remplacer par une file
    // d'un seul élément jetterait le reste de la playlist.
    const known = queue.items.findIndex(
      (v) => v.video_id === id && v.platform === platform,
    );
    if (known >= 0) {
      queue.jumpTo(known);
      video = queue.current;
    }
  }
  if (!video) {
    // Lien direct (marque-page, file périmée) : file d'un seul élément.
    video = {
      video_id: id,
      platform,
      title: "",
      channel_title: "",
      channel_id: "",
      thumbnail_url: "",
      kind: "video",
      duration: null,
      published: null,
      playlist_id: "",
    };
    queue.play([video]);
  }

  // ─── DOM ───

  const videoEl = el("video", { controls: true, playsinline: true });
  const overlayHost = el("div");
  const playerBox = el("div", { class: "player-box" }, videoEl, overlayHost);

  const titleEl = el("h1", { text: video.title || "…" });
  const channelLink = el("a", { class: "channel", text: video.channel_title || "" });
  if (video.channel_id) {
    channelLink.href = "#" + channelPath(video.channel_id, platform, video.channel_title);
  }
  const metaLine = el("span", { class: "sub", text: live ? "EN DIRECT" : "" });

  const speedSel = el(
    "select",
    { class: "input", title: "Vitesse de lecture" },
    [0.5, 0.75, 1, 1.25, 1.5, 1.75, 2, 2.25, 2.5, 2.75, 3].map((r) =>
      el("option", { value: String(r), selected: r === 1 }, `×${r}`),
    ),
  );
  const subSel = el(
    "select",
    { class: "input", title: "Sous-titres" },
    el("option", { value: "-1" }, "Sous-titres : aucun"),
  );
  const gainSel = el(
    "select",
    { class: "input", title: "Amplification audio (au-delà de 100 %)" },
    GAINS.map((g) =>
      el("option", { value: String(g), selected: g === prefs.gain }, `Vol ×${g}`),
    ),
  );
  const qualitySel = el(
    "select",
    { class: "input", title: "Qualité maximale" },
    QUALITIES.map((q) =>
      el("option", { value: String(q), selected: q === prefs.maxHeight }, `${q}p`),
    ),
  );

  const pipBtn = document.pictureInPictureEnabled
    ? el(
        "button",
        {
          class: "btn",
          title: "Picture-in-picture",
          onclick: () => videoEl.requestPictureInPicture().catch(() => {}),
        },
        "PiP",
      )
    : null;
  const detailsBtn = el(
    "button",
    { class: "btn", onclick: () => navigate(detailPath(video)) },
    "Détails",
  );
  const plBtn = el(
    "button",
    { class: "btn", onclick: () => addToPlaylistModal(video) },
    "+ Playlist",
  );

  const queuePanel = el("div", { class: "rows" });
  const relatedPanel = el("div", { class: "rows" });
  const relatedSection = el(
    "div",
    { hidden: platform !== "youtube" || live },
    el("div", { class: "section-title", text: "À suivre" }),
    relatedPanel,
  );
  const chatLiveNow = (platform === "youtube" || platform === "twitch") && live;
  const chatPanel = el("div", { class: "chat-body" });
  const chatSection = el(
    "div",
    { class: "chat-section", hidden: !chatLiveNow },
    el("div", { class: "section-title", text: "Chat en direct" }),
    chatPanel,
  );
  const queueSection = el(
    "div",
    { hidden: chatLiveNow },
    el("div", { class: "section-title", text: "File d'attente" }),
    queuePanel,
  );

  // ─── Bascule du panneau latéral : commentaires ⇄ file / chat / à suivre ───
  //
  // Le libellé reste « Commentaires » (l'état actif est porté par la classe
  // `primary` + aria-pressed) : sur un direct le panneau masqué est le chat, pas
  // la file, donc un libellé « File d'attente » mentirait la moitié du temps.
  const commentsSection = el("div", { hidden: true });
  const sidePanels = [queueSection, chatSection, relatedSection];
  let hiddenBefore = null; // Map(section → hidden) mémorisée pendant les commentaires

  // Tant que les commentaires occupent le panneau, les mises à jour de
  // visibilité (chat qui démarre, « À suivre » qui arrive) vont dans l'état
  // mémorisé au lieu du DOM : la restauration reste exacte.
  function setSideHidden(section, hidden) {
    if (hiddenBefore) hiddenBefore.set(section, hidden);
    else section.hidden = hidden;
  }

  const commentsBtn =
    platform === "youtube" || platform === "odysee"
      ? el("button", {
          class: "btn",
          text: "Commentaires",
          "aria-pressed": "false",
          onclick: () => toggleComments(),
        })
      : null;

  function toggleComments() {
    if (hiddenBefore) {
      for (const [section, hidden] of hiddenBefore) section.hidden = hidden;
      hiddenBefore = null;
      commentsSection.hidden = true;
    } else {
      hiddenBefore = new Map(sidePanels.map((s) => [s, s.hidden]));
      for (const s of sidePanels) s.hidden = true;
      if (!commentsSection.childElementCount) {
        commentsSection.append(createComments({ videoId: id, platform }));
      }
      commentsSection.hidden = false;
    }
    const on = hiddenBefore !== null;
    commentsBtn.classList.toggle("primary", on);
    commentsBtn.setAttribute("aria-pressed", on ? "true" : "false");
  }

  // ─── J'aime (bascule, ne touche jamais au lecteur) ───

  let liked = false;
  const likeBtn =
    platform === "youtube"
      ? el("button", {
          class: "btn",
          "aria-pressed": "false",
          onclick: async () => {
            likeBtn.disabled = true;
            try {
              await api.likeVideo(id, liked ? "none" : "like");
              liked = !liked;
              syncLike();
              toast(liked ? "Vidéo aimée 👍" : "J'aime retiré");
            } catch (err) {
              if (err instanceof ApiError && err.status === 409) {
                toast("Compte YouTube non connecté", { error: true });
              } else {
                errorToast(err);
              }
            } finally {
              likeBtn.disabled = false;
            }
          },
        })
      : null;

  function syncLike() {
    likeBtn.textContent = liked ? "👍 Aimé" : "👍 J'aime";
    likeBtn.setAttribute("aria-pressed", liked ? "true" : "false");
    likeBtn.classList.toggle("primary", liked);
  }
  if (likeBtn) {
    syncLike();
    // État initial : échec silencieux, le bouton reste utilisable.
    api
      .videoRating(id)
      .then((out) => {
        liked = !!(out && out.rating === "like");
        syncLike();
      })
      .catch(() => {});
  }

  view.append(
    el(
      "div",
      { class: "watch" },
      el(
        "div",
        {},
        playerBox,
        el(
          "div",
          { class: "watch-info" },
          titleEl,
          el("div", { class: "channel-line" }, channelLink, metaLine),
          el(
            "div",
            { class: "watch-actions" },
            speedSel,
            gainSel,
            qualitySel,
            subSel,
            pipBtn,
            likeBtn,
            commentsBtn,
            detailsBtn,
            plBtn,
          ),
        ),
      ),
      el(
        "aside",
        { class: "side-panel" },
        queueSection,
        chatSection,
        relatedSection,
        commentsSection,
      ),
    ),
  );

  // ─── Overlay (erreur / autoplay bloqué) ───

  function clearOverlay() {
    overlayHost.replaceChildren();
  }

  function showOverlay(message, button) {
    overlayHost.replaceChildren(
      el("div", { class: "player-overlay" }, message ? el("div", { text: message }) : null, button),
    );
  }

  // ─── Lecteur ───

  // La hauteur annoncée par /streams est celle de la piste choisie côté serveur,
  // mais le lecteur peut jouer le MPD (avc1 capé indépendamment) ou adapter la
  // variante HLS en cours de route : videoHeight est la seule vérité, et
  // 'resize' la suit à chaque changement de rendition.
  let streamsInfo = {};
  function renderMeta() {
    const parts = [];
    const height = videoEl.videoHeight || streamsInfo.height;
    if (height) parts.push(`${height}p`);
    if (live) parts.push("EN DIRECT");
    else if (streamsInfo.duration) parts.push(fmtDuration(streamsInfo.duration));
    metaLine.textContent = parts.join(" · ");
  }

  videoEl.addEventListener("loadedmetadata", renderMeta);
  videoEl.addEventListener("resize", renderMeta);

  const player = new Player(videoEl, {
    onMeta: (streams) => {
      if (streams.title) {
        titleEl.textContent = streams.title;
        if (!video.title) video.title = streams.title;
        document.title = `${streams.title} — ytui`;
      }
      streamsInfo = streams;
      renderMeta();
      subSel.replaceChildren(
        el("option", { value: "-1" }, "Sous-titres : aucun"),
        ...(streams.subtitles || []).map((s, i) =>
          el("option", { value: String(i) }, `Sous-titres : ${s.label || s.lang}`),
        ),
      );
    },
    onFatal: (msg) => {
      showOverlay(
        msg,
        el(
          "button",
          {
            class: "btn primary",
            onclick: () => {
              clearOverlay();
              player.load(video).catch((err) => {
                errorToast(err);
                showOverlay(err.detail || "Lecture impossible");
              });
            },
          },
          "Réessayer",
        ),
      );
    },
    onAutoplayBlocked: () => {
      showOverlay(
        null,
        el(
          "button",
          {
            class: "big-play",
            "aria-label": "Lire",
            onclick: () => {
              clearOverlay();
              videoEl.play().catch(() => {});
            },
          },
          "▶",
        ),
      );
    },
    // Déclaration hissée (function declaration) : définie plus bas, avec la file.
    onEnded: () => onEnded(),
    onSubtitleChange: (index) => {
      subSel.value = String(index);
    },
    onRateChange: (r) => {
      speedSel.value = String(r);
    },
  });

  speedSel.addEventListener("change", () => player.setRate(Number(speedSel.value)));
  gainSel.addEventListener("change", () => player.setGain(Number(gainSel.value)));
  qualitySel.onchange = (e) => player.setMaxHeight(Number(e.target.value));
  subSel.addEventListener("change", () => player.setSubtitle(Number(subSel.value)));
  videoEl.addEventListener("play", clearOverlay);

  // ─── File d'attente & « À suivre » ───

  function sideRow(v, { onClick, onRemove } = {}) {
    return el(
      "div",
      { class: "row", tabindex: "0", role: "button", onclick: onClick },
      el(
        "div",
        { class: "row-thumb" },
        v.thumbnail_url ? el("img", { src: v.thumbnail_url, loading: "lazy", alt: "" }) : null,
        v.duration ? el("span", { class: "badge", text: fmtDuration(v.duration) }) : null,
      ),
      el(
        "div",
        { class: "txt" },
        el("div", { class: "title", title: v.title, text: v.title }),
        v.channel_title ? el("div", { class: "sub", text: v.channel_title }) : null,
      ),
      onRemove
        ? el(
            "div",
            { class: "actions" },
            el(
              "button",
              {
                class: "btn icon small",
                title: "Retirer de la file",
                onclick: (e) => {
                  e.stopPropagation();
                  onRemove();
                },
              },
              "✕",
            ),
          )
        : null,
    );
  }

  function renderQueue() {
    queuePanel.replaceChildren();
    const upcoming = queue.upcoming;
    if (!upcoming.length) {
      queuePanel.append(el("div", { class: "sub", text: "File d'attente vide" }));
      return;
    }
    upcoming.forEach((v, i) => {
      queuePanel.append(
        sideRow(v, {
          onClick: () => queue.jumpTo(queue.index + 1 + i), // positionnel
          onRemove: () => queue.removeUpcoming(i),
        }),
      );
    });
  }

  let related = [];
  async function loadRelated() {
    if (platform !== "youtube" || live) return;
    try {
      const out = await api.related(id, platform);
      related = (out.items || []).filter((v) => v.kind === "video");
      relatedPanel.replaceChildren(
        ...related.map((v) =>
          sideRow(v, {
            onClick: () => {
              queue.enqueue(v);
              toast("Ajouté à la file d'attente");
            },
          }),
        ),
      );
      setSideHidden(relatedSection, related.length === 0);
    } catch {
      setSideHidden(relatedSection, true);
    }
  }

  // ─── Chat en direct (lecture seule, YouTube + Twitch) ───
  let chatCursor = 0;
  let chatTimer = null;
  let chatSeen = false;
  const chatWaiting = el("div", { class: "sub", text: "En attente de messages…" });
  const hexColor = (c) => (c && /^#[0-9A-Fa-f]{6}$/.test(c) ? c : "");

  function stopChat() {
    if (chatTimer) {
      clearInterval(chatTimer);
      chatTimer = null;
    }
  }

  async function pollChat() {
    let page;
    try {
      page = await api.liveChat(id, platform, chatCursor);
    } catch {
      return; // tick raté, on réessaie au suivant
    }
    chatCursor = page.cursor || 0;
    const msgs = page.messages || [];
    if (msgs.length) chatWaiting.remove();
    const atBottom =
      chatPanel.scrollHeight - chatPanel.scrollTop - chatPanel.clientHeight < 40;
    for (const m of msgs) {
      chatSeen = true;
      const author = el("span", { class: "chat-author", text: (m.author || "") + "  " });
      const color = hexColor(m.color);
      if (color) author.style.color = color;
      chatPanel.append(el("div", { class: "chat-msg" }, author, el("span", { text: m.text || "" })));
    }
    while (chatPanel.childElementCount > 200) chatPanel.firstElementChild.remove();
    if (atBottom) chatPanel.scrollTop = chatPanel.scrollHeight;
    if (!page.active) {
      stopChat();
      if (!chatSeen) {
        chatWaiting.textContent = "Le direct est terminé.";
        chatPanel.append(chatWaiting);
      }
    }
  }

  function startChat() {
    if (chatTimer) return;
    setSideHidden(chatSection, false);
    setSideHidden(queueSection, true);
    chatPanel.append(chatWaiting);
    chatTimer = setInterval(pollChat, 3000);
    pollChat();
  }

  async function initChat() {
    if (platform !== "youtube" && platform !== "twitch") return;
    if (live) {
      startChat(); // id composite Twitch → direct en cours
      return;
    }
    if (platform === "youtube") {
      try {
        const out = await api.lives();
        if ((out || []).some((it) => it.video && it.video.video_id === id)) startChat();
      } catch {
        /* pas de chat disponible */
      }
    }
  }

  // Fin de lecture : suivant, sinon autoplay de la première suggestion
  // (enqueue + next — sémantique mobile exacte). Appelé une seule fois par
  // média : le lecteur dédoublonne `ended` natif et PLAYBACK_ENDED de dash.js.
  async function onEnded() {
    if (live) return;
    if (queue.hasNext) {
      queue.next();
      return;
    }
    let first = related.length ? related[0] : null;
    if (!first && platform === "youtube") {
      try {
        const out = await api.related(id, platform);
        first = (out.items || []).find((v) => v.kind === "video") || null;
      } catch {
        first = null;
      }
    }
    if (first) {
      queue.enqueue(first);
      queue.next();
    }
  }

  let queueIndex = queue.index;
  const onQueueChange = () => {
    renderQueue();
    const cur = queue.current;
    const moved = queue.index !== queueIndex;
    queueIndex = queue.index;
    if (!cur) return;
    if (cur.video_id !== video.video_id || cur.platform !== video.platform) {
      // Avance de la file : remplace l'entrée d'historique (pas de spam back).
      replace(watchPath(cur));
    } else if (moved) {
      // Même vidéo à une autre place (playlist qui répète un titre) : l'URL ne
      // change pas, donc aucun re-rendu — il faut relancer le média ici, sinon
      // la lecture reste bloquée sur la fin du doublon.
      clearOverlay();
      player.load(video).catch((err) => {
        errorToast(err);
        showOverlay(err.detail || "Lecture impossible");
      });
    }
  };
  queue.addEventListener("change", onQueueChange);

  playerActions.next = () => {
    if (queue.hasNext) queue.next();
    else toast("Fin de la file d'attente");
  };
  playerActions.previous = () => {
    if (queue.hasPrevious) queue.previous();
    else if (!live) videoEl.currentTime = 0;
  };

  renderQueue();
  loadRelated();
  initChat();
  player.load(video).catch((err) => {
    errorToast(err);
    showOverlay(err.detail || "Lecture impossible");
  });

  return () => {
    stopChat();
    queue.removeEventListener("change", onQueueChange);
    player.destroy();
    document.title = "ytui";
  };
}
