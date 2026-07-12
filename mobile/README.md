# ytui mobile

Android client for the ytui backend (Flutter).

## Requirements

- A running ytui backend (see `backend/`), reachable over HTTPS.
- Flutter stable (the CI uses `ghcr.io/cirruslabs/flutter:stable`).

## Build

```sh
cd mobile
flutter pub get
flutter analyze && flutter test
flutter build apk --release   # build/app/outputs/flutter-apk/app-release.apk
```

The release build is signed with the debug keystore so the APK can be
sideloaded straight from CI; switch to a real keystore for store distribution.

## First launch

The app opens on the settings screen: enter the server URL
(e.g. `https://ytui.example.com`) and the API token (`YTUI_API_TOKEN`
of the backend), then "Test connection" and Save.

## Features (v1)

- Home feed of followed channels (pull-to-refresh, live badge pinned on top)
- Search mixing videos, playlists and channels
- Channel and YouTube-playlist screens ("play all" through the queue)
- Integrated player (media_kit / libmpv): stream URLs are resolved by the
  backend right before playback (HLS > progressive > split DASH), local play
  queue, cross-device resume (positions synced through the backend)
- Video details with Like / Comment (needs `ytui auth push` from the desktop)
- Watch history and local playlists (server-side, shared with the TUI)
- Live notifications: 5-min foreground poll + 15-min workmanager background task
- Long-press a video for quick actions (queue, details, save, follow)
