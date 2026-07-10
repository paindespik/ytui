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
ytui play <url>         # play a YouTube URL in mpv
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
thumbnails = true   # reserved for a future release
```

Feed metadata is cached in `~/.cache/ytui/meta.sqlite` (15 min TTL). When offline, the feed is served from the cache with a warning banner.

## Keybindings

| Key | Action |
|---|---|
| `j` / `k` / arrows | Move selection |
| `g` / `G` | Top / bottom |
| `Enter` | Play selected video in mpv |
| `r` | Refresh feed |
| `/` | Open search |
| `Escape` | Go back |
| `?` | Help |
| `q` | Quit |

## Development

```sh
pip install -e .[dev]
ruff check .
pytest
```
