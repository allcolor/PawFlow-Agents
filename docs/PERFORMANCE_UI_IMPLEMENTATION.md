# UI performance implementation

This change implements the measured streaming-render and OpenSpace visibility findings from `PERFORMANCE_AUDIT_BETA269.md`.

## Streaming

Token text, message identity, preview provenance and speech ingestion are updated immediately. Markdown, turn detail projection and scroll work are coalesced into one render per animation frame, with a 50 ms fallback timer when frames are suspended. Browser timer throttling can extend that delay in a hidden tab. Message-ID rotation and semantic/terminal SSE boundaries flush pending text synchronously before the existing handler changes ownership or reconciles the durable message. The persisted text remains authoritative. Session release cancels outstanding rendering callbacks; scheduled work captures its conversation session.

`tests/js/stream_render_spec.js` runs ten deterministic scenarios against the actual stream-state functions and token handler: burst accumulation, paused frames, ID rotation, durable correction, turn completion, terminal/tool boundaries, deletion, retirement, session isolation and duplicate suppression. `tests/test_webchat_performance_browser.py` compares the real Markdown renderer's final HTML in Chromium and checks OpenSpace activity across viewport and tab visibility transitions. These tests measure avoided work and correctness; they do not claim a particular production frame rate.

## OpenSpace

An IntersectionObserver measures the actual OpenSpace viewport, independently of focus. Hidden surfaces retain their scene, flow selection and incoming event state but cancel their animation frame and flow-poll timer. Becoming visible resumes animation and requests one fresh flow snapshot, retaining the existing overlap guard. Hidden browser tabs follow the same suspension path. The observer starts only after the scene modules are ready and disconnects on deactivation. IntersectionObserver detects viewport intersection, not occlusion: another maximized tile, modal or window covering OpenSpace can leave it rendering and polling.

## Retained live messages

The existing automatic live-window trim now counts nested message identities instead of only top-level wrappers. It preserves entire live groups, the newest group, selected nested messages and browser text selections, and retains the existing cursor rewind and dedup-identity cleanup. Detail-mirror identities inside a wrapper do not multiply its weight. This is a bound on eligible completed messages while autoscrolling, not a hard byte/DOM cap: a protected group or manually browsed history can exceed it. Bidirectional history virtualization remains a separate navigation change requiring scroll and selection measurements; no history is silently dropped while the user reads older pages.

## Deployment validation

Before hotpatching, complete cross-review with Claude and integrated regression checks. Static files must match the reviewed SHA256s in the running installation; reload an authenticated browser page and exercise streaming, stop/reconcile, concurrent conversations, OpenSpace scrolling and the existing composer/maximize fixes. Server and startup Python changes require their own activation check. Release follows only after the combined hotpatch validation succeeds.
