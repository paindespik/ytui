// Page lecture : lecteur + infos + file d'attente + « À suivre ».

import { api, ApiError } from "../js/api.js";
import {
  el,
  spinner,
  toast,
  errorToast,
  fmtCount,
  fmtDuration,
  watchPath,
  channelPath,
  detailPath,
  publicVideoUrl,
  addToPlaylistModal,
  openModal,
} from "../js/ui.js";
import { queue } from "../js/queue.js";
import { replace, navigate } from "../js/router.js";
import { Player, isLiveId } from "../js/player.js";
import { playerActions } from "../js/shortcuts.js";
import { prefs, QUALITIES, GAINS } from "../js/state.js";
import { createComments } from "../js/comments.js";

// Sleep-timer presets (minutes), mirroring the mobile player menu.
const SLEEP_PRESETS = [15, 30, 45, 60, 90];
const CHAT_POLL_MS = 3000;

export async function render(view, { params }) {
  const { platform, id } = params;
  // Vue démontée : toute continuation asynchrone (chat, « À suivre », overlay)
  // doit s'arrêter là plutôt que d'écrire dans un DOM qui n'est plus affiché —
  // et surtout de réarmer un minuteur.
  let disposed = false;
  // Un direct YouTube garde un id ordinaire : l'état démarre avec l'heuristique
  // d'id composite et bascule dès que /streams (is_live) ou le chat le confirme.
  let isLive = isLiveId(platform, id);

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
  const metaLine = el("span", { class: "sub", text: isLive ? "EN DIRECT" : "" });

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

  // ─── Navigation dans la file (n / p au clavier, boutons ici) ───

  const prevBtn = el(
    "button",
    { class: "btn", title: "Vidéo précédente (p)", onclick: () => playerActions.previous?.() },
    "⏮ Précédent",
  );
  const nextBtn = el(
    "button",
    { class: "btn", title: "Vidéo suivante (n)", onclick: () => playerActions.next?.() },
    "⏭ Suivant",
  );

  function syncNav() {
    prevBtn.disabled = !queue.hasPrevious;
    nextBtn.disabled = !queue.hasNext;
  }

  // ─── Lecture auto / boucle / minuterie / partage (parité mobile) ───

  const autoplayBtn = el("button", {
    class: "btn",
    title: "Enchaîner automatiquement la vidéo suivante (a)",
    "aria-pressed": "false",
    onclick: () => {
      prefs.autoplay = !prefs.autoplay;
      syncAutoplay();
      toast(prefs.autoplay ? "Lecture auto activée" : "Lecture auto désactivée");
    },
  });

  function syncAutoplay() {
    const on = prefs.autoplay;
    autoplayBtn.textContent = on ? "↪ Lecture auto ✓" : "↪ Lecture auto";
    autoplayBtn.setAttribute("aria-pressed", on ? "true" : "false");
    autoplayBtn.classList.toggle("primary", on);
  }

  const loopBtn = el("button", {
    class: "btn",
    title: "Répéter cette vidéo (l)",
    "aria-pressed": "false",
    onclick: () => {
      if (isLive) return;
      player.setLoop(!player.loop);
      syncLoop();
      toast(player.loop ? "Boucle activée" : "Boucle désactivée");
    },
  });

  function syncLoop() {
    const on = Boolean(player && player.loop);
    loopBtn.textContent = on ? "🔂 Boucle ✓" : "🔂 Boucle";
    loopBtn.setAttribute("aria-pressed", on ? "true" : "false");
    loopBtn.classList.toggle("primary", on);
    loopBtn.disabled = isLive;
  }

  // Minuterie de mise en veille : soit une échéance, soit « fin de la vidéo ».
  let sleepTimer = null;
  let sleepTick = null;
  let sleepDeadline = 0;
  let sleepAtEnd = false;

  const sleepBtn = el("button", {
    class: "btn",
    title: "Minuterie de mise en veille",
    onclick: () => sleepMenu(),
  });

  function clearSleep() {
    clearTimeout(sleepTimer);
    clearInterval(sleepTick);
    sleepTimer = null;
    sleepTick = null;
    sleepDeadline = 0;
    sleepAtEnd = false;
    syncSleep();
  }

  function syncSleep() {
    if (sleepAtEnd) sleepBtn.textContent = "⏱ Fin de la vidéo";
    else if (sleepDeadline) {
      const left = Math.max(1, Math.ceil((sleepDeadline - Date.now()) / 60000));
      sleepBtn.textContent = `⏱ ${left} min`;
    } else sleepBtn.textContent = "⏱ Minuterie";
    sleepBtn.classList.toggle("primary", Boolean(sleepDeadline || sleepAtEnd));
  }

  function fireSleep() {
    clearSleep();
    videoEl.pause();
    toast("Minuterie écoulée : lecture en pause");
  }

  function setSleep(minutes) {
    clearSleep();
    sleepDeadline = Date.now() + minutes * 60000;
    sleepTimer = setTimeout(fireSleep, minutes * 60000);
    sleepTick = setInterval(syncSleep, 30000); // libellé = temps restant
    syncSleep();
    toast(`Minuterie : ${minutes} min`);
  }

  function sleepMenu() {
    const buttons = [
      ...SLEEP_PRESETS.map((m) =>
        el(
          "button",
          {
            class: "btn",
            onclick: () => {
              close();
              setSleep(m);
            },
          },
          `${m} min`,
        ),
      ),
      el(
        "button",
        {
          class: "btn",
          onclick: () => {
            close();
            clearSleep();
            sleepAtEnd = true;
            syncSleep();
            toast("Minuterie : fin de la vidéo");
          },
        },
        "Fin de la vidéo",
      ),
      el(
        "button",
        {
          class: "btn",
          onclick: () => {
            close();
            clearSleep();
            toast("Minuterie désactivée");
          },
        },
        "Désactiver",
      ),
    ];
    const { close } = openModal(
      "Minuterie de mise en veille",
      el("div", { class: "list-pick" }, buttons),
    );
  }

  const shareBtn = el(
    "button",
    { class: "btn", title: "Partager le lien public", onclick: () => shareVideo() },
    "Partager",
  );

  async function shareVideo() {
    // Toujours le lien public de la plateforme : l'URL interne ytui n'a aucun
    // sens hors de ce serveur.
    const url = publicVideoUrl(video);
    if (navigator.share) {
      try {
        await navigator.share({ title: video.title || "ytui", url });
        return;
      } catch {
        return; // partage annulé par l'utilisateur
      }
    }
    try {
      await navigator.clipboard.writeText(url);
      toast("Lien copié");
    } catch {
      toast("Copie impossible", { error: true });
    }
  }

  const queuePanel = el("div", { class: "rows" });
  const relatedPanel = el("div", { class: "rows" });
  const relatedSection = el(
    "div",
    { hidden: platform !== "youtube" || isLive },
    el("div", { class: "section-title", text: "À suivre" }),
    relatedPanel,
  );
  const chatLiveNow = (platform === "youtube" || platform === "twitch") && isLive;
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
            prevBtn,
            nextBtn,
            autoplayBtn,
            loopBtn,
            sleepBtn,
            shareBtn,
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

  // Toute erreur de lecture propose la même issue : réessayer. (Avant, seul
  // onFatal offrait le bouton ; un rejet de player.load() laissait un message
  // mort à l'écran.)
  function showError(message) {
    showOverlay(
      message,
      el("button", { class: "btn primary", onclick: () => startPlayback() }, "Réessayer"),
    );
  }

  // Entre la demande et la première image il peut s'écouler plusieurs secondes
  // (résolution yt-dlp + MPD) : l'attente doit se voir.
  function showLoading() {
    overlayHost.replaceChildren(el("div", { class: "player-overlay" }, spinner()));
  }

  function startPlayback() {
    if (disposed) return;
    showLoading();
    player.load(video).catch((err) => {
      if (disposed) return;
      errorToast(err);
      showError(err.detail || "Lecture impossible");
    });
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
    if (isLive) parts.push("EN DIRECT");
    else if (streamsInfo.duration) parts.push(fmtDuration(streamsInfo.duration));
    // /streams carries the view count (same yt-dlp extraction); fall back to
    // whatever the listing already knew (feed items have it since RSS enrichment).
    const views = streamsInfo.view_count ?? video.view_count;
    if (!isLive && views != null) parts.push(`${fmtCount(views)} vues`);
    metaLine.textContent = parts.join(" · ");
  }

  // Bascule définitive en « direct » : badge, pas de suggestions, pas
  // d'enchaînement en fin de flux, pas de boucle.
  function markLive() {
    if (isLive) return;
    isLive = true;
    related = [];
    setSideHidden(relatedSection, true);
    player.setLoop(false);
    syncLoop();
    renderMeta();
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
      if (streams.is_live) markLive();
      renderMeta();
      subSel.replaceChildren(
        el("option", { value: "-1" }, "Sous-titres : aucun"),
        ...(streams.subtitles || []).map((s, i) =>
          el("option", { value: String(i) }, `Sous-titres : ${s.label || s.lang}`),
        ),
      );
    },
    onFatal: (msg) => showError(msg),
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
  videoEl.addEventListener("playing", clearOverlay);

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
    if (platform !== "youtube" || isLive) return;
    relatedPanel.replaceChildren(spinner());
    try {
      const out = await api.related(id, platform);
      if (disposed || isLive) return;
      related = (out.items || []).filter((v) => v.kind === "video");
      if (!related.length) {
        relatedPanel.replaceChildren(el("div", { class: "sub", text: "Aucune suggestion" }));
        return;
      }
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
    } catch {
      if (disposed) return;
      relatedPanel.replaceChildren(el("div", { class: "sub", text: "Suggestions indisponibles" }));
    }
  }

  // ─── Chat en direct (lecture seule, YouTube + Twitch) ───
  let chatCursor = 0;
  let chatTimer = null;
  let chatSeen = false;
  let chatStarted = false;
  let chatStopped = false;
  const chatWaiting = el("div", { class: "sub", text: "En attente de messages…" });
  const hexColor = (c) => (c && /^#[0-9A-Fa-f]{6}$/.test(c) ? c : "");

  function stopChat() {
    chatStopped = true;
    if (chatTimer) {
      clearTimeout(chatTimer);
      chatTimer = null;
    }
  }

  // Sondage non réentrant : le tick suivant n'est armé qu'une fois la réponse
  // traitée (un setInterval de 3 s empilait les requêtes dès que le serveur
  // répondait plus lentement, et les pages arrivaient dans le désordre).
  async function pollChat() {
    chatTimer = null;
    let page = null;
    try {
      page = await api.liveChat(id, platform, chatCursor);
    } catch {
      /* tick raté, on réessaie au suivant */
    }
    if (disposed || chatStopped) return;
    if (!page) {
      chatTimer = setTimeout(pollChat, CHAT_POLL_MS);
      return;
    }
    // Le curseur ne recule jamais : une réponse sans curseur (ou plus ancienne)
    // rejouerait des messages déjà affichés.
    chatCursor = Math.max(chatCursor, Number(page.cursor) || 0);
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
      return;
    }
    chatTimer = setTimeout(pollChat, CHAT_POLL_MS);
  }

  function startChat() {
    if (disposed || chatStarted) return;
    chatStarted = true;
    chatStopped = false;
    markLive(); // un chat actif = un direct, quel que soit le format de l'id
    setSideHidden(chatSection, false);
    setSideHidden(queueSection, true);
    chatPanel.append(chatWaiting);
    pollChat();
  }

  async function initChat() {
    if (platform !== "youtube" && platform !== "twitch") return;
    if (isLive) {
      startChat(); // id composite Twitch → direct en cours
      return;
    }
    if (platform === "youtube") {
      try {
        const out = await api.lives();
        // La vue a pu être démontée pendant l'attente : sans ce garde, le
        // sondage du chat survivait à la navigation.
        if (disposed) return;
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
    if (isLive) return;
    // Minuterie « fin de la vidéo » : on s'arrête là, file ou pas.
    if (sleepAtEnd) {
      clearSleep();
      videoEl.pause();
      toast("Minuterie écoulée : lecture en pause");
      return;
    }
    if (!prefs.autoplay) return; // lecture auto désactivée : la lecture s'arrête
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
    if (disposed || isLive) return;
    if (first) {
      queue.enqueue(first);
      queue.next();
    }
  }

  let queueIndex = queue.index;
  const onQueueChange = () => {
    renderQueue();
    syncNav();
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
      startPlayback();
    }
  };
  queue.addEventListener("change", onQueueChange);

  playerActions.next = () => {
    if (queue.hasNext) queue.next();
    else toast("Fin de la file d'attente");
  };
  playerActions.previous = () => {
    if (queue.hasPrevious) queue.previous();
    else if (!isLive) videoEl.currentTime = 0;
  };
  playerActions.toggleAutoplay = () => {
    prefs.autoplay = !prefs.autoplay;
    syncAutoplay();
    toast(prefs.autoplay ? "Lecture auto activée" : "Lecture auto désactivée");
  };
  playerActions.toggleLoop = () => {
    if (isLive) return;
    player.setLoop(!player.loop);
    syncLoop();
    toast(player.loop ? "Boucle activée" : "Boucle désactivée");
  };

  renderQueue();
  syncNav();
  syncAutoplay();
  syncLoop();
  syncSleep();
  loadRelated();
  initChat();
  startPlayback();

  return () => {
    disposed = true;
    stopChat();
    clearSleep();
    queue.removeEventListener("change", onQueueChange);
    player.destroy();
    document.title = "ytui";
  };
}
