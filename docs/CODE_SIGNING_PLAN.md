# Code Signing Plan — PawFlow / PawCode / Android / Relay artifacts

Status: **draft** — Android debug publication exists; stable production signing
is planned but not implemented

## 1. Purpose

PawFlow currently ships unsigned binaries and packages. This plan defines how to
sign every distributable artifact so end users get fewer scary warnings, and so
malicious builds of our software are easier to detect. It covers:

- Windows executables and installers (`setup.exe`, `pawcode.exe`)
- Android APKs and future Android App Bundles (`.apk`, `.aab`)
- Relay Desktop executables, helpers, and bundled native tools such as `frpc.exe`
- macOS applications and disk images (`.app`, `.dmg`, `.pkg`) — **not built yet**
- Linux packages (`.deb`, `.rpm`) and archives (`.tar.gz`, `.zip`)
- Checksums and detached signatures for every artifact

It deliberately does **not** reuse the PFP Ed25519 signing key (see §9).

## 2. Current state (2026-08)

Artifacts produced by `.github/workflows/release-assets.yml` and
`scripts/build-pawcode-installer.py`:

| Artifact | Producer | Platform | Signed today? |
|---|---|---|---|
| `pawcode-<ver>-setup.exe` | NSIS (`build_nsis`) | Windows | No |
| `pawcode_<ver>_<arch>.deb` | `dpkg-deb` | Linux | No |
| `pawcode-<ver>-<os>.zip/.tar.gz` | PyInstaller + zipfile | all | No |
| `pawflow-relay-cli-<ver>-*.zip/.tar.gz` | PyInstaller + zipfile | all | No |
| Relay Desktop (Electron) | `dist:win` / `dist:linux` | Win/Linux | No |
| Bundled FRP client (`frpc.exe`) | verified FRP 0.70.1 upstream archive | Windows | No |
| `pawflow-installers/*` | `build-pawflow-install-zip.sh` | Linux | No |
| `pawflow-android-<ver>-debug.apk` | Gradle / Android release-assets job | Android | Debug key only |

No `.rpm` and no macOS artifacts are produced today. The Android release job
builds and verifies an explicitly named debug APK, but no stable Android release
keystore exists yet. No Authenticode, GPG, or Apple notarization infrastructure
exists.

## 3. Why one key cannot sign everything

Each OS trusts signatures through a different mechanism:

| Platform | Trust anchor | Verifier |
|---|---|---|
| Windows | CA root store (Authenticode) or Microsoft (Azure Trusted Signing) | SmartScreen, kernel, `signtool verify` |
| Android | App signing certificate pinned to the package identity | Package Manager, `apksigner verify`, Google Play |
| macOS | Apple Developer Program (Developer ID + notarization) | Gatekeeper |
| Linux | Local GPG keyring imported by the admin | `apt`/`dpkg`, `dnf`/`rpm` |
| PawFlow .pfp | Ed25519 public key embedded in manifest (TOFU) | PawFlow itself |

The PFP key is a raw Ed25519 key verified only by PawFlow (trust-on-first-use).
Windows and macOS require X.509 certificates from a CA they trust; Linux uses
OpenPGP. **One key cannot satisfy all four**, and reusing the PFP key for OS
binaries would widen the blast radius of a single leak.

## 4. Windows (.exe / NSIS installer)

### What is needed

An **Authenticode** signature on every shipped Windows PE executable, including
`pawcode.exe`, Relay Desktop, its native helpers, and the bundled `frpc.exe`,
followed by a signature on the final NSIS/Electron installer. Apply signatures
with `signtool` (Windows SDK) or `osslsigncode` (cross, on Linux CI), and add an
**RFC 3161 timestamp** so each signature stays valid after the certificate
expires.

Signing only the outer installer is insufficient. Windows Defender extracts and
scans bundled executables independently, so an unsigned `frpc.exe` can still be
reported as `Win32/Frproxy` even when the installer itself is trusted.

### Registration / where to get the certificate

Two viable routes:

