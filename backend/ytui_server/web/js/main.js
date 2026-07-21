// Boot: route table, session check, nav highlighting.

import { route, dispatch, navigate, parseHash } from "./router.js";
import { api } from "./api.js";
import { watched } from "./state.js";
import { initShortcuts, showHelp } from "./shortcuts.js";

import * as login from "../views/login.js";
import * as feed from "../views/feed.js";
import * as suggestions from "../views/suggestions.js";
import * as search from "../views/search.js";
import * as lives from "../views/lives.js";
import * as channel from "../views/channel.js";
import * as ytplaylist from "../views/ytplaylist.js";
import * as playlists from "../views/playlists.js";
import * as playlist from "../views/playlist.js";
import * as history from "../views/history.js";
import * as detail from "../views/detail.js";
import * as settings from "../views/settings.js";
import * as watch from "../views/watch.js";

route("/login", login.render);
route("/", feed.render);
route("/suggestions", suggestions.render);
route("/search", search.render);
route("/lives", lives.render);
route("/history", history.render);
route("/playlists", playlists.render);
route("/playlist/:id", playlist.render);
route("/channel/:platform/:id", channel.render);
route("/ytplaylist/:platform/:id", ytplaylist.render);
route("/detail/:platform/:id", detail.render);
route("/watch/:platform/:id", watch.render);
route("/settings", settings.render);

const view = document.getElementById("view");
const appRoot = document.getElementById("app");

function highlightNav() {
  const { path } = parseHash();
  document.querySelectorAll("#sidebar a[data-nav]").forEach((a) => {
    const target = a.dataset.nav;
    a.classList.toggle("active", target === "/" ? path === "/" : path.startsWith(target));
  });
}

async function render() {
  highlightNav();
  await dispatch(view);
}

window.addEventListener("hashchange", render);
document.getElementById("nav-help").addEventListener("click", showHelp);

(async () => {
  let authenticated = false;
  try {
    authenticated = (await api.sessionStatus()).authenticated;
  } catch {
    /* server unreachable: the login page will surface the error */
  }
  appRoot.hidden = false;
  const { path } = parseHash();
  if (!authenticated && path !== "/login") {
    navigate("/login");
  } else if (authenticated && path === "/login") {
    navigate("/");
  }
  if (authenticated) watched.load(); // fire and forget
  initShortcuts();
  await render();
})();
