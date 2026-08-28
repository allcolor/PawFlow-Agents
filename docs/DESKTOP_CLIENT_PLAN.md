# PawFlow Native Desktop Client Implementation Plan

Status: approved for implementation after this plan is documented.

## 1. Objective

Deliver a PawFlow desktop client for Windows, Linux, and macOS that provides the
same server-selection, authentication, and embedded webchat model as the Android
client while respecting desktop security and packaging conventions.

The desktop client is a PawFlow frontend. It does not create, start, stop, or own
relays. Relay lifecycle remains the responsibility of the webchat resource panel
and PawFlow Relay Desktop.

## 2. User outcomes

A user can:

1. install a signed desktop application for the current operating system;
2. save several PawFlow server profiles;
3. keep each server's Private Gateway key protected by the operating-system
   credential facility;
4. discover the server's configured authentication providers;
5. authenticate with PawFlow builtin credentials or OAuth 2.0 using PKCE;
6. open several independent webchat tabs and preserve their navigation state;
7. follow a `pawflow://` deep link into the correct server and conversation;
8. upload files through the webchat and download authenticated FileStore assets;
9. open external links in the system browser without granting them application
   privileges;
10. remove a profile and its local credentials and cookies.

## 3. Existing contracts to reuse

The implementation must mirror, not fork, these existing contracts:

- `pawflow-android/.../ServerProfile.java`: HTTPS-only server URLs with no
  userinfo, fragment, or non-root path.
- `ServerStore.java` and `CryptoStore.java`: non-secret profile metadata plus
  protected gateway material.
- `MainActivity.java`: `/auth/mobile/providers`,
  `/auth/mobile/start`, `/auth/mobile/builtin`,
  `/auth/mobile/consume`, and `/_gateway`.
- `ChatTabs.java`: explicit tab add, activate, close, and clear semantics.
- `docs/relay_client.md`: clients do not own relay lifecycle.
- `pawflow-relay-desktop/package.json`: Electron 41 and electron-builder
  packaging conventions for all three desktop operating systems.

The server-side mobile authentication API remains the source of truth. No new
desktop-only authentication protocol is introduced.

## 4. Product boundary

### 4.1 Included

- native application window and menus;
- multi-server profile manager;
- OS-protected local secrets;
- builtin and OAuth/PKCE login;
- embedded webchat;
- multi-tab state;
- same-origin navigation enforcement;
- authenticated downloads and file chooser support;
- deep links;
- session clearing and logout;
- platform installers and portable archives;
- automatic update metadata integration, without silently applying an update.

### 4.2 Excluded

- relay management;
- server installation;
- server settings duplicated outside the webchat;
- an independent conversation database;
- an independent agent protocol;
- storing PawFlow messages outside the server;
- bypassing Private Gateway or AuthGateway controls.

## 5. Architecture decision

Create a separate `pawflow-desktop/` Electron application. Do not merge it into
`pawflow-relay-desktop/`.

Electron is selected because PawFlow already builds and ships an Electron desktop
application on Windows, Linux, and macOS, the product requires an embedded
authenticated web surface, and this avoids introducing a second native toolchain.
The application is still a native desktop distribution with OS installers, window
management, deep links, keychain-backed encryption, downloads, and system browser
integration.

### 5.1 Process model

- Main process:
  - owns profiles, encrypted secrets, cookies, deep-link dispatch, downloads,
    dialogs, and controlled web contents;
  - performs authentication HTTP requests;
  - validates every URL and IPC payload;
  - creates one isolated `WebContentsView` per chat tab.
- Renderer:
  - renders only the trusted local application chrome;
  - receives a narrow API from preload;
  - never receives a plaintext gateway key or authentication password after the
    corresponding request completes.
- Chat views:
  - load only one configured HTTPS origin;
  - have Node integration disabled;
  - use context isolation and sandboxing;
  - cannot navigate the main application renderer;
  - send new-window and download requests to main-process policy handlers.

### 5.2 Proposed files

```
pawflow-desktop/
  package.json
  package-lock.json
  README.md
  src/
    main.js
    preload.js
    renderer.js
    index.html
    styles.css
    profile_store.js
    auth.js
    pkce.js
    tab_manager.js
    url_policy.js
    assets/
  scripts/
    package-portable.js
tests/
  test_pawflow_desktop.py
```

Modules stay below the repository's target file size and expose pure helpers for
Node-based unit tests.

## 6. Data model

### 6.1 Profile metadata

`profiles.json` under Electron's `app.getPath("userData")`:

```json
{
  "version": 1,
  "profiles": [
    {
      "id": "UUID",
      "name": "Production",
      "base_url": "https://pawflow.example.org",
      "secret_ref": "gateway:UUID",
      "created_at": "UTC timestamp",
      "updated_at": "UTC timestamp"
    }
  ],
  "last_profile_id": "UUID"
}
```

Required fields never fall back to anonymous or default values. Invalid records
fail with a visible profile-specific error instead of being silently skipped.

