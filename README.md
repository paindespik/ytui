# ytui

A terminal YouTube client: follow channels via RSS (no account needed), search with yt-dlp, and play videos in an external mpv window.

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
```

## Configuration

Config lives at `~/.config/ytui/config.toml` and is created with defaults on first run:

```toml
[feed]
backend = "rss"

[channels]
# Channel IDs (UC...) or @handles. Handles are resolved once and cached.
list = [
  "UCXuqSBlHAE6Xw-yeJA0Tunw",
  "@LinusTechTips",
]

[player]
command = "mpv"
format = "bestvideo[height<=?1080]+bestaudio/best"
audio_only = false

[ui]
thumbnails = true   # thumbnail panel; set to false for SSH / plain terminals
```

Feed metadata is cached in `~/.cache/ytui/meta.sqlite` (15 min TTL). When offline, the feed is served from the cache with a warning banner.

## Keybindings

| Key | Action |
|---|---|
| `j` / `k` / arrows | Move selection |
| `g` / `G` | Top / bottom |
| `Enter` | Play video/playlist in mpv, or open a channel |
| `o` | Open the highlighted item's channel or playlist view |
| `a` | Follow the channel (persist it to config.toml) |
| `p` | (playlist view) Play the whole playlist |
| `r` | Refresh feed |
| `/` | Open search |
| `Escape` | Go back |
| `?` | Help |
| `q` | Quit |

## Search, channels and playlists

Search results mix videos, playlists and channels (see the *Type* column).

- `Enter` on a **video** or **playlist** plays it in mpv (mpv's ytdl_hook streams whole playlists natively).
- `Enter` on a **channel** opens its latest videos; press `a` there to follow it — the channel ID is written to `[channels].list` in config.toml and appears in the home feed on the next refresh.
- `o` on a playlist opens the playlist view, where `Enter` plays one entry and `p` plays them all.

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
