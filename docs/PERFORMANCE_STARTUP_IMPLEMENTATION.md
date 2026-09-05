# Startup performance implementation

Date: 2026-09-05. Scope: startup findings in PERFORMANCE_AUDIT_BETA269.md.

## Delivered behavior

- RxJS 7.8.2 and highlight.js 11.9.0, including the highlight theme, are pinned local assets. Both scripts use classic-script `defer` and precede their consumers. The application module order is preserved. The existing asset failure/reload handler is installed before vendor requests can fail; highlight configuration and initial code highlighting run at DOMContentLoaded.
- JavaScript, CSS, locale JSON, vendors and the final component-contract stylesheet use individual SHA256 content hashes, truncated to 16 hexadecimal characters, in their URLs. The eight-character aggregate `PAWFLOW_ASSET_VERSION` remains available to the existing SSE hotpatch probe. Template changes still change that aggregate without invalidating unrelated asset URLs.
- A process-local manifest shares a metadata snapshot for one second. Unchanged files are not reread or rehashed; metadata includes mtime, size, ctime and inode. `_invalidate_asset_cache()` forces immediate byte verification. Automatic discovery still operates after the one-second window. Missing files use an explicit `None` timestamp sentinel: zero and negative timestamps, including empty assets, remain valid and receive content hashes.
- Initial HTML embeds English plus the selected locale, rather than all three catalogs. The server chooses the language cookie before Accept-Language, including quality weights and supported-language validation. The browser preference order is supported localStorage preference, supported server `PAWFLOW_I18N_LANGUAGE`, then navigator language. An absent or inaccessible localStorage does not overwrite a cookie-only preference.
- Additional catalogs use asynchronous fetch with their own content hashes. In-flight requests are deduplicated; successful catalogs are retained for the page lifetime. The latest language choice wins, including choosing the currently displayed language while another request is pending. Failure retains the current language and permits retry. An old localStorage preference can fetch during boot, but app surface refresh waits until DOMContentLoaded and cannot override a newer user choice. There is no synchronous XHR.
- Only the usage dashboard is loaded on demand. Its single external opening entry point, `showUsageDashboard()`, is provided by a 37-line loader. Concurrent opening clicks share a request, boot dependencies finish before opening, failed downloads report an error and permit retry, and reopening does not download again. A script that fails to define its entry point uses the existing page reload recovery. The existing guarded budget SSE hook remains functional before and after opening.

Admin settings, resource editors, cognitive panels and OpenSpace remain in the ordered eager chain. Admin settings participates in startup button/update handling and SSE progress; the larger feature groups share globals, registration side effects and multiple command/UI entry points. Deferring them without changing their owners would require broader dependency work. No HTTP route, state, session, history or OpenSpace source was edited by this startup task.

## Measured source payload and cache behavior

Measurements use actual rendered HTML and referenced source bytes from the shared MyWorkspace checkout. Gzip uses individual deterministic local compression; it does not establish actual edge compression, browser transfer sizes, phone startup latency or production cache-hit rates. Favicons and logos are excluded from the table. Vendor bytes are included on both sides so moving them from CDN to local storage is not counted as a saving.

| Measurement | Before | After |
| --- | ---: | ---: |
| English HTML | 379,764 bytes | 162,075 bytes |
| English HTML, gzip estimate | 106,235 bytes | 43,246 bytes |
| French HTML | 379,764 bytes | 272,972 bytes |
| Spanish HTML | 379,764 bytes | 271,230 bytes |
| HTML plus initial JS/CSS, including vendors | 2,996,783 bytes | 2,765,547 bytes |
| Same combined payload, gzip estimate | 804,877 bytes | 738,141 bytes |
| Warm HTML generation, 20-call median | 10.848 ms | 1.604 ms |
| Blocking external vendor scripts | 2 | 0 |

