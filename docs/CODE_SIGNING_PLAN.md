# Code Signing Plan — PawFlow / PawCode / Relay artifacts

Status: **draft** — decision plan, nothing implemented yet

## 1. Purpose

PawFlow currently ships unsigned binaries and packages. This plan defines how to
sign every distributable artifact so end users get fewer scary warnings, and so
malicious builds of our software are easier to detect. It covers:

- Windows executables and installers (`setup.exe`, `pawcode.exe`)
- macOS applications and disk images (`.app`, `.dmg`, `.pkg`) — **not built yet**
- Linux packages (`.deb`, `.rpm`) and archives (`.tar.gz`, `.zip`)
- Checksums and detached signatures for every artifact

It deliberately does **not** reuse the PFP Ed25519 signing key (see §8).

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
| `pawflow-installers/*` | `build-pawflow-install-zip.sh` | Linux | No |

No `.rpm` and no macOS artifacts are produced today. No signing infrastructure
exists anywhere (no Authenticode, no GPG, no notarization).

## 3. Why one key cannot sign everything

Each OS trusts signatures through a different mechanism:

| Platform | Trust anchor | Verifier |
|---|---|---|
| Windows | CA root store (Authenticode) or Microsoft (Azure Trusted Signing) | SmartScreen, kernel, `signtool verify` |
| macOS | Apple Developer Program (Developer ID + notarization) | Gatekeeper |
| Linux | Local GPG keyring imported by the admin | `apt`/`dpkg`, `dnf`/`rpm` |
| PawFlow .pfp | Ed25519 public key embedded in manifest (TOFU) | PawFlow itself |

The PFP key is a raw Ed25519 key verified only by PawFlow (trust-on-first-use).
Windows and macOS require X.509 certificates from a CA they trust; Linux uses
OpenPGP. **One key cannot satisfy all four**, and reusing the PFP key for OS
binaries would widen the blast radius of a single leak.

## 4. Windows (.exe / NSIS installer)

### What is needed

An **Authenticode** signature on the `pawcode.exe` binary and the NSIS
`setup.exe`, applied with `signtool` (Windows SDK) or `osslsigncode` (cross,
on Linux CI), plus an **RFC 3161 timestamp** so the signature stays valid after
the certificate expires.

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

### Steps (CI, GitHub Actions)

1. Store certificate/key in `AZURE_*` secrets (Trusted Signing) or import the
   PFX (OV/EV) into a protected secret; keep the EV token in a hardware module
   accessed by the runner (e.g. via a signing service), never in the repo.
2. Sign `pawcode.exe` before NSIS packaging, then sign `setup.exe` after.
3. Add `/tr http://timestamp.digicert.com /td SHA256` (or the CA's RFC 3161 URL).
4. Verify with `signtool verify /pa /v` in CI.
5. Add a workflow that checks signatures on release artifacts.

## 5. macOS (.app / .dmg / .pkg)

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

## 6. Linux (.deb / .rpm / .tar.gz)

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

## 7. Auxiliary hardening

- **Checksums**: `SHA256SUMS` + signed detached file for every release.
- **Reproducible builds**: extend the existing byte-for-byte reproducibility
  used for bundled PFP builds to the CLI binaries so builds can be verified.
- **SBOM**: optional; generates trust and helps enterprise adoption.
- **Sigstore/cosign** (alternative for Linux containers/artifacts): free, no
  key management, but different trust model (keyless, Fulcio) — not a
  replacement for Authenticode/Developer ID/notarization.

## 8. Key separation (security)

| Key | Purpose | Store |
|---|---|---|
| PFP Ed25519 key (`PAWFLOW_PFP_SIGNING_KEY`) | .pfp packages, TOFU | CI secret |
| Windows Authenticode / Azure Trusted Signing | .exe / NSIS / MSI | Azure HSM or cert token |
| Apple Developer ID + notary API key | .app / .dmg / .pkg | CI keychain + GitHub secret |
| GPG `pawflow-releases` | .deb / .rpm / archives / checksums | Offline master + CI signing subkey |

Keep each key separate. Rotate on leak; revoke via CA/Apple/GPG revocation as
appropriate. Add timestamping everywhere so old signatures survive expiry.

## 9. Rollout phases

### Phase 0 — Foundations (no cost)
- Add GPG signing of `.tar.gz`/`.zip` + signed `SHA256SUMS`.
- Publish the GPG public key + fingerprint on the website and installer.
- Add CI signature verification jobs.

### Phase 1 — Linux packages (no cost)
- Sign `.deb` with `dpkg-sig`; add `.rpm` build + `rpmsign`.
- Optional: Launchpad PPA / COPR for pre-trusted sources.

### Phase 2 — Windows (paid)
- Decide Azure Trusted Signing (~US$10/month) vs OV/EV cert (~US$100–600/yr).
- Sign `pawcode.exe` + `setup.exe` with timestamping; verify in CI.

### Phase 3 — macOS (paid)
- Apple Developer Program (US$99/yr), macOS CI runner.
- Developer ID + notarization + stapling for the Electron desktop app.

### Go/no-go
- Phase 0–1: go (free, low risk).
- Phase 2: go when we accept the recurring cost and pick a provider.
- Phase 3: go when macOS artifacts become a supported deliverable.

## 10. Cost summary

| Item | One-time | Recurring |
|---|---|---|
| GPG signing | — | Free |
| Launchpad PPA / COPR / OBS | — | Free |
| Azure Trusted Signing | identity validation | ~US$10/month |
| OV code-signing cert | — | ~US$100–300/yr |
| EV code-signing cert | token | ~US$300–600/yr |
| Apple Developer Program | — | US$99/yr |
| macOS CI runner | — | ~10× Linux runner cost |

## 11. Open decisions

1. Windows: Azure Trusted Signing vs OV/EV certificate.
2. macOS: is a native macOS build in scope for 1.0?
3. Linux: direct GPG distribution vs PPA/COPR hosting.
4. Whether to add `.rpm` to the release matrix.
