// Minimal hash router: register "/pattern/:param" handlers, dispatch on hashchange.
// A handler may return a cleanup function, called before the next route renders.

const routes = [];
let currentCleanup = null;
// Numéro de la navigation en cours. Un rendu lent (une vue qui attend le
// réseau) peut se terminer après qu'une autre route a pris la main : sans ce
// compteur, son nettoyage écrasait celui de la vue réellement affichée, et le
// lecteur de la vue abandonnée continuait de jouer en fond — en réécrivant sa
// position périmée par-dessus la progression réelle.
let generation = 0;

export function route(pattern, handler) {
  const names = [];
  const regex = new RegExp(
    "^" +
      pattern.replace(/:[^/]+/g, (m) => {
        names.push(m.slice(1));
        return "([^/]+)";
      }) +
      "$",
  );
  routes.push({ regex, names, handler });
}

export function parseHash() {
  const raw = location.hash.startsWith("#") ? location.hash.slice(1) : location.hash;
  const qIndex = raw.indexOf("?");
  const path = (qIndex === -1 ? raw : raw.slice(0, qIndex)) || "/";
  const query = new URLSearchParams(qIndex === -1 ? "" : raw.slice(qIndex + 1));
  return { path, query };
}

export function navigate(path) {
  location.hash = "#" + path;
}

// Replace the current entry (no history spam, e.g. queue auto-advance).
export function replace(path) {
  const url = new URL(location.href);
  url.hash = "#" + path;
  location.replace(url.href);
}

export async function dispatch(view) {
  const gen = ++generation;
  const { path, query } = parseHash();
  for (const { regex, names, handler } of routes) {
    const m = path.match(regex);
    if (!m) continue;
    if (typeof currentCleanup === "function") {
      try {
        currentCleanup();
      } catch (err) {
        console.warn("route cleanup failed", err);
      }
    }
    currentCleanup = null;
    view.replaceChildren();
    window.scrollTo(0, 0);
    const params = {};
    names.forEach((n, i) => {
      params[n] = decodeURIComponent(m[i + 1]);
    });
    const cleanup = await handler(view, { params, query, path });
    if (gen !== generation) {
      // Navigation dépassée : démonter immédiatement ce qui vient d'être monté.
      if (typeof cleanup === "function") {
        try {
          cleanup();
        } catch (err) {
          console.warn("stale route cleanup failed", err);
        }
      }
      return path;
    }
    if (typeof cleanup === "function") currentCleanup = cleanup;
    return path;
  }
  navigate("/");
  return null;
}
