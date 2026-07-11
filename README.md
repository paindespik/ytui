# ytui

A terminal YouTube client with BitChute support: follow channels via RSS (no account needed), search with yt-dlp, and play videos in an external mpv window — with a playback queue, watch history, local playlists and background downloads.

## Requirements

- Python 3.11+
- **mpv** (system dependency, used for playback):
  - Debian/Ubuntu: `sudo apt install mpv`
  - Fedora: `sudo dnf install mpv`
  - Arch: `sudo pacman -S mpv`

yt-dlp is bundled as a Python library — you don't need the binary.

## Install

With [pipx](https://pipx.pypa.io/) from git:

```sh
pipx install git+https://github.com/paindespik/ytui.git
```

Or for development:

```sh
git clone <repo> youtube-cli && cd youtube-cli
python -m venv .venv && source .venv/bin/activate
pip install -e .[dev]
```

## Usage

```sh
ytui                    # open the TUI (home feed, or search if no channels configured)
ytui search "query"     # search and print results to stdout
ytui play <url>         # play a YouTube video or playlist URL in mpv
ytui --version          # print the version
```

## Configuration

Config lives at `~/.config/ytui/config.toml` and is created with defaults on first run:

```toml
[feed]
backend = "rss"

[channels]
# Channel IDs (UC...), @handles, or BitChute channel slugs prefixed with
# "bitchute:". Handles are resolved once and cached.
list = [
  "UCXuqSBlHAE6Xw-yeJA0Tunw",
  "@LinusTechTips",
  "bitchute:bitchute",
]

[player]
command = "mpv"
format = "bestvideo[height<=?1080]+bestaudio/best"
audio_only = false
download_dir = "~/Videos"   # target for the 'd' (download) action

[ui]
thumbnails = true   # thumbnail panel; set to false for SSH / plain terminals
```

Feed metadata, watch history and local playlists are stored in `~/.cache/ytui/meta.sqlite` (feed TTL: 15 min). When offline, the feed is served from the cache with a warning banner.

## Keybindings

### Video lists (feed, search, channel, YouTube playlist, history, local playlist)

| Key | Action |
|---|---|
| `j` / `k` / arrows | Move selection |
| `g` / `G` | Top / bottom |
| `Enter` | Play video/playlist in mpv, or open a channel |
| `e` | Enqueue in the running mpv (append-play; starts mpv if idle) |
| `i` | Video details (full description, views, likes, date) |
| `o` | Open the highlighted item's channel or playlist view |
| `a` | Follow the channel (persist it to config.toml) |
| `A` | Play audio only (this video, ignores the global setting) |
| `d` | Download in the background to `[player].download_dir` |
| `s` | Save the item to a local playlist (picker modal) |
| `Space` | Pause / resume mpv playback |
| `n` | Next entry in the mpv queue |

### Home feed

| Key | Action |
|---|---|
| `r` | Refresh feed |
| `/` | Open search |
| `h` | Watch history |
| `P` | Local playlists |
| `,` | Settings |
| `q` | Quit |

### Other screens

| Key | Action |
|---|---|
| `p` | (YouTube playlist / local playlist) Play the whole playlist |
| `x` | (history) Remove entry · (local playlist) Remove entry · (settings) Remove channel |
| `n` / `r` | (local playlists) New / rename playlist |
| `x` | (local playlists) Delete playlist (with confirmation) |
| `y` | (video details) Copy URL to clipboard (OSC 52) |
| `b` / `m` / `t` | (settings) Toggle backend / audio-only / thumbnails |
| `Escape` | Go back |
| `?` | Help |

## Playback queue

Playback goes through a single mpv instance controlled over its JSON IPC socket
(`$XDG_RUNTIME_DIR/ytui-mpv.sock`). `Enter` replaces what is playing, `e` appends
to the queue, `Space` pauses and `n` skips. A status line (“▶ playing (2 queued)”)
appears above the footer on the home feed while mpv runs. When mpv exits, ytui
returns to the idle state automatically.

## Watch history

Every play/enqueue is recorded. Watched videos show a dimmed `✓` marker in all
lists. Press `h` for the history screen (`Enter` replays, `x` removes an entry).

## Local playlists

Local playlists live in ytui's SQLite database (no YouTube account involved) and
can contain both videos and YouTube playlists. Press `s` on any list item to save
it to a playlist (create one on the fly), and `P` from the home feed to manage
them: `n` new, `r` rename, `x` delete, `Enter` to open. Inside a playlist,
`Enter` plays one entry, `p` plays everything through the mpv queue in order,
and `x` removes an entry.

## Settings

Press `,` for the settings screen: add followed channels (`a` — accepts UC ids,
@handles or `bitchute:<slug>`), remove them (`x`), toggle the
feed backend, global audio-only and thumbnails. Changes are written to
`config.toml` immediately, preserving your comments.

## Search, channels and playlists

Search results mix videos, playlists and channels (see the *Type* column).

- `Enter` on a **video** or **playlist** plays it in mpv (mpv's ytdl_hook streams whole playlists natively).
- `Enter` on a **channel** opens its latest videos; press `a` there to follow it — the channel ID is written to `[channels].list` in config.toml and appears in the home feed on the next refresh.
- `o` on a playlist opens the playlist view, where `Enter` plays one entry and `p` plays them all.

## BitChute channels

BitChute channels can be followed alongside YouTube ones. The channel slug is
the name in the channel URL — for `bitchute.com/channel/<slug>/` the entry is
`bitchute:<slug>`. Add it from the settings screen (`,` then `a`) or directly
to `[channels].list` in config.toml, then press `r` on the home feed to
refresh. Feeds come from BitChute's RSS API and use the same cache TTL and
offline fallback as YouTube. Pressing `a` on a BitChute video in any list
follows its channel (the `bitchute:` prefix is added automatically). Playback
goes through mpv via yt-dlp's native BitChute extractor.

## Thumbnails

A side panel shows the highlighted item's thumbnail, rendered with
[textual-image](https://github.com/lnqs/textual-image). The image protocol is
auto-detected:

- **foot** (and other sixel-capable terminals): crisp Sixel graphics.
- **kitty / ghostty**: kitty graphics protocol.
- anything else: Unicode half-block fallback (works everywhere, lower fidelity).

Caveats:

- **tmux/screen break Sixel and kitty graphics** — you'll get the half-block fallback inside a multiplexer.
- Set `thumbnails = false` under `[ui]` to disable the image entirely (e.g. slow SSH links).

Thumbnails are cached in `~/.cache/ytui/thumbs/` with LRU eviction above ~100 MB.

## Development

```sh
pip install -e .[dev]
ruff check .
pytest
```
