# Game Night Scores – Android app

A small Android app that shows the score sheets from `../game-scores` offline, in a full-screen WebView. The pages are copied into the APK at build time, so the app and the web version are always the same code.

## How a new version reaches the phone

`.github/workflows/android-app.yml` builds the app on every push that changes `game-scores/` or `android/`. When the signing secrets are set it publishes a GitHub release `app-v1.<run number>` with the APK attached.

- The run number only goes up, so each APK has a higher `versionCode`.
- Every build is signed with the same key, and the `applicationId` (`app.gamenightscores`) never changes.

Together those make Android install a new APK as an **update** over the old one: no uninstall, and all saved scores stay. The app checks the latest release when the menu opens and shows a "Download update" banner when there is a newer version.

## Signing secrets (one-time setup)

Add two repository secrets under **Settings → Secrets and variables → Actions → New repository secret**:

| Name | Value |
|---|---|
| `ANDROID_KEYSTORE_BASE64` | the release keystore, base64-encoded |
| `ANDROID_KEYSTORE_PASSWORD` | its password (key alias `gamenight`, same password for the key) |

Keep a private backup of the keystore and password. If they are lost, no future build can update the installed app; it would have to be uninstalled (losing its scores) and installed again.

Without the secrets the workflow only builds an unsigned test APK to check the code compiles, and publishes nothing.

## Photo scoring

On claude.ai the viewer's Claude account reads photos. In the app there is no claude.ai, so the menu has a **Photo scoring** setting for an Anthropic API key, stored only on the phone. Photos are sent with the official Anthropic JS SDK (bundled in `game-scores/vendor/`, MIT licence) to `claude-opus-5-5`, with server-side refusal fallbacks enabled.

## Building locally

Needs JDK 17 and the Android SDK (platform 35).

```
cd android
gradle assembleDebug
```