English HTML shrinks by 217,689 bytes (57.3%). The shared checkout changed `messages_render.js` during measurement, adding 1,031 raw bytes and 334 gzip bytes outside this task. Holding that unrelated file at its baseline gives a startup-only combined reduction of 232,267 raw bytes (7.75%) and 67,070 gzip-estimated bytes (8.33%). The new local vendors total 211,102 raw bytes; initial local JS/CSS references rise from 126 to 129 because those three previously external resources are now local.

The cache experiment copies the final source tree into a ScratchDir fixture and modifies only its i18n.js. Under the previous shared-version algorithm applied to that same final tree, the edit invalidates all 129 initial JS/CSS URLs (2,603,496 bytes including the probe comment). Individual content hashes invalidate one URL (10,554 bytes). A template-only fixture edit changes the aggregate reload signal and changes zero individual asset URLs. These are deterministic cache-key results, not measured browser network savings.

## Regression evidence

Dedicated tests:

- `tests/test_chat_startup.py`: 18 cases for actual script order, content digests, cache scan/read coalescing, concurrent access, preserved-mtime edits, explicit invalidation, template reload signals, locale selection, cookie/header precedence and safe inline JSON. Five cases also cover epoch/negative timestamps with empty and nonempty assets, missing/deleted/recreated modules, and complete page rendering from a tar archive with epoch timestamps.
- `tests/test_chat_startup_browser.py`: 12 real-Chromium cases for actual local vendors and RxJS consumers, selected locale boot, localStorage migration, absent/denied localStorage with a server cookie preference, an early user choice superseding migration, asynchronous request races/cache reuse/cancellation, failed locale requests and retry, dashboard loading/retry/reopening/budget events, parse-failure recovery, and the complete shipped classic-script chain with an isolated backend fixture.
- Python compilation and Node syntax checks passed for the modified Python/JavaScript and downloaded vendor scripts.

The dedicated startup cases passed. A combined local run with existing template/cognitive checks produced 49 passes and two obsolete static assertions requiring a single shared eight-character asset key. Root owns updating those assertions and broader integration. Root subsequently reported 61 passes for test_performance_server_paths, test_chat_startup and test_chat_startup_browser at approximately 13:35 UTC. Full-suite and wheel/build gates are owned by root.

Evidence in the startup agent's ScratchDir: `before.json`, `before.html`, `after.json`, `after.html`, `report.json`, `startup_metrics.py`, `startup_report.py`, `startup-reviewed.log` and `source-hashes.sha256`. Scripts operate on synthetic/copy fixtures and source payloads, not live conversation data.

## Atomic activation and restart requirement

**Activate the new Python renderer, Jinja templates, i18n code, startup loader and vendor files together through a coordinated restart. Do not copy these templates into an old running renderer as a standalone hotpatch.** The templates require new Python context keys, including `asset_versions`, `i18n_urls` and `lazy_urls`. Jinja auto-reload can observe the new templates while the old imported Python module still lacks those keys, causing StrictUndefined render failures. Updating Python files on disk does not update an installed/imported module's `_JS_MODULES`, helper functions or renderer context.

Stage the complete file set, activate it using the release/restart procedure, and verify rendered HTML and all referenced static assets from the newly started process. Existing open pages retain their previously loaded scripts; the existing aggregate-version probe supplies the normal reload signal. If an integration-specific in-process reload is used instead, root must explicitly ensure every renderer reference and module list is replaced before the template switch. Merely invalidating the manifest does not replace imported Python code.

After activation, ordinary asset/template content hotpatch discovery remains supported within one second, with explicit manifest invalidation for immediate verification. Atomic activation is still required when changing the renderer/template contract or Python module list.

The first integrated hotpatch exposed an additional deployment regression: `TarInfo` entries default to an epoch timestamp, and the manifest incorrectly treated that valid zero value as a missing asset. A fresh authenticated page load failed although the health endpoint and file digests passed. The original files were restored before correcting the missing-file sentinel. Regression tests must render the extracted archive, and live acceptance must reload the authenticated page and verify the actual referenced asset bytes; an HTTP 200 login page is not asset validation.

