// Réglages : chaînes suivies, préférences de lecture, statut serveur, déconnexion.

import { api, ApiError } from "../js/api.js";
import { navigate } from "../js/router.js";
import { prefs, QUALITIES, GAINS } from "../js/state.js";
import {
  el,
  channelPath,
  spinner,
  emptyState,
  errorToast,
  toast,
  confirmModal,
  iconBtn,
  skeletonRows,
  PLATFORM_LABELS,
} from "../js/ui.js";
import { icon } from "../js/icons.js";

// `commande` → <code> (les aides citent des commandes du client bureau).
function withCode(text) {
  return String(text)
    .split(/`([^`]+)`/g)
    .map((part, i) => (i % 2 ? el("code", { text: part }) : part));
}

// Initiale d'une chaîne pour sa pastille (pas d'avatar distant à charger).
function initial(name) {
  const letter = String(name || "?").replace(/^[@\s]+/, "").charAt(0);
  return (letter || "?").toUpperCase();
}

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
    channelList.replaceChildren(skeletonRows(3));
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
          el("div", { class: "avatar", "aria-hidden": "true", text: initial(c.title || c.channel_id) }),
          el(
            "div",
            { class: "txt" },
            el("div", { class: "title", text: c.title || c.channel_id }),
            el(
              "div",
              { class: "sub" },
              el("span", {
                class: `chip platform ${c.platform}`,
                text: PLATFORM_LABELS[c.platform] || "YouTube",
              }),
            ),
          ),
          el(
            "div",
            { class: "actions" },
            iconBtn("x", null, {
              class: "danger",
              title: "Ne plus suivre",
              onclick: async (e) => {
                e.stopPropagation();
                const ok = await confirmModal(
                  `Ne plus suivre « ${c.title || c.channel_id} » ?`,
                  { confirmLabel: "Ne plus suivre" },
                );
                if (!ok) return;
                try {
                  await api.unfollowChannel(c.channel_id, c.platform);
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
    "aria-label": "Référence de chaîne à suivre",
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
      el("button", { class: "btn primary", type: "submit" }, icon("plus"), el("span", { text: "Ajouter" })),
    ),
    el("div", {
      class: "field-help",
      text:
        "Formats acceptés : URL de chaîne, UC…, @handle, bitchute:slug, odysee:@nom, " +
        "twitch:login, tiktok:user, crowdbunker:nom",
    }),
  );

  // ─── Lecture ───
  view.append(el("div", { class: "section-title", text: "Lecture" }));

  // Interrupteur lié à une préférence booléenne.
  const toggle = (key) =>
    el("input", {
      type: "checkbox",
      class: "switch",
      checked: prefs[key],
      onchange: (e) => {
        prefs[key] = e.target.checked;
      },
    });
  const sbToggle = toggle("sponsorblock");
  const gainSelect = el(
    "select",
    {
      class: "input",
      onchange: (e) => {
        prefs.gain = Number(e.target.value);
      },
    },
    GAINS.map((g) =>
      el("option", {
        value: g,
        selected: g === prefs.gain,
        text: g === 1 ? "Aucune" : `×${g.toLocaleString("fr-FR")}`,
      }),
    ),
  );
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
    "aria-label": "Langues des sous-titres",
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
      el(
        "label",
        { class: "pref-row" },
        el("span", { text: "Lecture auto (enchaîner la vidéo suivante)" }),
        toggle("autoplay"),
      ),
      el(
        "label",
        { class: "pref-row" },
        el("span", { text: "Amplification audio (au-delà de 100 %)" }),
        gainSelect,
      ),
      el(
        "label",
        { class: "pref-row" },
        el("span", { text: "Mode cinéma par défaut" }),
        toggle("cinema"),
      ),
    ),
  );

  // ─── Compte YouTube (poussé depuis le client bureau) ───
  view.append(el("div", { class: "section-title", text: "Compte YouTube" }));
  const accountBox = el("div", { class: "pref-rows" }, skeletonRows(2));
  view.append(accountBox);

  const statusRow = (iconName, label, ok, okText, koText, hint, action = null) =>
    el(
      "div",
      { class: "pref-row account-row" },
      icon(iconName),
      el(
        "div",
        { class: "txt" },
        el("div", { text: label }),
        el("div", { class: "field-help" }, withCode(hint)),
      ),
      el("span", { class: ok ? "chip status ok" : "chip status", text: ok ? okText : koText }),
      action,
    );

  async function loadAccount() {
    let st;
    try {
      st = await api.youtubeAuthStatus();
    } catch {
      accountBox.replaceChildren(el("div", { class: "sub", text: "État du compte indisponible" }));
      return;
    }
    const dropCookies = st.cookies
      ? iconBtn("trash-2", null, {
          class: "danger small",
          title: "Supprimer les cookies du serveur",
          onclick: async () => {
            const ok = await confirmModal(
              "Supprimer les cookies YouTube du serveur ? Les suggestions et la musique " +
                "redeviendront anonymes jusqu'au prochain « ytui auth cookies ».",
            );
            if (!ok) return;
            try {
              await api.deleteYoutubeCookies();
              toast("Cookies supprimés");
            } catch (err) {
              errorToast(err);
            }
            loadAccount();
          },
        })
      : null;
    accountBox.replaceChildren(
      statusRow(
        "key-round",
        "Jeton OAuth",
        st.authenticated,
        "Connecté",
        "Absent",
        st.authenticated
          ? "J'aime et commentaires actifs."
          : "Pour aimer et commenter : `ytui auth push` depuis le client bureau.",
      ),
      statusRow(
        "cookie",
        "Cookies du compte",
        st.cookies,
        "Présents",
        "Absents",
        st.cookies
          ? "Accueil personnalisé (Suggestions, Musique)."
          : "Pour des suggestions personnalisées : `ytui auth cookies`.",
        dropCookies,
      ),
    );
  }
  loadAccount();

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
      iconBtn("log-out", "Se déconnecter", {
        class: "danger",
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