1. **Azure Trusted Signing (recommended)** — Microsoft-operated signing service.
   - Register: an Azure subscription + the "Trusted Signing" resource.
   - Cost: ~US$9.99/month + Azure resource fees (identity validation one-time).
   - No hardware token required; the key lives in Microsoft's HSM.
   - Best SmartScreen outcome for new software: Microsoft already knows the key.
   - Sign in CI with `AzureSignTool` and the `TrustedSigning` action.
2. **Classic CA certificate (OV or EV)** — DigiCert, Sectigo, GlobalSign, SSL.com.
   - OV code-signing: ~US$100–300/year.
   - EV: ~US$300–600/year + mandatory USB hardware token (or HSM).
   - EV improves SmartScreen reputation but requires keeping the token in CI.
   - Registration: purchase from the CA, organization verification (D-U-N-S for EV).

### SmartScreen (the "unknown publisher" warning)

SmartScreen is a **reputation** system, not just a certificate check. Even with a
valid Authenticode cert, a brand-new publisher gets "More info → Run anyway"
until downloads and prevalence build reputation. Azure Trusted Signing is the
fastest path today because the key is pre-vetted by Microsoft.

### Defender and dual-use tunnel binaries

Authenticode and SmartScreen reputation do not disable antivirus signatures.
FRP is a legitimate reverse-proxy client, but its tunnelling capability is also
useful to attackers, so Defender can classify an authentic build as
`Win32/Frproxy`. A valid signature improves publisher attribution and allows
certificate-based enterprise policy, but it does not by itself guarantee that
Defender will accept the file.

For every Relay Desktop release:

1. Pin the FRP version and upstream source/archive digest.
2. Prefer a reproducible build from the pinned FRP source. Until that is in
   place, retain the existing verified upstream archive and record its digest in
   release provenance.
3. Sign and timestamp `frpc.exe` before it is embedded in Relay Desktop.
4. Submit the signed `frpc.exe` and the signed installer to Microsoft Security
   Intelligence as suspected false positives before public release.
5. Block publication until signature verification passes and any unexpected
   Defender verdict has been reviewed. Do not add broad Defender exclusions or
   ask users to exclude the Relay Desktop installation directory.

### Steps (CI, GitHub Actions)

1. Store certificate/key in `AZURE_*` secrets (Trusted Signing) or import the
   PFX (OV/EV) into a protected secret; keep the EV token in a hardware module
   accessed by the runner (e.g. via a signing service), never in the repo.
2. Inventory every Windows PE file in the release payload and fail if an
   executable is not covered by the signing manifest.
3. Sign inner payloads first: `frpc.exe`, Relay Desktop helpers, Relay Desktop,
   and `pawcode.exe`.
4. Build the NSIS/Electron installer from those signed payloads, then sign the
   final installer last.