### 6.2 Secret storage

Use Electron `safeStorage` only when encryption is available. On Linux, reject
the `basic_text` backend; require a supported secret service such as
Secret Service/KWallet. Store encrypted blobs in a separate `secrets.json`
indexed by opaque `secret_ref`; the profile file contains no gateway key.

The application must never:

- write plaintext secrets to logs;
- expose a secret through renderer IPC;
- include secrets in crash reports;
- copy a key to the clipboard without an explicit user action;
- reuse one profile's cookies for another origin.

If the OS credential backend is unavailable, profile creation stops with a
remediation message. There is no plaintext fallback.

### 6.3 Session state

Chat tab metadata may be persisted locally:

```json
{
  "version": 1,
  "profiles": {
    "UUID": {
      "active_tab_id": "UUID",
      "tabs": [
        {
          "id": "UUID",
          "url": "https://pawflow.example.org/chat?conversation_id=...",
          "title": "Conversation title",
          "created_at": "UTC timestamp",
          "updated_at": "UTC timestamp"
        }
      ]
    }
  }
}
```

Only same-origin URLs are stored. Authentication cookies remain in Electron's
partitioned session store and are never copied into JSON.

## 7. Authentication protocol

### 7.1 Server discovery

1. Normalize and validate the HTTPS base URL.
2. Send `GET /auth/mobile/providers` with
   `X-PawFlow-Gateway-Key` from the main process.
3. Render the returned password and OAuth provider choices.
4. Reject redirects to a different origin.

### 7.2 Builtin authentication

1. Generate a PKCE verifier with at least 64 random bytes and an S256 challenge.
2. Send `POST /auth/mobile/builtin` with username, password, and challenge.
3. Receive `handoff_code`.
4. POST `code` and `code_verifier` to `/auth/mobile/consume` inside the
   partitioned chat session.
5. Clear the verifier and password from application state.
6. Load `/chat` only after the handoff succeeds.

### 7.3 OAuth authentication

1. Generate PKCE.
2. Send `POST /auth/mobile/start` with provider and challenge.
3. Persist only the pending flow id, profile id, encrypted verifier, UUID, and
   timestamp.
4. Open the authorization URL in the system browser.
5. Register `pawflow://oauth` as the application protocol.
6. On callback, require the exact stored `flow_id`, correct scheme and host,
   and a known profile.
7. POST the callback code and verifier to `/auth/mobile/consume`.
8. Delete pending OAuth state on success, explicit failure, or user cancellation.

Pending state has no implicit expiry invented by the client. The server remains
authoritative for validity.

### 7.4 Private Gateway bootstrap

When no handoff is available, POST the stored gateway key and `next=/chat` to
`/_gateway` in the selected chat partition. Never place the key in a URL.

## 8. Tabs and navigation

Each profile uses its own persistent Electron session partition. Each tab owns an
independent `WebContentsView`, history, loading state, title, and unread marker.

Allowed in-app navigation:

- exact profile scheme, host, and effective port;
- HTTPS only;
- PawFlow chat, auth, graph, settings, and authenticated FileStore routes on that
  origin.

Navigation to another origin is cancelled and opened through
`shell.openExternal` only after `https:` validation. `file:`, `javascript:`,
`data:`, custom executable schemes, userinfo URLs, mixed content, and permission
requests are denied unless an explicit product feature adds a reviewed handler.

`window.open` on the PawFlow origin creates a new native tab. External popups
open in the system browser.

## 9. Downloads and uploads

Uploads use Electron's file chooser through the webchat.

For downloads:

1. intercept `will-download`;
2. require the configured PawFlow origin;
3. preserve the selected profile's authenticated cookie jar;
4. sanitize the suggested filename;
5. ask for a destination or use the OS Downloads directory according to the
   user's preference;
6. expose progress and cancellation;
7. never auto-open executable content.

A redirect during download is revalidated at every hop.

## 10. Deep links

Supported initial contract:

```
pawflow://oauth?flow_id=...&code=...
pawflow://open?server=<profile-uuid>&conversation_id=<uuid>
pawflow://open?server=<profile-uuid>&path=%2Fchat...
```

The app accepts a deep link from cold start or an existing single-instance
process. It validates UUIDs, profile existence, path allowlists, and origin. OAuth
callbacks never accept an arbitrary server URL.

## 11. UX

Main surfaces:

1. server selector with add, edit, remove, connect, and connection diagnostics;
2. login provider screen;
3. chat shell with profile indicator, horizontally scrollable tabs, add/close,
   back/forward, reload, and return-to-servers actions;
4. download shelf;
5. settings for session clearing, default download behavior, startup behavior,
   and diagnostics.

All user-facing strings are French by default and prepared for localization.
Every desktop surface remains usable at 1024x640 and scrolls vertically when
content does not fit.

## 12. Security controls

