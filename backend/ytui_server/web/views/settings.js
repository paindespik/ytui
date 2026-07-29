// Réglages : chaînes suivies, préférences de lecture, statut serveur, déconnexion.

import { api, ApiError } from "../js/api.js";
import { navigate } from "../js/router.js";
import { prefs, QUALITIES } from "../js/state.js";
import {
  el,
  channelPath,
  spinner,
  emptyState,
  errorToast,
  toast,
  confirmModal,
} from "../js/ui.js";

function fmtUptime(seconds) {
  const s = Math.max(0, Math.floor(seconds || 0));
  const days = Math.floor(s / 86400);
  const hours = Math.floor((s % 86400) / 3600);
  const mins = Math.floor((s % 3600) / 60);
  if (days > 0) return `${days} j ${hours} h`;
  if (hours > 0) return `${hours} h ${mins} min`;
  return `${mins} min`;
}

export async function render(view) {
  view.append(el("div", { class: "page-head" }, el("h1", { text: "Réglages" })));

  // ─── Chaînes suivies ───
  view.append(el("div", { class: "section-title", text: "Chaînes suivies" }));
  const channelList = el("div", { class: "rows" });
  view.append(channelList);

  async function loadChannels() {
    channelList.replaceChildren(spinner());
    let channels;
    try {
      channels = await api.channels();
    } catch (err) {
      errorToast(err);
      channelList.replaceChildren(emptyState("Impossible de charger les chaînes"));
      return;
    }
    if (!channels.length) {
      channelList.replaceChildren(
        el("div", { class: "sub", text: "Aucune chaîne suivie pour l'instant." }),
      );
      return;
    }
    channelList.replaceChildren(
      ...channels.map((c) => {
        const open = () => navigate(channelPath(c.channel_id, c.platform, c.title));
        return el(
          "div",
          {
            class: "row",
            tabindex: "0",
            role: "button",
            title: "Voir les vidéos de la chaîne",
            onclick: open,
            onkeydown: (e) => {
              if (e.key === "Enter") open();
            },
          },
          el(
            "div",
            { class: "txt" },
            el("div", { class: "title", text: c.title || c.channel_id }),
            el("div", { class: "sub", text: c.platform }),
          ),
          el(
            "div",
            { class: "actions" },
            el("button", {
              class: "btn icon danger",
              title: "Ne plus suivre",
              text: "✕",
              onclick: async (e) => {
                e.stopPropagation();
                const ok = await confirmModal(
                  `Ne plus suivre « ${c.title || c.channel_id} » ?`,
                  { confirmLabel: "Ne plus suivre" },
                );
                if (!ok) return;
                try {
                  await api.unfollowChannel(c.channel_id);
                  toast("Chaîne retirée");
                  await loadChannels();
                } catch (err) {
                  errorToast(err);
                }
              },
            }),
          ),
        );
      }),
    );
  }

  const refInput = el("input", {
    class: "input",
    type: "text",
    placeholder: "Référence de chaîne…",
  });
  view.append(
    el(
      "form",
      {
        class: "form-line",
        onsubmit: async (e) => {
          e.preventDefault();
          const ref = refInput.value.trim();
          if (!ref) {
            toast("Référence vide", { error: true });
            return;
          }
          try {
            await api.followChannel(ref);
            refInput.value = "";
            toast("Chaîne ajoutée");
            await loadChannels();
          } catch (err) {
            if (err instanceof ApiError && err.status === 404) {
              toast("Chaîne introuvable", { error: true });
            } else if (err instanceof ApiError && err.status === 409) {
              toast("Déjà suivie", { error: true });
            } else if (err instanceof ApiError && err.status === 422) {
              toast("Référence vide", { error: true });
            } else if (err instanceof ApiError && err.status === 502) {
              toast("Erreur de la plateforme", { error: true });
            } else {
              errorToast(err);
            }
          }
        },
      },
      refInput,
      el("button", { class: "btn primary", type: "submit", text: "Ajouter" }),
    ),
    el("div", {
      class: "field-help",
      text: "Formats acceptés : UC…, @handle, bitchute:slug, odysee:@nom, twitch:login, tiktok:user",
    }),
  );

  // ─── Lecture ───
  view.append(el("div", { class: "section-title", text: "Lecture" }));

  const sbToggle = el("input", {
    type: "checkbox",
    checked: prefs.sponsorblock,
    onchange: (e) => {
      prefs.sponsorblock = e.target.checked;
    },
  });
  const qualitySelect = el(
    "select",
    {
      class: "input",
      onchange: (e) => {
        prefs.maxHeight = Number(e.target.value);
      },
    },
    QUALITIES.map((q) =>
      el("option", { value: q, selected: q === prefs.maxHeight, text: `${q}p` }),
    ),
  );
  const langsInput = el("input", {
    class: "input",
    type: "text",
    value: prefs.subLangs,
    placeholder: "fr,en",
    onchange: (e) => {
      prefs.subLangs = e.target.value.trim();
    },
  });
  view.append(
    el(
      "div",
      { class: "pref-rows" },
      el(
        "label",
        { class: "pref-row" },
        el("span", { text: "Sauter les segments sponsorisés (SponsorBlock)" }),
        sbToggle,
      ),
      el(
        "label",
        { class: "pref-row" },
        el("span", { text: "Qualité maximale" }),
        qualitySelect,
      ),
      el(
        "label",
        { class: "pref-row" },
        el("span", { text: "Langues des sous-titres" }),
        langsInput,
      ),
    ),
  );

  // ─── Serveur ───
  view.append(el("div", { class: "section-title", text: "Serveur" }));
  const serverBox = el("div", { class: "kv-rows" }, spinner());
  view.append(serverBox);
  api
    .status()
    .then((s) => {
      const rows = [
        ["Version", s.version],
        ["Uptime", fmtUptime(s.uptime_seconds)],
        ["Base de données", `${(s.db_bytes / (1024 * 1024)).toFixed(1)} Mo`],
        ["Lignes d'historique", String(s.history_rows)],
        ["Chaînes suivies", String(s.channels)],
        ["Lives actifs", String(s.lives_active)],
      ];
      serverBox.replaceChildren(
        ...rows.map(([k, v]) =>
          el(
            "div",
            { class: "kv" },
            el("span", { class: "kv-key", text: k }),
            el("span", { class: "kv-val", text: v }),
          ),
        ),
      );
    })
    .catch(() => {
      serverBox.replaceChildren(el("div", { class: "sub", text: "Statut serveur indisponible" }));
    });

  // ─── Déconnexion ───
  view.append(
    el(
      "div",
      { class: "settings-footer" },
      el("button", {
        class: "btn danger",
        text: "Se déconnecter",
        onclick: async () => {
          try {
            await api.logout();
          } catch {
            /* le cookie est peut-être déjà invalide : on repart au login */
          }
          navigate("/login");
        },
      }),
    ),
  );

  await loadChannels();
}
