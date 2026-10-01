// Page lecture : lecteur + infos + file d'attente + « À suivre ».

import { api, ApiError } from "../js/api.js";
import {
  el,
  spinner,
  toast,
  errorToast,
  fmtCount,
  fmtDate,
  fmtDuration,
  fmtRelative,
  watchPath,
  channelPath,
  detailPath,
  publicVideoUrl,
  addToPlaylistModal,
  iconBtn,
  setBtnLabel,
  showMenu,
  descriptionNodes,
  followButton,
} from "../js/ui.js";
import { icon } from "../js/icons.js";
import { queue } from "../js/queue.js";
import { replace, navigate } from "../js/router.js";
import { Player, isLiveId } from "../js/player.js";
import { playerActions } from "../js/shortcuts.js";
import { prefs, QUALITIES, GAINS } from "../js/state.js";
import { createComments } from "../js/comments.js";

// Sleep-timer presets (minutes), mirroring the mobile player menu.
const SLEEP_PRESETS = [15, 30, 45, 60, 90];
const CHAT_POLL_MS = 3000;

// Compte à rebours avant la vidéo suivante (lecture auto). Mutable pour les
// tests : 0 enchaîne immédiatement, sans écran d'attente.
export const upNextConfig = { seconds: 5 };

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
  const metaLine = el("span", { class: "sub", text: isLive ? "EN DIRECT" : "" });
  const channelAvatar = el("span", { class: "avatar small", "aria-hidden": "true" });
  function syncChannel() {
    channelAvatar.textContent = (video.channel_title || "")
      .replace(/^[@\s]+/, "")
      .charAt(0)
      .toUpperCase();
    channelAvatar.hidden = !video.channel_title;
    channelLink.textContent = video.channel_title || "";
    if (video.channel_id) {
      channelLink.setAttribute(
        "href",
        "#" + channelPath(video.channel_id, platform, video.channel_title),
      );
    }
  }
  syncChannel();
  const followBtn = followButton(platform, video.channel_id, { compact: true });

  // ─── Description (repliée, horodatages cliquables) ───
  //
  // /api/videos/{id} partage l'extraction de /streams côté serveur (YouTube) :
  // la demander en même temps que la lecture ne coûte pas un second yt-dlp.
  const descMeta = el("div", { class: "desc-meta" });
  const descText = el("div", { class: "desc-text" });
  const descToggle = el("button", {
    class: "desc-toggle",
    type: "button",
    text: "Afficher plus",
    onclick: (e) => {
      e.stopPropagation();
      setDescOpen(!descBox.classList.contains("open"));
    },
  });
  const descBox = el(
    "div",
    {
      class: "desc-box",
      // Clic n'importe où sur la boîte repliée : on déplie (comme YouTube).
      onclick: () => {
        if (!descBox.classList.contains("open")) setDescOpen(true);
      },
    },
    descMeta,
    descText,
    descToggle,
  );
  descBox.hidden = true;

  function setDescOpen(open) {
    descBox.classList.toggle("open", open);
    descToggle.textContent = open ? "Afficher moins" : "Afficher plus";
  }

  async function loadDetails() {
    // Les ids composites (direct Twitch/TikTok) n'ont pas de page de détails.
    if (isLiveId(platform, id) || platform === "tiktok") return;
    let d;
    try {
      d = await api.videoDetails(id, platform);
    } catch {
      return; // non bloquant : la page reste sans description
    }
    if (disposed || !d) return;
    // Lien nu : la chaîne n'était pas connue avant les détails.
    if (!video.channel_id && d.channel_id) video.channel_id = d.channel_id;
    if (!video.channel_title && d.channel_title) video.channel_title = d.channel_title;
    syncChannel();
    followBtn.setChannel(video.channel_id);
    const meta = [
      d.upload_date ? fmtRelative(d.upload_date) : "",
      d.like_count != null ? `${fmtCount(d.like_count)} j'aime` : "",
    ].filter(Boolean);
    descMeta.textContent = meta.join(" · ");
    if (d.upload_date) descMeta.title = fmtDate(d.upload_date);
    const text = (d.description || "").trim();
    if (!text && !meta.length) return;
    descText.replaceChildren(
      ...descriptionNodes(text, {
        onSeek: isLive
          ? null
          : (t) => {
              player.seek(t);
              playerBox.scrollIntoView({ block: "nearest", behavior: "smooth" });
            },
      }),
    );
    // Texte court : rien à déplier.
    const long = text.length > 220 || text.split("\n").length > 3;
    descToggle.hidden = !long;
    if (!long) descBox.classList.add("open");
    descBox.hidden = false;
  }

  // ─── Réglages de lecture (panneau « Lecture ») ───
  //
  // Vitesse, qualité, sous-titres, amplification, lecture auto, boucle et
  // minuterie vivent dans un seul panneau : la barre d'actions garde les
  // gestes fréquents, l'état actif (boucle, minuterie) reste visible en puce.

  const speedSel = el(
    "select",
    { class: "input", "aria-label": "Vitesse de lecture" },
    [0.5, 0.75, 1, 1.25, 1.5, 1.75, 2, 2.25, 2.5, 2.75, 3].map((r) =>
      el(
        "option",
        { value: String(r), selected: r === 1 },
        r === 1 ? "Normale" : `×${r.toLocaleString("fr-FR")}`,
      ),
    ),
  );
  const subSel = el(
    "select",
    { class: "input", "aria-label": "Sous-titres" },
    el("option", { value: "-1" }, "Aucun"),
  );
  const gainSel = el(
    "select",
    { class: "input", "aria-label": "Amplification audio (au-delà de 100 %)" },
    GAINS.map((g) =>
      el(
        "option",
        { value: String(g), selected: g === prefs.gain },
        g === 1 ? "Aucune" : `×${g.toLocaleString("fr-FR")}`,
      ),
    ),
  );
  const qualitySel = el(
    "select",
    { class: "input", "aria-label": "Qualité maximale" },
    QUALITIES.map((q) =>
      el("option", { value: String(q), selected: q === prefs.maxHeight }, `${q}p`),
    ),
  );
  const autoplayInput = el("input", {
    type: "checkbox",
    class: "switch",
    "aria-label": "Lecture auto",
  });
  const loopInput = el("input", { type: "checkbox", class: "switch", "aria-label": "Boucle" });
  const sleepSel = el(
    "select",
    { class: "input", "aria-label": "Minuterie de mise en veille" },
    el("option", { value: "0" }, "Désactivée"),
    SLEEP_PRESETS.map((m) => el("option", { value: String(m) }, `${m} min`)),
    el("option", { value: "end" }, "Fin de la vidéo"),
  );

  const settingRow = (iconName, label, control, hint = "") =>
    el(
      "label",
      { class: "setting-row", title: hint || null },
      icon(iconName),
      el("span", { class: "setting-label", text: label }),
      control,
    );

  const settingsPanel = el(
    "div",
    {
      class: "popover settings-panel",
      role: "dialog",
      "aria-label": "Réglages de lecture",
      // Un clic dedans ne doit pas refermer le panneau (écouteur document).
      onclick: (e) => e.stopPropagation(),
    },
    settingRow("gauge", "Vitesse", speedSel, "[ et ] au clavier"),
    settingRow("monitor", "Qualité max.", qualitySel),
    settingRow("captions", "Sous-titres", subSel, "v au clavier"),
    settingRow("volume-2", "Amplification", gainSel, "Au-delà de 100 % du volume"),
    el("div", { class: "popover-sep" }),
    settingRow("circle-play", "Lecture auto", autoplayInput, "a au clavier"),
    settingRow("repeat-1", "Boucle", loopInput, "l au clavier"),
    settingRow("timer", "Minuterie", sleepSel),
  );
  settingsPanel.hidden = true;

  // compact-1 / compact-2 : libellés masqués quand la colonne rétrécit
  // (container queries, app.css) — l'icône et le title restent.
  const settingsBtn = iconBtn("settings-2", "Lecture", {
    class: "compact-2",
    title: "Réglages de lecture",
    "aria-haspopup": "dialog",
    "aria-expanded": "false",
    onclick: (e) => {
      e.stopPropagation();
      setPanel(settingsPanel.hidden);
    },
  });

  function setPanel(open) {
    settingsPanel.hidden = !open;
    settingsBtn.setAttribute("aria-expanded", open ? "true" : "false");
    settingsBtn.classList.toggle("on", open);
  }
  settingsPanel._close = () => setPanel(false); // Échap (shortcuts.js)
  const closePanel = () => setPanel(false);
  document.addEventListener("click", closePanel);

  // ─── Navigation dans la file (n / p au clavier, boutons ici) ───

  const prevBtn = iconBtn("skip-back", null, {
    title: "Vidéo précédente (p)",
    onclick: () => playerActions.previous?.(),
  });
  const nextBtn = iconBtn("skip-forward", null, {
    title: "Vidéo suivante (n)",
    onclick: () => playerActions.next?.(),
  });

  function syncNav() {
    prevBtn.disabled = !queue.hasPrevious;
    nextBtn.disabled = !queue.hasNext;
  }

  // ─── Lecture auto / boucle / minuterie / partage (parité mobile) ───

  function toggleAutoplay() {
    prefs.autoplay = !prefs.autoplay;
    syncAutoplay();
    toast(prefs.autoplay ? "Lecture auto activée" : "Lecture auto désactivée");
  }

  function syncAutoplay() {
    autoplayInput.checked = prefs.autoplay;
  }

  autoplayInput.addEventListener("change", () => {
    if (autoplayInput.checked !== prefs.autoplay) toggleAutoplay();
  });

  // Puce d'état : la boucle se voit (et se coupe) sans rouvrir le panneau.
  const loopChip = el(
    "button",
    {
      class: "chip state",
      type: "button",
      title: "Boucle active — cliquer pour la couper (l)",
      onclick: () => toggleLoop(),
    },
    icon("repeat-1", { size: 14 }),
    "Boucle",
  );

  function toggleLoop() {
    if (isLive) return;
    player.setLoop(!player.loop);
    syncLoop();
    toast(player.loop ? "Boucle activée" : "Boucle désactivée");
  }

  function syncLoop() {
    const on = Boolean(player && player.loop);
    loopInput.checked = on;
    loopInput.disabled = isLive;
    loopChip.hidden = !on;
  }

  loopInput.addEventListener("change", () => {
    if (loopInput.checked !== Boolean(player && player.loop)) toggleLoop();
    else syncLoop();
  });

  // Minuterie de mise en veille : soit une échéance, soit « fin de la vidéo ».
  let sleepTimer = null;
  let sleepTick = null;
  let sleepDeadline = 0;
  let sleepMinutes = 0;
  let sleepAtEnd = false;

  const sleepChipLabel = el("span");
  const sleepChip = el(
    "button",
    {
      class: "chip state",
      type: "button",
      title: "Minuterie de mise en veille — cliquer pour la modifier",
      onclick: (e) => {
        e.stopPropagation();
        setPanel(true);
        sleepSel.focus();
      },
    },
    icon("timer", { size: 14 }),
    sleepChipLabel,
  );

  function clearSleep() {
    clearTimeout(sleepTimer);
    clearInterval(sleepTick);
    sleepTimer = null;
    sleepTick = null;
    sleepDeadline = 0;
    sleepMinutes = 0;
    sleepAtEnd = false;
    syncSleep();
  }

  function syncSleep() {
    let label = "";
    if (sleepAtEnd) label = "Fin de la vidéo";
    else if (sleepDeadline) {
      label = `${Math.max(1, Math.ceil((sleepDeadline - Date.now()) / 60000))} min`;
    }
    sleepChipLabel.textContent = label;
    sleepChip.hidden = !label;
    sleepSel.value = sleepAtEnd ? "end" : sleepDeadline ? String(sleepMinutes) : "0";
  }

  function fireSleep() {
    clearSleep();
    videoEl.pause();
    toast("Minuterie écoulée : lecture en pause");
  }

  function setSleep(minutes) {
    clearSleep();
    sleepMinutes = minutes;
    sleepDeadline = Date.now() + minutes * 60000;
    sleepTimer = setTimeout(fireSleep, minutes * 60000);
    sleepTick = setInterval(syncSleep, 30000); // libellé = temps restant
    syncSleep();
    toast(`Minuterie : ${minutes} min`);
  }

  sleepSel.addEventListener("change", () => {
    const choice = sleepSel.value;
    if (choice === "end") {
      clearSleep();
      sleepAtEnd = true;
      syncSleep();
      toast("Minuterie : fin de la vidéo");
    } else if (Number(choice) > 0) {
      setSleep(Number(choice));
    } else {
      clearSleep();
      toast("Minuterie désactivée");
    }
  });

  const shareBtn = iconBtn("share-2", "Partager", {
    class: "compact-1",
    title: "Partager le lien public",
    onclick: () => shareVideo(),
  });
  const plBtn = iconBtn("list-plus", "Playlist", {
    class: "compact-1",
    title: "Ajouter à une playlist",
    onclick: () => addToPlaylistModal(video),
  });
  const moreBtn = iconBtn("ellipsis", null, {
    title: "Plus d'actions",
    "aria-haspopup": "menu",
    onclick: (e) => {
      e.stopPropagation();
      setPanel(false);
      showMenu(e, [
        { icon: "info", label: "Détails de la vidéo", onClick: () => navigate(detailPath(video)) },
        video.channel_id
          ? {
              icon: "user",
              label: "Ouvrir la chaîne",
              onClick: () =>
                navigate(channelPath(video.channel_id, platform, video.channel_title)),
            }
          : null,
        document.pictureInPictureEnabled
          ? {
              icon: "picture-in-picture-2",
              label: "Picture-in-picture",
              onClick: () => videoEl.requestPictureInPicture().catch(() => {}),
            }
          : null,
      ]);
    },
  });

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

  // Commentaires : toujours sous la vidéo (comme youtube.com), jamais dans le
  // panneau latéral. Pendant un direct, le chat en tient lieu.
  const commentsSection =
    platform === "youtube" || platform === "odysee"
      ? el("section", { class: "watch-comments", hidden: chatLiveNow })
      : null;

  function setSideHidden(section, hidden) {
    section.hidden = hidden;
    syncSide();
  }

  // Panneau latéral sans rien à montrer (file vide, pas de suggestions ni de
  // chat) : le lecteur récupère toute la largeur au lieu d'une colonne vide.
  let watchRoot = null;
  function syncSide() {
    if (!watchRoot) return;
    const empty = [queueSection, chatSection, relatedSection].every((s) => s.hidden);
    watchRoot.classList.toggle("no-side", empty);
  }

  // Chargement paresseux : l'API YouTube coûte du quota, on ne demande les
  // commentaires que quand la section approche du viewport.
  let commentsObserver = null;
  function loadComments() {
    if (disposed || !commentsSection || commentsSection.childElementCount) return;
    commentsSection.append(createComments({ videoId: id, platform }));
  }

  // ─── J'aime (bascule, ne touche jamais au lecteur) ───

  let liked = false;
  const likeBtn =
    platform === "youtube"
      ? iconBtn("thumbs-up", "J'aime", {
          class: "compact-2",
          title: "J'aime",
          "aria-pressed": "false",
          onclick: async () => {
            likeBtn.disabled = true;
            try {
              await api.likeVideo(id, liked ? "none" : "like");
              liked = !liked;
              syncLike();
              toast(liked ? "Vidéo aimée" : "J'aime retiré");
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
    setBtnLabel(likeBtn, liked ? "Aimé" : "J'aime");
    likeBtn.setAttribute("aria-pressed", liked ? "true" : "false");
    likeBtn.classList.toggle("on", liked);
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

  // ─── Mode cinéma (t) ───

  const cinemaBtn = iconBtn("rectangle-horizontal", null, {
    class: "cinema-btn",
    title: "Mode cinéma (t)",
    "aria-pressed": prefs.cinema ? "true" : "false",
    onclick: () => toggleCinema(),
  });
  cinemaBtn.classList.toggle("on", prefs.cinema);

  function toggleCinema() {
    prefs.cinema = !prefs.cinema;
    watchRoot.classList.toggle("cinema", prefs.cinema);
    cinemaBtn.classList.toggle("on", prefs.cinema);
    cinemaBtn.setAttribute("aria-pressed", prefs.cinema ? "true" : "false");
  }

  watchRoot = el(
    "div",
    { class: prefs.cinema ? "watch cinema" : "watch" },
    el(
      "div",
      // display: contents — lecteur et infos sont placés par la grille de
      // .watch (le mode cinéma met le lecteur seul sur toute la largeur).
      { class: "watch-main" },
      playerBox,
      el(
        "div",
        { class: "watch-info" },
        titleEl,
        el(
          "div",
          { class: "channel-line" },
          channelAvatar,
          channelLink,
          followBtn,
          el("span", { class: "grow" }),
          metaLine,
        ),
        el(
          "div",
          { class: "watch-actions" },
          el("div", { class: "btn-group" }, prevBtn, nextBtn),
          likeBtn,
          shareBtn,
          plBtn,
          // Groupe de droite : puces d'état + réglages + « plus », qui passent
          // ensemble à la ligne sur une colonne étroite.
          el(
            "div",
            { class: "actions-end" },
            loopChip,
            sleepChip,
            cinemaBtn,
            el("div", { class: "menu-anchor" }, settingsBtn, settingsPanel),
            moreBtn,
          ),
        ),
        descBox,
      ),
    ),
    commentsSection,
    el(
      "aside",
      { class: "side-panel" },
      queueSection,
      chatSection,
      relatedSection,
    ),
  );
  view.append(watchRoot);

  // Déclenchement à 600 px du viewport. Sans IntersectionObserver (harnais de
  // tests Node), chargement immédiat.
  if (commentsSection) {
    if (typeof IntersectionObserver === "function") {
      commentsObserver = new IntersectionObserver(
        (entries, observer) => {
          if (!entries.some((e) => e.isIntersecting)) return;
          // L'observateur reçu en argument, pas la variable (déjà null si un
          // lot d'entrées arrive après la coupure) ; loadComments est idempotent.
          observer.disconnect();
          commentsObserver = null;
          loadComments();
        },
        { rootMargin: "0px 0px 600px 0px" },
      );
      commentsObserver.observe(commentsSection);
    } else {
      loadComments();
    }
  }

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
        el("option", { value: "-1" }, "Aucun"),
        ...(streams.subtitles || []).map((s, i) =>
          el("option", { value: String(i) }, s.label || s.lang),
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
          icon("play", { size: 34 }),
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
  // Relancer la vidéo terminée (bouton natif) annule le compte à rebours.
  videoEl.addEventListener("play", () => {
    cancelUpNext();
    clearOverlay();
  });
  videoEl.addEventListener("playing", clearOverlay);

  // ─── File d'attente & « À suivre » ───

  function sideRow(v, { onClick, onRemove, onAdd } = {}) {
    const row = el(
      "div",
      {
        class: "row",
        tabindex: "0",
        role: "button",
        onclick: onClick,
        onkeydown: (e) => {
          if (e.key === "Enter") onClick?.();
        },
      },
      el(
        "div",
        { class: "row-thumb" },
        v.thumbnail_url ? el("img", { src: v.thumbnail_url, loading: "lazy", alt: "" }) : null,
        v.duration ? el("span", { class: "badge", text: fmtDuration(v.duration) }) : null,
      ),
      el(
        "div",
        { class: "txt" },
        el("div", { class: "title clamp", title: v.title, text: v.title }),
        v.channel_title ? el("div", { class: "sub", text: v.channel_title }) : null,
      ),
      onRemove || onAdd
        ? el(
            "div",
            { class: "actions" },
            onAdd
              ? iconBtn("list-plus", null, {
                  class: "small",
                  title: "Ajouter à la file d'attente",
                  onclick: (e) => {
                    e.stopPropagation();
                    onAdd();
                  },
                })
              : null,
            onRemove
              ? iconBtn("x", null, {
                  class: "small",
                  title: "Retirer de la file",
                  onclick: (e) => {
                    e.stopPropagation();
                    onRemove();
                  },
                })
              : null,
          )
        : null,
    );
    row._video = v; // raccourci q (file d'attente)
    return row;
  }

  function renderQueue() {
    queuePanel.replaceChildren();
    const upcoming = queue.upcoming;
    // File vide : pas de section « vide » en tête du panneau, « À suivre »
    // remonte. Pendant un direct, la section cède la place au chat.
    setSideHidden(queueSection, chatStarted || !upcoming.length);
    if (!upcoming.length) return;
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
            // Clic : lecture immédiate, le reste de la file reste à venir.
            onClick: () => queue.playNow(v),
            onAdd: () => {
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
    if (commentsSection) commentsSection.hidden = true; // le chat tient lieu de commentaires
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
      upNext(queue.items[queue.index + 1], () => {
        if (queue.hasNext) queue.next();
      });
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
    if (disposed || isLive || !first) return;
    upNext(first, () => queue.playNow(first));
  }

  // ─── « Suivant dans N s » ───

  let upNextTimer = null;
  let upNextTick = null;

  function cancelUpNext() {
    clearTimeout(upNextTimer);
    clearInterval(upNextTick);
    const was = upNextTimer !== null;
    upNextTimer = null;
    upNextTick = null;
    return was;
  }

  function upNext(target, go) {
    cancelUpNext();
    const seconds = upNextConfig.seconds;
    if (!(seconds > 0)) {
      go();
      return;
    }
    let left = Math.ceil(seconds);
    const count = el("b", { text: String(left) });
    const run = () => {
      if (disposed) return;
      cancelUpNext();
      clearOverlay();
      go();
    };
    const bar = el("div", { class: "upnext-bar" });
    bar.style.animationDuration = `${seconds}s`;
    overlayHost.replaceChildren(
      el(
        "div",
        { class: "player-overlay upnext" },
        el("div", { class: "upnext-label" }, "Suivant dans ", count, " s"),
        el(
          "div",
          { class: "upnext-card" },
          target.thumbnail_url
            ? el("img", { src: target.thumbnail_url, alt: "", class: "upnext-thumb" })
            : null,
          el(
            "div",
            { class: "upnext-txt" },
            el("div", { class: "upnext-title", text: target.title || "Vidéo suivante" }),
            target.channel_title ? el("div", { class: "sub", text: target.channel_title }) : null,
          ),
        ),
        el("div", { class: "upnext-track" }, bar),
        el(
          "div",
          { class: "upnext-actions" },
          el("button", { class: "btn", type: "button", text: "Annuler", onclick: () => {
            cancelUpNext();
            clearOverlay();
          } }),
          el("button", { class: "btn primary", type: "button", text: "Lire maintenant", onclick: run }),
        ),
      ),
    );
    upNextTick = setInterval(() => {
      left = Math.max(0, left - 1);
      count.textContent = String(left);
    }, 1000);
    upNextTimer = setTimeout(run, seconds * 1000);
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
  playerActions.toggleAutoplay = toggleAutoplay;
  playerActions.toggleLoop = toggleLoop;
  playerActions.toggleCinema = toggleCinema;

  renderQueue();
  syncNav();
  syncAutoplay();
  syncLoop();
  syncSleep();
  loadRelated();
  initChat();
  startPlayback();
  loadDetails();

  return () => {
    disposed = true;
    commentsObserver?.disconnect();
    cancelUpNext();
    document.removeEventListener("click", closePanel);
    stopChat();
    clearSleep();
    queue.removeEventListener("change", onQueueChange);
    player.destroy();
    document.title = "ytui";
  };
}
