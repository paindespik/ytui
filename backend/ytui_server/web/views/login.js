// Page de connexion : saisie du jeton API → cookie de session.

import { api, ApiError } from "../js/api.js";
import { el } from "../js/ui.js";
import { navigate } from "../js/router.js";
import { watched } from "../js/state.js";

export async function render(view) {
  const error = el("div", { class: "notice", hidden: true });
  const input = el("input", {
    class: "input",
    type: "password",
    placeholder: "Jeton API",
    autocomplete: "current-password",
    required: true,
  });
  const submit = el("button", { class: "btn primary", type: "submit" }, "Se connecter");

  const form = el(
    "form",
    {
      onsubmit: async (e) => {
        e.preventDefault();
        error.hidden = true;
        submit.disabled = true;
        try {
          await api.login(input.value.trim());
          watched.load();
          navigate("/");
        } catch (err) {
          error.textContent =
            err instanceof ApiError && err.status === 401
              ? "Jeton invalide"
              : `Connexion impossible : ${err.detail || err.message}`;
          error.hidden = false;
          submit.disabled = false;
        }
      },
    },
    input,
    submit,
  );
  form.style.display = "flex";
  form.style.flexDirection = "column";
  form.style.gap = "12px";

  view.append(
    el(
      "div",
      { class: "login-wrap" },
      el(
        "div",
        { class: "login-box" },
        el("h1", {}, el("img", { src: "icon.svg", width: "30", height: "30", alt: "" }), "ytui"),
        el("p", {
          class: "sub",
          text: "Entrez le jeton API du serveur pour ouvrir une session.",
        }),
        error,
        form,
      ),
    ),
  );
  input.focus();
}