Integration also aligns the HTTP byte cache in `services/_http_request.py` with the manifest's mtime, size, ctime and inode signature. Rewriting or atomically replacing a file while preserving mtime and size now refreshes the served bytes instead of associating an old cached body with a new asset URL. `tests/test_http_listener.py` covers both operations, warm-cache read reuse and HEAD responses; both new cases failed against the previous cache signature. This Python change activates through the same coordinated restart.

This task did not hotpatch, restart, commit, push or deploy. HTTP compression/content negotiation and actual edge-header verification remain coordinated with root/Claude, who own the HTTP serving path. The estimates above do not claim that gzip is already served.

## Vendored provenance

- RxJS bundle: https://cdn.jsdelivr.net/npm/rxjs@7.8.2/dist/bundles/rxjs.umd.min.js
- RxJS Apache 2.0 license: https://cdn.jsdelivr.net/npm/rxjs@7.8.2/LICENSE.txt
- highlight.js bundle: https://cdnjs.cloudflare.com/ajax/libs/highlight.js/11.9.0/highlight.min.js
- highlight theme: https://cdnjs.cloudflare.com/ajax/libs/highlight.js/11.9.0/styles/github-dark.min.css
- highlight BSD 3-Clause license: https://cdn.jsdelivr.net/npm/@highlightjs/cdn-assets@11.9.0/LICENSE

The licenses are stored alongside the vendor files. Downloaded JavaScript syntax was verified before the files were copied into the workspace.

## Reviewed source SHA256 manifest

These hashes identify the startup source/test artifacts at handoff. The documentation file's own hash is recorded separately in the final handoff manifest.

| Path | SHA256 |
| --- | --- |
| tasks/io/serve_chat_ui.py | 141b73d71259135348d82e6b46cdda8c73f441f5ab1a407682814146df815ea8 |
| tasks/io/chat_ui/templates/head/vendor.html | 706435c8ed441b54162fd31c4bcad844af6e29996a98ee5aa4fc608112b7705f |
| tasks/io/chat_ui/templates/head/styles.html | b6f53300e6632346202718b52c5b537f6fec7bcba611a6cd821fbe3b922db900 |
| tasks/io/chat_ui/templates/boot/scripts.html | bf5555368b0d48ad720788a8c8ee7b7adc511a9e2a061f6ab871970162ffebe6 |
| tasks/io/chat_ui/templates/chat.html | 8040fb200bd5bad3076f1bcacc447346dd07e8592b957a716a3776254cf895b0 |
| tasks/io/chat_ui/i18n.js | 5badc09e2e12a6b851056599460fbba670ac9c7bcb2f3224e11c8c382d11b46a |
| tasks/io/chat_ui/startup_optional.js | e006e0a3b52fb28aa3fc393250aafc3e508628bc3f266effb5be628256d99a8e |
| tests/test_chat_startup.py | 82113b458023b344e82ed7d3acf440e60fb603dcef0b671e47fd1178d1b9093c |
| tests/test_chat_startup_browser.py | b2532f28c376cdfe307aab251ce71901120689319d2fe257b6d7199f62b9a256 |
| tasks/io/chat_ui/vendor/rxjs-7.8.2.umd.min.js | 2152e8a794982170a4c1dae32a74e31a81218fd74781c27b0d628a02bf532413 |
| tasks/io/chat_ui/vendor/highlight-11.9.0.min.js | 837a6fa5b0c736b52bbde2b2b6190f305da3fc9ed41681db5321507057b5c846 |
| tasks/io/chat_ui/vendor/github-dark.min.css | 9f208d022102b1d0c7aebfecd8e42ca7997d5de636649d2b31ea63093d809019 |
| tasks/io/chat_ui/vendor/rxjs-LICENSE.txt | 81c407ac717813b0e3795402960e04003c7bba8ba59b621624707028531c9ade |
| tasks/io/chat_ui/vendor/highlight-LICENSE.txt | 6c081431591d9df696c82dc598fe1423765b8a299b200ed00b281afd0f64c490 |