5. Add `/tr http://timestamp.digicert.com /td SHA256` (or the CA's RFC 3161 URL).
6. Verify every signed PE file with `signtool verify /pa /v` in CI, including a
   post-packaging extraction check so packaging cannot replace a signed payload.
7. Generate release provenance containing the upstream FRP version and digest,
   the shipped `frpc.exe` digest, and the signer identity.
8. Submit release candidates to Microsoft Security Intelligence and record the
   submission/result in the release checklist.
9. Add a workflow that checks signatures and provenance on release artifacts.

## 5. Android (.apk / .aab)

### Trust model and artifact types

Android requires every installable APK to be signed. The signing certificate is
the durable identity of `org.allcolor.pawflow`: Android accepts an update only
when its signer matches the installed application or belongs to a valid signing
lineage. A debug certificate is suitable only for development. GitHub-hosted
runners generate unrelated debug keys, so successive debug releases are not a
stable update channel.

The direct GitHub release channel should publish a signed universal APK. A
future Play Store channel should publish an Android App Bundle and enable
**Play App Signing**: Google protects the app-signing key while CI uses a
separate, replaceable upload key. Direct APK users still require a PawFlow-held
stable signing key.

Use APK Signature Scheme v2 and v3. Keep v1 disabled unless support below
Android 7 is introduced; the current minimum is Android 8 (API 26).

### One-time key ceremony

1. Assign two people as key custodians and record the package name, alias,
   creation date, certificate SHA-256 fingerprint, and recovery locations.
2. Generate a dedicated Android release key offline, never from a CI runner:

   ```bash
   keytool -genkeypair -v \
     -keystore pawflow-android-release.jks \
     -alias pawflow-android \
     -keyalg RSA -keysize 4096 -validity 10000
   keytool -list -v -keystore pawflow-android-release.jks \
     -alias pawflow-android
   ```

3. Keep two encrypted offline backups in separate custody locations. Test
   restoration before the first signed release. Losing the direct-distribution
   key prevents normal updates for existing sideloaded installations.
4. Store the expected public certificate SHA-256 fingerprint as a reviewed
   repository variable. It is not secret and must be compared on every build.
5. If Google Play distribution is enabled, enroll the app-signing key in Play
   App Signing and create a distinct upload key. Never use the PFP, GPG,
   Authenticode, or Apple key for Android.

### GitHub Actions secrets

Create a protected `android-release` GitHub Environment with required
reviewers and these Actions secrets:

| Secret | Content |
|---|---|
| `ANDROID_SIGNING_KEYSTORE_B64` | Base64 of the encrypted JKS/PKCS12 file |
| `ANDROID_SIGNING_KEY_ALIAS` | Release-key alias |
| `ANDROID_SIGNING_STORE_PASSWORD` | Keystore password |
| `ANDROID_SIGNING_KEY_PASSWORD` | Private-key password |

Set `ANDROID_SIGNING_CERT_SHA256` as an environment variable containing the
expected public fingerprint. GitHub Actions must mask passwords, never print
Gradle properties, never upload the decoded keystore, and delete it from
`RUNNER_TEMP` in an `if: always()` cleanup step.

### Gradle integration

1. Add a `release` signing configuration that reads the keystore path, alias,
   and passwords from environment variables. Do not put credentials in
   `gradle.properties`, source files, workflow arguments, or build logs.
2. Make `assembleRelease` and `bundleRelease` fail with a clear error when
   any signing input is absent. **Never fall back to the debug signing config.**
3. Keep local and pull-request validation on `lintDebug`,
   `testDebugUnitTest`, and `assembleDebug`; forked PRs must never receive
   signing secrets.
4. Inject `versionName` and monotonic `versionCode` from the release tag,
   as the current debug release job already does.

### Tag CI and publication

The protected tag job should perform this order:

1. Check out the exact annotated tag and verify it points to a green `main`
   SHA.
2. Decode the keystore into `RUNNER_TEMP`, set mode `0600`, and expose only
   its path and passwords to the Gradle process environment.
3. Run `./gradlew clean lintRelease testReleaseUnitTest assembleRelease
   bundleRelease`.
4. Verify the APK before upload:

   ```bash
   zipalign -c -v 4 app-release.apk
   apksigner verify --verbose --print-certs app-release.apk
   aapt dump badging app-release.apk
   ```

5. Fail unless the package is `org.allcolor.pawflow`, the artifact is not
   debuggable, versionName/versionCode match the tag, v2/v3 verification
   succeeds, and the signer certificate matches
   `ANDROID_SIGNING_CERT_SHA256`.
6. Generate SHA-256 checksums, an SBOM/provenance statement, and upload only
   the verified signed APK/AAB. The release publish job must depend on this
   verification job and use `if-no-files-found: error`.
7. Delete the decoded keystore and unset signing variables with
   `if: always()`. Retain verification logs and provenance, never the key.

Until this gate is implemented and the stable key ceremony is complete, release
artifacts must retain the explicit `-debug.apk` suffix.

### Rotation and incident response

- Test backup restoration annually and before changing custodians.
- For Google Play, use Play's upload-key reset process if only the upload key is
  lost. Follow Play's supported app-signing-key upgrade process when rotating
  the protected signing key.
- For direct APK distribution, preserve signing lineage where supported and
  test upgrades from the last public APK on API 26 and the latest Android
  before release.
- On suspected compromise, stop Android publication, remove GitHub secrets,
  revoke environment access, rotate the upload key, document affected
  fingerprints, and publish recovery instructions. Do not silently sign a new
  package with an unrelated key.

## 6. macOS (.app / .dmg / .pkg)

### What is needed

A **Developer ID Application** certificate + **notarization** by Apple +
**stapling** of the notarization ticket. Gatekeeper requires both Developer ID
and notarization for unsigned-download apps to open without a warning.

### Registration / cost

- **Apple Developer Program**: US$99/year (renewed annually).
  - Individual or Organization account (organization needs a D-U-N-S number).
- Developer ID Application certificate: created in the Apple developer portal
  (one per team; Apple issues only one signing cert at a time, use a Mac / CI
  keychain or a remote signing service).
- Notarization: free, part of the Developer Program.
- **Hardware**: signing traditionally requires macOS (`codesign`, `notarytool`).
  Use `macos-latest` runners in CI (cost: 10× Linux runner minutes on
  GitHub-hosted macOS) or a remote signing service (e.g. `Apple notarytool` on
  a Mac mini, or a signing SaaS).

### Steps

1. Add `macos-latest` to the release matrix and build `dist:mac` for the
   Electron desktop app (and the PyInstaller CLI for macOS if we ship it).
2. `codesign --options runtime --timestamp --sign "Developer ID Application:"
   --deep` on the `.app`, then build the `.dmg`, then `codesign` the `.dmg`.
3. `xcrun notarytool submit --wait` (App Store Connect API key) and
   `xcrun stapler staple` the `.dmg`.
4. Store the Apple API key (`AuthKey_*.p8`) in GitHub secrets; keep the
   certificate in the CI keychain via a base64 secret + `security import`.

## 7. Linux (.deb / .rpm / .tar.gz)

### What is needed

OpenPGP (**GPG**) signatures, verified against a keyring the admin imports.
There is no central registry: trust is decided machine-by-machine.

- `.deb`: `dpkg-sig --sign builder` or `debsigs`; verify with `debsig-verify`
  (needs a policy in `/etc/debsig/policies`). Simpler and common: put a
  `Release` file signed by GPG in an apt repo, or ship the key in the
  installer and instruct `apt-key add` / `trusted.gpg.d` import.
- `.rpm`: `rpmsign --addsign` (needs the GPG key in a keyring rpm can read);
  verify with `rpm --checksig`; in `dnf` repos the `repomd.xml.asc` is
  signed.
- `.tar.gz` / `.zip`: detached GPG signatures (`gpg --detach-sign --armor`)
  plus a `SHA256SUMS` file signed by the same key.

### Registration / cost

- **None required and free**: generate a dedicated GPG key (ed25519 subkey for
  signing). No payment, no approval.
- Optional distribution channels that host and sign for you:
  - Ubuntu **PPA** (Launchpad): free, key managed by Launchpad, apt trusts it.
  - Fedora **COPR**: free, same model.
  - openSUSE **OBS**: free.
  These require a free account and per-repo review, but they give users a
  pre-trusted apt/dnf source.
- Direct distribution: publish the public key on the website, in the installer
  scripts (`install-pawflow.sh` can import it), and as a `keyring.gpg` file
  packaged with the deb/rpm.

### Steps

1. Generate `pawflow-releases@...` GPG key (offline master + signing subkey).
2. Sign `.deb` with `dpkg-sig` after `dpkg-deb --build`.
3. Add `rpmbuild` + `rpmsign` support to the build (we do not build .rpm yet).
4. Sign every `.tar.gz`/`.zip` and the `SHA256SUMS` file.
5. Publish the public key at a stable URL and embed the fingerprint in docs
   and in `install-pawflow.sh`.

## 8. Auxiliary hardening

- **Checksums**: `SHA256SUMS` + signed detached file for every release.
- **Reproducible builds**: extend the existing byte-for-byte reproducibility
  used for bundled PFP builds to the CLI binaries so builds can be verified.
- **SBOM**: optional; generates trust and helps enterprise adoption.
- **Sigstore/cosign** (alternative for Linux containers/artifacts): free, no
  key management, but different trust model (keyless, Fulcio) — not a
  replacement for Authenticode/Developer ID/notarization.

## 9. Key separation (security)

| Key | Purpose | Store |
|---|---|---|
| PFP Ed25519 key (`PAWFLOW_PFP_SIGNING_KEY`) | .pfp packages, TOFU | CI secret |
| Windows Authenticode / Azure Trusted Signing | .exe / NSIS / MSI | Azure HSM or cert token |
| Android release / upload key | .apk / .aab | Offline backups + protected GitHub Environment / Play HSM |
| Apple Developer ID + notary API key | .app / .dmg / .pkg | CI keychain + GitHub secret |
| GPG `pawflow-releases` | .deb / .rpm / archives / checksums | Offline master + CI signing subkey |

Keep each key separate. Rotate on leak; revoke via CA/Apple/GPG revocation as
appropriate. Add timestamping everywhere so old signatures survive expiry.

## 10. Rollout phases

### Phase 0 — Foundations (no cost)
- Add GPG signing of `.tar.gz`/`.zip` + signed `SHA256SUMS`.
- Publish the GPG public key + fingerprint on the website and installer.
- Add CI signature verification jobs.

### Phase 1 — Linux packages (no cost)
- Sign `.deb` with `dpkg-sig`; add `.rpm` build + `rpmsign`.
- Optional: Launchpad PPA / COPR for pre-trusted sources.

### Phase 2 — Android (no recurring signing cost)
- Perform the two-custodian key ceremony and verify offline backups.
- Add fail-closed Gradle release signing and a protected GitHub Environment.
- Verify package metadata, non-debuggable status, schemes, and certificate
  fingerprint before the release job can upload the APK.
- Optionally register Google Play Console and enable Play App Signing.

### Phase 3 — Windows (paid)
- Decide Azure Trusted Signing (~US$10/month) vs OV/EV cert (~US$100–600/yr).
- Define a complete PE signing manifest for PawCode, Relay Desktop, native
  helpers, and `frpc.exe`.
- Sign and timestamp inner payloads before packaging, then sign the final
  installer; verify both layers in CI.
- Add FRP release provenance and Microsoft false-positive submission to the
  mandatory release checklist.

### Phase 4 — macOS (paid)
- Apple Developer Program (US$99/yr), macOS CI runner.
- Developer ID + notarization + stapling for the Electron desktop app.

### Go/no-go
- Phase 0–1: go (free, low risk).
- Phase 2: go after assigning key custodians and testing recovery.
- Phase 3: go when we accept the recurring cost and pick a provider.
- Phase 4: go when macOS artifacts become a supported deliverable.

## 11. Cost summary

| Item | One-time | Recurring |
|---|---|---|
| GPG signing | — | Free |
| Launchpad PPA / COPR / OBS | — | Free |
| Android direct APK signing | key ceremony and secure backups | Free |
| Google Play Console (optional) | US$25 registration | — |
| Azure Trusted Signing | identity validation | ~US$10/month |
| OV code-signing cert | — | ~US$100–300/yr |
| EV code-signing cert | token | ~US$300–600/yr |
| Apple Developer Program | — | US$99/yr |
| macOS CI runner | — | ~10× Linux runner cost |

## 12. Open decisions

1. Android: direct APK only or Google Play distribution as well.
2. Android: name the two release-key custodians and protected-environment
   reviewers.
3. Windows: Azure Trusted Signing vs OV/EV certificate.
4. macOS: is a native macOS build in scope for 1.0?
5. Linux: direct GPG distribution vs PPA/COPR hosting.
6. Whether to add `.rpm` to the release matrix.
7. FRP provenance: reproducible source build in PawFlow CI vs verified upstream
   binary followed by PawFlow Authenticode signing.
