# Third-Party Notices

PawFlow Docker images bundle third-party operating system packages, language runtimes, package-manager dependencies, and developer tools. This file summarizes the redistribution posture for the public images built by `.github/workflows/docker-publish.yml`.

This notice is not a substitute for the generated SBOM. Release review must inspect the SBOM/provenance metadata produced for each image.

## Public Images

PawFlow publishes these redistributable images:

- `ghcr.io/allcolor/pawflow`
- `ghcr.io/allcolor/pawflow-relay-minimal`
- `ghcr.io/allcolor/pawflow-relay-dev`

The full relay image intentionally uses Playwright-managed Chromium instead of Google Chrome and does not install Microsoft Visual Studio Code desktop. `code-server` is used for browser-based editor support.

## Image Not Published

PawFlow does not publish `pawflow-claude-code:latest`. That image is built locally because it installs Claude Code and Antigravity binaries whose redistribution terms are not suitable for a public PawFlow image.

## License Families Present

The public images may include software under, among others:

- MIT
- Apache-2.0
- BSD-style licenses
- Python Software Foundation License
- LGPL-family licenses for selected libraries
- GPL-family licenses for selected Ubuntu/Debian packages and command-line tools
- Ubuntu/Debian package copyright and trademark notices

The project source code is licensed separately under the repository license. Third-party packages retain their own licenses and notices.

## Release Checklist

Before making a Docker release public:

1. Confirm the workflow builds only the three public images listed above.
2. Confirm no public image installs Google Chrome, Microsoft Visual Studio Code desktop, Claude Code, or Antigravity.
3. Review SBOM/provenance output for unexpected proprietary packages.
4. Keep this notice and the repository license reachable from the package source URL.
5. If a new binary installer or package repository is added, verify its redistribution terms before publishing the image.
