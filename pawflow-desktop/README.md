# PawFlow Desktop

PawFlow Desktop is the native Windows, Linux, and macOS client for PawFlow
webchat. It manages server profiles, performs builtin or OAuth authentication
through the existing mobile PKCE endpoints, and hosts isolated chat tabs.

It is intentionally separate from PawFlow Relay Desktop. This application is a
conversation client and never creates, starts, stops, or owns relays.

## Security

- Server URLs are HTTPS-only and origin-pinned.
- Private Gateway keys are encrypted with Electron `safeStorage`.
- Linux refuses Electron's plaintext `basic_text` backend.
- The local renderer and remote chat views use sandboxing, context isolation, and
  no Node integration.
- Chat cookies are partitioned by server profile.
- External links open in the system browser.
- Unsupported schemes and cross-origin downloads are rejected.

## Development

```bash
cd pawflow-desktop
npm ci
npm run check
npm test
npm start
```

Build platform packages with `npm run dist:win`, `dist:linux`, or
`dist:mac`. Build outputs are written to
`dist/pawflow-desktop-installers`.

The complete implementation contract is documented in
`docs/DESKTOP_CLIENT_PLAN.md`.
