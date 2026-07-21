// Same-origin API client. The HttpOnly session cookie authenticates every
// call (fetch sends it automatically), including <video>/<track>/MSE media
// requests that go through /api/proxy.

import { navigate } from "./router.js";

export class ApiError extends Error {
  constructor(status, detail) {
    super(detail || `HTTP ${status}`);
    this.status = status;
    this.detail = detail || `HTTP ${status}`;
  }
}

async function request(method, path, { query, body } = {}) {
  const url = new URL(path, location.origin);
  if (query) {
    for (const [k, v] of Object.entries(query)) {
      if (v !== undefined && v !== null && v !== "" && v !== false) {
        url.searchParams.set(k, String(v));
      }
    }
  }
  let resp;
  try {
    resp = await fetch(url, {
      method,
      headers: body !== undefined ? { "Content-Type": "application/json" } : undefined,
      body: body !== undefined ? JSON.stringify(body) : undefined,
    });
  } catch {
    throw new ApiError(0, "Serveur injoignable");
  }
  if (resp.status === 401 && path !== "/api/session") {
    navigate("/login");
    throw new ApiError(401, "Session expirée");
  }
  if (!resp.ok) {
    let detail = "";
    try {
      detail = (await resp.json()).detail || "";
    } catch {
      /* non-JSON error body */
    }
    throw new ApiError(resp.status, detail);
  }
  if (resp.status === 204) return null;
  return resp.json();
}

const id = encodeURIComponent;

export const api = {
  // ─── session ───
  sessionStatus: () => request("GET", "/api/session"),
  login: (token) => request("POST", "/api/session", { body: { token } }),
  logout: () => request("DELETE", "/api/session"),

  // ─── browse ───
  feed: (refresh = false) => request("GET", "/api/feed", { query: { refresh } }),
  suggestions: (refresh = false) => request("GET", "/api/suggestions", { query: { refresh } }),
  search: (q, source = "youtube", limit = 20) =>
    request("GET", "/api/search", { query: { q, source, limit } }),
  lives: () => request("GET", "/api/lives"),
  channelVideos: (channelId, platform = "youtube", limit = 50) =>
    request("GET", `/api/channels/${id(channelId)}/videos`, { query: { platform, limit } }),
  playlistVideos: (playlistId, platform = "youtube", limit = 200) =>
    request("GET", `/api/ytplaylists/${id(playlistId)}/videos`, { query: { platform, limit } }),

  // ─── videos ───
  videoDetails: (videoId, platform = "youtube") =>
    request("GET", `/api/videos/${id(videoId)}`, { query: { platform } }),
  videoStreams: (videoId, { platform = "youtube", maxHeight = 1080, subLangs = "" } = {}) =>
    request("GET", `/api/videos/${id(videoId)}/streams`, {
      query: { platform, max_height: maxHeight, sub_langs: subLangs },
    }),
  mpdUrl: (videoId, platform = "youtube", maxHeight = 1080) =>
    `/api/videos/${id(videoId)}/mpd?platform=${id(platform)}&max_height=${maxHeight}`,
  related: (videoId, platform = "youtube", limit = 20) =>
    request("GET", `/api/videos/${id(videoId)}/related`, { query: { platform, limit } }),
  sponsorSegments: (videoId, platform = "youtube") =>
    request("GET", `/api/videos/${id(videoId)}/sponsor`, { query: { platform } }),
  likeVideo: (videoId) => request("POST", `/api/videos/${id(videoId)}/like`),
  commentVideo: (videoId, text) =>
    request("POST", `/api/videos/${id(videoId)}/comment`, { body: { text } }),
  videoComments: (videoId, page = 1, pageSize = 50) =>
    request("GET", `/api/videos/${id(videoId)}/comments`, {
      query: { platform: "odysee", page, page_size: pageSize },
    }),

  // ─── followed channels ───
  channels: () => request("GET", "/api/channels"),
  followChannel: (ref) => request("POST", "/api/channels", { body: { ref } }),
  unfollowChannel: (channelId) => request("DELETE", `/api/channels/${id(channelId)}`),

  // ─── history ───
  history: (limit = 200) => request("GET", "/api/history", { query: { limit } }),
  recordWatch: (video) => request("POST", "/api/history", { body: { video } }),
  watchedIds: () => request("GET", "/api/history/watched-ids"),
  savePosition: (videoId, position, duration) =>
    request("PUT", `/api/history/${id(videoId)}/position`, { body: { position, duration } }),
  resume: async (videoId) => {
    try {
      return await request("GET", `/api/history/${id(videoId)}/resume`);
    } catch (err) {
      if (err instanceof ApiError && err.status === 404) return null;
      throw err;
    }
  },
  removeWatch: (videoId) => request("DELETE", `/api/history/${id(videoId)}`),

  // ─── local playlists ───
  playlists: () => request("GET", "/api/playlists"),
  createPlaylist: (name) => request("POST", "/api/playlists", { body: { name } }),
  renamePlaylist: (playlistId, name) =>
    request("PATCH", `/api/playlists/${playlistId}`, { body: { name } }),
  deletePlaylist: (playlistId) => request("DELETE", `/api/playlists/${playlistId}`),
  playlistItems: (playlistId) => request("GET", `/api/playlists/${playlistId}/items`),
  addPlaylistItem: (playlistId, video) =>
    request("POST", `/api/playlists/${playlistId}/items`, { body: { video } }),
  removePlaylistItem: (playlistId, position) =>
    request("DELETE", `/api/playlists/${playlistId}/items/${position}`),

  // ─── misc ───
  status: () => request("GET", "/api/status"),
  proxyUrl: (url) => "/api/proxy?url=" + encodeURIComponent(url),
  proxyHlsUrl: (url) => "/api/proxy/hls?url=" + encodeURIComponent(url),
};
