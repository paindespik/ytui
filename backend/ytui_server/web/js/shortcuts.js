// Global keyboard shortcuts (desktop-first, TUI-inspired).
// Player pages register their handlers in `playerActions`; list pages get
// j/k/Entrée/q navigation over .card/.row elements automatically.

import { navigate, dispatch, parseHash } from "./router.js";
import { el, openModal, anyModalOpen, toast } from "./ui.js";
import { queue } from "./queue.js";

// player.js assigns: toggle, seekBy, speedBy, cycleSubtitles, fullscreen,
// mute, next, previous. Cleared on watch page unmount.
export const playerActions = {};

let focusIndex = -1;

function focusables() {
  return [...document.querySelectorAll("#view .card, #view .row")];
}

function moveFocus(delta) {
  const items = focusables();
  if (!items.length) return;
  focusIndex = Math.min(Math.max(focusIndex + delta, 0), items.length - 1);
  const target = items[focusIndex];
  items.forEach((n) => n.classList.remove("focused"));
  target.classList.add("focused");
  target.focus({ preventScroll: true });
  target.scrollIntoView({ block: "nearest" });
}

function focusedVideo() {
  const items = focusables();
  const node = items[focusIndex];
  return node && node._video ? node._video : null;
}

function isTyping(e) {
  const t = e.target;
  return (
    t instanceof HTMLInputElement ||
    t instanceof HTMLTextAreaElement ||
    t instanceof HTMLSelectElement ||
    (t instanceof HTMLElement && t.isContentEditable)
  );
}

export function showHelp() {
  const rows = [
    ["/", "Rechercher"],
    ["j / k", "Carte suivante / précédente"],
    ["Entrée", "Ouvrir l'élément sélectionné"],
    ["q", "Ajouter l'élément à la file d'attente"],
    ["r", "Actualiser la vue"],
    ["Espace", "Lecture / pause"],
    ["← / →", "Reculer / avancer de 5 s"],
    ["[ / ]", "Vitesse −0,25 / +0,25"],
    ["v", "Sous-titres suivants"],
    ["m", "Muet"],
    ["f", "Plein écran"],
    ["n / p", "Vidéo suivante / précédente de la file"],
    ["Échap", "Fermer / retour"],
    ["?", "Cette aide"],
  ];
  openModal(
    "Raccourcis clavier",
    el(
      "table",
      { class: "kbd-table" },
      rows.map(([k, label]) =>
        el("tr", {}, el("td", {}, el("kbd", { text: k })), el("td", { text: label })),
      ),
    ),
  );
}

export function initShortcuts() {
  window.addEventListener("hashchange", () => {
    focusIndex = -1;
  });

  document.addEventListener("keydown", (e) => {
    if (isTyping(e)) {
      if (e.key === "Escape") e.target.blur();
      return;
    }
    if (anyModalOpen()) return; // the modal handles Escape itself
    if (e.ctrlKey || e.metaKey || e.altKey) return;

    switch (e.key) {
      case "/":
        e.preventDefault();
        if (parseHash().path !== "/search") navigate("/search");
        setTimeout(() => document.getElementById("search-input")?.focus(), 60);
        return;
      case "?":
        e.preventDefault();
        showHelp();
        return;
      case "Escape":
        history.back();
        return;
      case "j":
        e.preventDefault();
        moveFocus(1);
        return;
      case "k":
        e.preventDefault();
        moveFocus(-1);
        return;
      case "q": {
        const video = focusedVideo();
        if (video && video.kind === "video") {
          queue.enqueue(video);
          toast("Ajouté à la file d'attente");
        }
        return;
      }
      case "r":
        dispatch(document.getElementById("view"));
        return;
    }

    // Player-only bindings.
    switch (e.key) {
      case " ":
        if (playerActions.toggle) {
          e.preventDefault();
          playerActions.toggle();
        }
        return;
      case "ArrowLeft":
        if (playerActions.seekBy) {
          e.preventDefault();
          playerActions.seekBy(-5);
        }
        return;
      case "ArrowRight":
        if (playerActions.seekBy) {
          e.preventDefault();
          playerActions.seekBy(5);
        }
        return;
      case "[":
        playerActions.speedBy?.(-0.25);
        return;
      case "]":
        playerActions.speedBy?.(0.25);
        return;
      case "v":
        playerActions.cycleSubtitles?.();
        return;
      case "m":
        playerActions.mute?.();
        return;
      case "f":
        playerActions.fullscreen?.();
        return;
      case "n":
        playerActions.next?.();
        return;
      case "p":
        playerActions.previous?.();
        return;
    }
  });
}
