# Android app

The Android client lives in `pawflow-android/`. It provides a native server
selector and authentication UI, then opens the selected server's webchat in
an embedded WebView.

## User flow

1. The native splash screen displays the PawFlow logo.
2. Add one or more PawFlow servers using a label, an HTTPS origin, and the
   server's private gateway key.
3. Select a server. If no PawFlow session cookie exists, the app displays the
   server's enabled login providers as native controls.
4. Built-in credentials are submitted directly to the server. OAuth2 providers
   open in a Custom Tab and return to `pawflow://oauth/callback`.
5. The authenticated webchat opens inside the app.

The webchat toolbar always includes **Servers**, which destroys the open
WebViews and returns to the native server selector without logging out.
Android Back navigates the active WebView history first and returns to the
server selector when that history is empty.

The native tab strip keeps several webchat WebViews open on the selected
server. Use **+** to start another chat, select a numbered tab to switch
without reloading it, and use **×** to close it. Each tab retains its own URL,
page state, and navigation history while sharing the server login cookie.
Closing the final tab returns to the server selector.

The native chrome (toolbar + tab strip) folds away to the right like a
drawer: a vertical grip tab hugging the right edge of the web area toggles
it, giving the webchat the whole screen.

A specific conversation deep-links via `…/chat?conversation_id=<id>` — in a
browser and in the app. The webchat sidebar's right-click menu offers
**Open in new tab**; inside the app, `window.open`/`target=_blank` becomes a
new native chat tab (`setSupportMultipleWindows` + `onCreateWindow`).

The WebView appends `PawFlowAndroid` to its user agent so the webchat can use
Android-safe navigation. Flow instance and template graphs open their
same-origin URL in a native WebView tab; browsers continue to use the embedded
blob-backed graph tab. Avoiding a graph iframe backed by a `blob:` URL keeps
the Android renderer stable while preserving the authenticated session.

## System bars and keyboard

`targetSdk 35` enforces edge-to-edge on Android 15+: every screen root is set
through `setScreen`, which absorbs the system bars, display cutout and IME as
padding (older releases keep the classic decor-fitted layout). Without it the
composer sat under the navigation bar and the WebView jumped while typing.
The activity also declares `android:windowSoftInputMode="adjustResize"`, and
the webchat root paints the inset strips navy to match the app chrome.

## Downloads

WebView ignores downloads unless a `DownloadListener` is set. Files the
webchat offers (agent-shared files, exports) are handed to the system
`DownloadManager` with the session cookie — so authenticated FileStore URLs
work — and land in the Downloads folder with a completion notification.

## Mobile authentication protocol

The default PawFlow flow exposes:

- `GET /auth/mobile/providers`
- `POST /auth/mobile/start`
- `POST /auth/mobile/builtin`
- `POST /auth/mobile/consume`

OAuth2 starts in the system browser. A five-minute in-memory handoff is bound
to an S256 PKCE challenge, the OAuth state, and a single-use random code. The
custom-scheme callback contains only the handoff code; it never contains the
PawFlow session token. The WebView exchanges the code and PKCE verifier through
`/auth/mobile/consume`, which sets the private-gateway and PawFlow session
cookies before redirecting to `/chat`. Provider cancellation is returned to
the app through the same callback and invalidates the pending handoff.

The consume route is exempt from the private gateway because it creates the
gateway cookie itself. The OAuth callback bypasses the gateway only while its
validated mobile state remains active.

## Client security

- Server profiles accept HTTPS origins only. Paths, queries, fragments, and
  cleartext HTTP are rejected.
- Gateway keys and pending PKCE verifiers are encrypted with an AES-GCM key
  held by Android Keystore.
- WebViews disable file/content access, mixed content, third-party cookies,
  and cross-origin navigation.
- External origins open in the system browser. No JavaScript bridge is
  exposed to web content.
- Removing a profile clears its local encrypted key and its PawFlow cookies.

Profiles that use the same hostname on different ports share Android WebView's
hostname-scoped cookies. Use distinct hostnames when those profiles must have
separate sessions.

## Build

The project requires JDK 17+, Android SDK 35, and Gradle 8.10.2:

```bash
export ANDROID_HOME=/path/to/android-sdk
./gradlew testDebugUnitTest assembleDebug
```

The debug APK is written to
`pawflow-android/app/build/outputs/apk/debug/app-debug.apk`.

Release tags run the same lint, unit-test, and debug-APK build in GitHub
Actions. The tag supplies `pawflowVersion` and `pawflowVersionCode`; the
result is published as `pawflow-android-<version>-debug.apk`. This beta
artifact uses an Android debug signature because no stable Android release
keystore is configured yet. A production or Play Store build must use a
separately protected, persistent release key.

