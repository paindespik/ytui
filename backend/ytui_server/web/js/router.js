// Minimal hash router: register "/pattern/:param" handlers, dispatch on hashchange.
// A handler may return a cleanup function, called before the next route renders.

const routes = [];
let currentCleanup = null;

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
    if (typeof cleanup === "function") currentCleanup = cleanup;
    return path;
  }
  navigate("/");
  return null;
}
