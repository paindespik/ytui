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

### APK signing

The release build is signed with the project's stable keystore so updates
install over the previous version without wiping app data:

- `android/ytui-release.keystore` — RSA 2048, alias `ytui`, CN=ytui,
  O=seandriard, valid 10 000 j. SHA-256 du certificat :
  `8d4e944e0b8d144dc64daaac7f1c30cca515b3febb7097a31dd5ef9415a54bc1`
- `android/key.properties` — `storeFile`/`storePassword`/`keyAlias`/`keyPassword`
  consumed by `android/app/build.gradle` when present.

Both are gitignored ("Signing — never commit secrets" in `mobile/.gitignore`)
and backed up on the personnel server under `~/ytui-signing/` (perms 700/600).

Building on a machine without those two files falls back to that machine's
local debug key: the APK then carries a different signature than the
installed one, `adb install -r` fails, and the phone needs an
`adb uninstall dev.ytui.app` first (wipes the SharedPreferences: server URL +
token). Restore `ytui-signing/*` into `android/` before building when in doubt.

CI keeps in sync: `.forgejo/workflows/ci.yml` decodes the repo Actions secrets
`ANDROID_KEYSTORE_B64` (base64 of the keystore) and `ANDROID_KEYSTORE_PASSWORD`
into `android/ytui-release.jks` + `key.properties` before the build, so CI
artifacts carry the same signature as local builds and install over each
other (no uninstall dance). If you ever regenerate the keystore, re-push both
secrets: `PUT /api/v1/repos/<owner>/ytui/actions/secrets/ANDROID_KEYSTORE_B64`
and `…/ANDROID_KEYSTORE_PASSWORD` with `{"data": …}`.

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
- In the player (portrait): a like toggle and a comments panel that replaces the
  queue/suggestions list without interrupting playback — comments can be posted
  from there (YouTube; Odysee is read-only)
- Watch history and local playlists (server-side, shared with the TUI)
- Live notifications: 5-min foreground poll + 15-min workmanager background task
- Long-press a video for quick actions (queue, details, save, follow)