- `contextIsolation: true`;
- `sandbox: true`;
- `nodeIntegration: false`;
- no remote module;
- restrictive Content Security Policy for local chrome;
- IPC channel allowlist and schema validation;
- OS-protected secrets with no fallback;
- exact-origin checks including effective port;
- no arbitrary certificate bypass;
- no credentials in command-line arguments;
- no implicit permission grant for camera, microphone, geolocation, MIDI,
  notifications, clipboard read, or screen capture;
- renderer crash isolation per chat tab;
- cookies cleared when a profile is removed;
- logs redact URLs containing query data and all authorization material.

## 13. Packaging and release

Electron Builder targets:

- Windows: NSIS and ZIP;
- Linux: AppImage, DEB, and tar.gz;
- macOS: DMG and ZIP.

Use application id `org.allcolor.pawflow.desktop` and product name
`PawFlow Desktop`. Register the `pawflow` protocol in packaged builds.
Artifacts are added to the existing release-assets workflow only after platform
smoke tests pass. Code signing and notarization remain explicit release gates.

The application version is injected from the repository's single version source
during packaging; `package.json` is not a second product version authority.

## 14. Migration

Version 1 has no legacy desktop profile format. It may optionally import Android
server metadata only through an explicit exported profile that never contains a
plaintext gateway key.

If Relay Desktop server profiles are later importable, import only URL and name.
The user must confirm access to the existing keychain record or enter the gateway
key again. Do not read Relay Desktop JSON secrets directly.

## 15. Implementation work packages

### D1. Pure contracts

- URL normalization and same-origin policy;
- PKCE creation;
- profile and tab schemas;
- deterministic tests.

### D2. Protected persistence

- atomic profile/session writes;
- `safeStorage` adapter;
- Linux backend fail-closed behavior;
- migration/version checks.

### D3. Authentication

- provider discovery;
- builtin flow;
- OAuth flow;
- protocol callback and single-instance routing;
- session handoff.

### D4. Chat shell

- secure application window;
- isolated tab manager;
- navigation and popup policy;
- tab persistence and restoration.

### D5. Files and desktop integration

- upload chooser;
- authenticated downloads;
- external browser;
- native menus, keyboard shortcuts, and diagnostics.

### D6. Packaging

- electron-builder metadata;
- portable package;
- platform CI matrix;
- signing/notarization hooks;
- release artifact documentation.

## 16. Test strategy

### Unit

- valid and invalid URL normalization;
- effective-port origin matching;
- PKCE verifier/challenge vectors;
- profile schema and atomic persistence;
- OS-secret adapter failures;
- deep-link parsing and rejection;
- tab close/restore behavior;
- download filename sanitization.

### Integration

- mock PawFlow HTTPS server with Private Gateway, builtin auth, OAuth callback,
  and handoff consume;
- cookie isolation between two profiles;
- same-origin popup becomes a tab;
- external popup opens only through the system browser adapter;
- authenticated FileStore download carries session cookies;
- removed profile deletes secrets, cookies, and tab state.

### Platform smoke

- install, launch, protocol registration, login, open two chats, download a file,
  logout, uninstall;
- Windows 11, current Ubuntu LTS, and current macOS;
- offline relaunch with existing profile shows a recoverable connection error.

No test adds an implicit execution deadline. Test-only bounds are explicit in the
test command or fixture.

## 17. CI gates

- `npm ci`;
- `npm run check`;
- Node unit tests;
- Python static artifact tests;
- Electron headless integration tests on Linux;
- platform packaging matrix;
- artifact-name and version checks;
- dependency audit;
- no plaintext-secret fixtures;
- documentation link checks.

## 18. Acceptance criteria

The plan is complete when all of the following are demonstrated:

1. one codebase builds installable artifacts for Windows, Linux, and macOS;
2. two saved servers remain isolated;
3. gateway keys are unavailable in plaintext at rest and in renderer IPC;
4. builtin and OAuth/PKCE authentication both reach the webchat;
5. three tabs retain independent navigation state;
6. cold-start deep links work;
7. external navigation never gains application privileges;
8. authenticated FileStore download succeeds;
9. deleting a profile removes its secret and cookies;
10. the app contains no relay lifecycle controls;
11. unit, integration, security, and packaging gates pass;
12. relevant user and developer documentation ships with the implementation.

## 19. Risks and mitigations

| Risk | Mitigation |
|---|---|
| Linux has no usable keyring | Fail closed and show Secret Service/KWallet remediation |
| Electron web contents escape | Sandbox, isolation, narrow IPC, exact-origin policy |
| OAuth callback reaches wrong server | Bind flow id, verifier, and profile UUID |
| Cookie collision between servers | One persistent session partition per profile UUID |
| Webchat behavior drifts from Android | Reuse the same mobile auth endpoints and shared contract tests |
| Scope creep into relay management | Enforce the product boundary in UI and tests |
| Large renderer files | Split pure modules before the 800-line target |
| Unsigned packages create warnings | Treat signing/notarization as release gates, not runtime bypasses |
