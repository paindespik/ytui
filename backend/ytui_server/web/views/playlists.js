// Playlists locales : liste + création / renommage / suppression.

import { api, ApiError } from "../js/api.js";
import { navigate } from "../js/router.js";
import {
  el,
  spinner,
  emptyState,
  errorToast,
  toast,
  confirmModal,
  promptModal,
  importPlaylistModal,
  fmtDate,
} from "../js/ui.js";

export async function render(view) {
  const newBtn = el("button", {
    class: "btn primary",
    text: "Nouvelle playlist",
    onclick: async () => {
      const name = await promptModal("Nouvelle playlist", {
        placeholder: "Nom de la playlist",
        submitLabel: "Créer",
      });
      if (!name || !name.trim()) return;
      try {
        await api.createPlaylist(name.trim());
        toast("Playlist créée");
        await load();
      } catch (err) {
        if (err instanceof ApiError && err.status === 409) {
          toast("Nom déjà pris", { error: true });
        } else {
          errorToast(err);
        }
      }
    },
  });
  const importBtn = el("button", {
    class: "btn",
    text: "Importer depuis YouTube",
    onclick: async () => {
      const source = await promptModal("Importer une playlist YouTube", {
        placeholder: "URL ou identifiant de la playlist",
        submitLabel: "Continuer",
      });
      if (!source) return;
      const playlist = await importPlaylistModal({ source });
      if (playlist) await load();
    },
  });
  const body = el("div");
  view.append(
    el("div", { class: "page-head" }, el("h1", { text: "Playlists" }), newBtn, importBtn),
    body,
  );

  async function load() {
    body.replaceChildren(spinner());
    let playlists;
    try {
      playlists = await api.playlists();
    } catch (err) {
      errorToast(err);
      body.replaceChildren(emptyState("Impossible de charger les playlists"));
      return;
    }
    if (!playlists.length) {
      body.replaceChildren(emptyState("Aucune playlist — créez-en une."));
      return;
    }
    body.replaceChildren(
      el(
        "div",
        { class: "rows" },
        playlists.map((p) => playlistRow(p, load)),
      ),
    );
  }

  await load();
}

function playlistRow(p, reload) {
  const count = p.count === 1 ? "1 vidéo" : `${p.count} vidéos`;
  const row = el(
    "div",
    {
      class: "row",
      tabindex: "0",
      role: "button",
      onclick: () => navigate(`/playlist/${p.id}?name=${encodeURIComponent(p.name)}`),
      onkeydown: (e) => {
        if (e.key === "Enter") navigate(`/playlist/${p.id}?name=${encodeURIComponent(p.name)}`);
      },
    },
    el("div", { class: "row-thumb playlist-thumb" }, el("span", { text: "≡" })),
    el(
      "div",
      { class: "txt" },
      el("div", { class: "title", text: p.name }),
      el("div", { class: "sub", text: `${count} · ${fmtDate(new Date(p.created_at * 1000))}` }),
    ),
    el(
      "div",
      { class: "actions" },
      el("button", {
        class: "btn small",
        text: "Renommer",
        onclick: async (e) => {
          e.stopPropagation();
          const name = await promptModal("Renommer la playlist", {
            value: p.name,
            submitLabel: "Renommer",
          });
          if (!name || !name.trim() || name.trim() === p.name) return;
          try {
            await api.renamePlaylist(p.id, name.trim());
            toast("Playlist renommée");
            await reload();
          } catch (err) {
            if (err instanceof ApiError && err.status === 409) {
              toast("Nom déjà pris", { error: true });
            } else {
              errorToast(err);
            }
          }
        },
      }),
      el("button", {
        class: "btn small danger",
        text: "Supprimer",
        onclick: async (e) => {
          e.stopPropagation();
          const ok = await confirmModal(`Supprimer la playlist « ${p.name} » ?`);
          if (!ok) return;
          try {
            await api.deletePlaylist(p.id);
            toast("Playlist supprimée");
            await reload();
          } catch (err) {
            errorToast(err);
          }
        },
      }),
    ),
  );
  return row;
}
