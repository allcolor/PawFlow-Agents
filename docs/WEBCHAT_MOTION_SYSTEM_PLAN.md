# PawFlow WebChat Motion and Interaction Performance Plan

Status: **implemented, locally validated, and hotpatched into beta.264 on 2026-09-04, including the follow-up disclosure, sidebar-accordion, chrome-expander, and tile/full-tile fluidity refinements**.

## Decision

PawFlow should keep its current visual identity and make interaction feel continuous, responsive, and interruptible.

The implementation will reproduce the useful behavioral invariants demonstrated by [ddoemonn/interior at commit `52988dcc82c9ef2c21bc6b288207a2b850f9b318`](https://github.com/ddoemonn/interior/tree/52988dcc82c9ef2c21bc6b288207a2b850f9b318), but it will not import React, Next.js, or the `motion` package. PawFlow already serves ordered standalone JavaScript and CSS modules, so the correct fit is a small native DOM, CSS, and Web Animations API layer.

This is not an animation-only project. The current visible snap is partly caused by `display:none/block`, but the most serious pauses come from synchronous layout, complete projection cloning during streaming, whole-surface rendering, and layout-bound CSS transitions. Those costs must be removed before motion is added.

The order is therefore:

1. measure and freeze the current behavior;
2. remove main-thread work from input paths;
3. add one shared motion policy and a few narrow controllers;
4. migrate each surface atomically and delete its old path;
5. enforce performance, accessibility, reduced-motion, and lifecycle gates in a real browser.

There will be no legacy motion mode, compatibility adapter, framework migration, or long-lived feature flag.

## User outcome

A user must be able to open or close a Resources node, turn detail, sidebar, menu, dialog, or workflow card and immediately see the interface respond. Streaming in another part of the page must not make that interaction pause.

The result is successful when:

- an interaction acknowledges the click on the next paint;
- opening a surface never waits for a network request or a complete rerender;
- opening and closing are smooth, reversible, and safe under repeated clicks;
- content that changes while opening retargets without jumping;
- the same final state and accessibility semantics exist with reduced motion;
- no observer, listener, timer, animation, or detached DOM subtree survives its owner;
- themes, spacing, typography, colors, and the existing visual hierarchy remain unchanged.

## Scope

### In scope

- simplified-turn headers, detail panels, tabs, and live cue surfaces;
- Resources sidebar accordion and its expandable repository/resource sections;
- real hierarchical resource rows that behave as tree views;
- native `<details>` surfaces used for thinking, tasks, delegates, tool results, and workflow metadata;
- filtered transcript tabs and the OpenSpace transcript projection;
- technical message grouping and other transcript reconciliation work triggered by streaming;
- desktop and mobile sidebar open/close behavior;
- workspace tile-to-full-tile and full-tile-to-tile layout changes;
- tooltips, dropdowns, context menus, popovers, overlays, and dialogs;
- WorkflowRun Kanban cards, drawer, and run inspector updates;
- loading/action buttons, progress indicators, local task-step state transitions, and toasts;
- live cue timers, canvas rain, temporary `will-change`, and offscreen work;
- normal-motion and reduced-motion behavior;
- unit, DOM, accessibility, lifecycle, geometry, screenshot, and browser-performance coverage.

### Out of scope

- changing PawFlow's visual design or theme palette;
- adopting React, Next.js, `motion`, or another UI framework;
- rewriting the full WebChat;
- changing agent, task, workflow, SSE, or resource backend semantics;
- decorative animation that does not communicate state or continuity;
- animating every visibility change indiscriminately;
- preserving the old implementation beside the new one.

## Evidence and current baseline

The comparative audit found one animation dependency in `interior`, but its useful ideas are behavioral: measured and cancellable disclosure, stable button faces, local step transitions, shared tooltip timing, bounded floating-layer placement, keyboard navigation, focus restoration, and centralized reduced-motion behavior.

PawFlow's eight directly relevant suites currently pass **97 tests in 4.69 seconds**. They protect structure and behavior but do not measure layout, paint, long tasks, interaction latency, animation interruption, or resource cleanup. This green run is the functional baseline, not proof that the UI is responsive.

The Playwright dependency already exists in `requirements.txt`. The implementation must add a real Chromium gate rather than relying only on string assertions or the lightweight Node DOM harness.

## Root-cause inventory

| Priority | Surface | Current mechanism | Why it feels blocked | Required correction |
|---|---|---|---|---|
| P0 | Simplified turn details | `.expanded` changes details from `display:none` to `display:block`; the same path activates tabs and reads `scrollHeight` | The click reveals and lays out a potentially large subtree before it can paint | Separate logical state from motion, batch reads before writes, and anchor scrolling after the opening paint |
| P0 | Filtered transcript tabs | Every observed mutation clears every projection and deep-clones all matching source rows | A single streamed character can clone the full transcript | Reconcile only dirty top-level rows by stable message/turn identity and suspend hidden projections |
| P0 | OpenSpace transcript | Every mutation clears and clones the entire transcript | Streaming competes continuously with input and animation | Use the same keyed projection reconciler and stop observing when the projection is not visible |
| P0 | Resources inner sections | `_toggleSection` flips `display`, rerenders cached data, and can start a debounced multi-request refresh | Opening is coupled to cache rendering, disk/network work, and eventual root `innerHTML` replacement | Keep section DOM mounted, open immediately, and make refresh an independent, deduplicated data operation |
| P1 | Resources root | Four requests converge, variables/secrets load later, then `resourcesContent.innerHTML` may be replaced | Focus, scroll, disclosure state, and DOM identity are destroyed by whole-root replacement | Split renderers by section and patch keyed sections/rows in place |
| P1 | Technical grouping | Insertions can scan and reparent the full transcript and nested scopes | Streaming cost grows with transcript size | Reconcile only the affected top-level turn/group |
| P1 | Sidebar | `width`, `min-width`, and `flex-grow` transition on each frame | Layout propagates through the application shell | Commit layout once and animate a FLIP transform; preserve the mobile drawer contract |
| P1 | Workspace tile/full-tile changes | Tile width, height, and position variables are replaced atomically | Every mounted surface visibly snaps to its new geometry | Capture all surface rectangles, commit once, then run one interruptible group FLIP with translation and scale |
| P1 | Workflow Kanban and inspector | Card disclosure rebuilds the board; inspector refresh replaces run/detail HTML | A local state change causes whole-surface churn | Patch the changed card, lane, drawer, run row, or detail region only |
| P2 | Tooltips and menus | Each controller appends, measures, clamps, and owns document listeners separately | Repeated layout work and duplicated lifecycle logic | Share placement scheduling, outside interaction, Escape handling, focus restoration, and cleanup |
| P2 | Live turn cues | Canvas sizing reads layout; text scrambling and rain use timers; blur filters and retained `will-change` add paint cost | Active visual work competes with clicks | Cache size with `ResizeObserver`, pause offscreen work, share a visibility-aware scheduler, and clear hints after settling |
| P2 | Progress and view controls | Some indicators animate `left` or `width` | Layout occurs on every frame | Animate `translateX` or `scaleX` with a fixed geometry |
| P3 | Buttons and local status | Labels/icons are often replaced in normal flow | Dimensions shift and late async responses can regress state | Use stable stacked faces and generation-owned state changes |

The old plan-step `width` transition in `plans_panel.js` is not a production cause because that module is excluded when the page is rendered. It must not drive implementation priorities.

## Non-negotiable interaction contracts

### Immediate acknowledgement

An input handler may update lightweight state and classes, but it must not fetch, rebuild a surface, deep-clone a collection, or perform a layout read after a DOM/style write. Expensive work is scheduled after the next paint or removed through incremental reconciliation.

### State and motion are separate

Every controller has a logical state independent of whether an animation exists:

- `closed`
- `opening`
- `open`
- `closing`

The logical target is authoritative. Animation is only the visual path to that target. Rapid reversal retargets from the current visual value; it never queues a second full animation and never waits for the first one to finish.

### Accessibility state is never delayed by decoration

On open:

1. set `aria-expanded="true"`;
2. make the panel available;
3. remove `inert` and `aria-hidden`;
4. measure in the read phase;
5. animate in the write phase.

On close:

1. set `aria-expanded="false"`;
2. if focus is inside, restore it to the controlling trigger;
3. apply `inert` and `aria-hidden="true"` immediately;
4. animate the visible exit;
5. apply terminal `hidden` only after the animation settles.

Reduced motion performs the same state changes and cleanup without temporal animation.

### Stable geometry

Controls that change between idle, pending, success, and error keep a stable outer size. Text or icons may cross-fade/translate inside an overlay, but the surrounding layout must not move.

### Stable identity

Lists and projections use durable message, turn, task, run, resource, or node identifiers. Array indexes and visible labels are not keys. If the canonical DOM does not expose an existing UUID, the renderer must expose that UUID as a `data-*` attribute; it must not invent a second identity scheme.

### One owner, one teardown

Every controller owns exactly one `AbortController` and any observers, timers, requestAnimationFrame callbacks, and animations it creates. Replacing or removing the surface aborts the owner and returns all diagnostic counts to baseline.

## Proposed native architecture

The implementation adds four small classic-script modules and one CSS module. They remain narrowly scoped and are loaded before their consumers.

### `ui_motion.js`

Responsibilities:

- expose the current `prefers-reduced-motion` value and react to changes;
- batch layout reads, then DOM/style writes, in a shared frame;
- replace an animation on a named element/channel instead of stacking it;
- normalize finish, cancel, disconnect, and abort cleanup;
- pause registered decorative work when the document or owning surface is hidden;
- expose test-only counters when diagnostics are enabled.

It is not a component framework and does not render application content.

Suggested public surface:

- `pfMotion.reduced()`
- `pfMotion.read(callback, ownerSignal)`
- `pfMotion.write(callback, ownerSignal)`
- `pfMotion.replace(element, channel, keyframes, options, ownerSignal)`
- `pfMotion.whenSettled(element, channel)`
- `pfMotion.setSurfaceActive(owner, active)`

All animation generations are monotonic. A stale `finished` promise must not mutate the current state.

### `ui_disclosure.js`

Responsibilities:

- own the four-state disclosure machine;
- synchronize trigger, panel, ARIA, `inert`, focus, and terminal `hidden`;
- measure natural block size in the read phase;
- animate a clipped, contained wrapper through the Web Animations API;
- retarget when `ResizeObserver` reports a content-size change during opening;
- preserve or restore the correct scroll anchor;
- support immediate reduced-motion completion;
- provide one teardown path.

A bounded block-size animation is acceptable for a disclosure only when containment limits its layout scope and the browser trace passes the budget. Large application-shell transitions use FLIP transforms instead. No surface may combine a layout animation with a subtree rebuild.

### `ui_projection.js`

Responsibilities:

- map canonical top-level rows to projected rows by durable ID;
- consume `MutationObserver` records and derive a set of dirty owning rows;
- clone or hydrate only inserted/changed rows;
- remove deleted rows and reorder retained rows without clearing the projection;
- preserve per-row expansion and scroll state;
- reconcile a load-more proxy independently;
- disconnect while the destination surface is hidden;
- reject source/destination ownership changes from an old conversation generation.

A single character mutation may update at most the owning projected row for each visible projection. It must never clear or clone the whole projection.

### `ui_floating_layer.js`

Responsibilities:

- portal ownership for tooltips, menus, popovers, and non-modal floating layers;
- one scheduled geometry-read phase followed by one placement-write phase;
- viewport clamping and computed transform origin;
- open/close through `transform` and `opacity`;
- cancellation on pointer cancellation, blur, scroll, resize, Escape, and owner teardown;
- outside-click handling without delayed duplicate document listeners;
- focus entry and restoration appropriate to the surface;
- tooltip grouping so movement between adjacent controls does not restart the full delay.

Modal dialogs retain their focus trap and modal semantics but reuse motion policy, animation replacement, and teardown.

### `css/05_motion.css`

This file is inserted after `00_base.css` and before surface-specific modules. Initial tokens are explicit and must be tuned only from trace and usability evidence:

```css
:root {
  --pf-motion-fast: 120ms;
  --pf-motion-standard: 180ms;
  --pf-motion-disclosure: 300ms;
  --pf-motion-layout: 300ms;
  --pf-motion-exit: 140ms;
  --pf-motion-ease-standard: cubic-bezier(.2, .8, .2, 1);
  --pf-motion-ease-emphasized: cubic-bezier(.2, .9, .25, 1);
  --pf-motion-ease-exit: cubic-bezier(.4, 0, 1, 1);
}
```

The reduced-motion media query sets these temporal tokens to `0ms`. JavaScript also reads the media query so canvas, scrambling, delayed tooltip, and other timer-driven effects stop rather than merely slow down.

The module defines only motion state selectors and containment/wrapper rules. Theme colors and visual surface styling remain in their current modules.

## Rendering and scheduling rules

Each animation frame follows one direction:

1. consume events and update logical targets;
2. collect affected owners/keys;
3. perform all geometry reads;
4. perform all DOM/style writes;
5. let the browser paint;
6. run non-urgent rendering or network work later.

The following patterns are forbidden in migrated code:

- write a class/style/HTML value and then read `scrollHeight`, `clientWidth`, or a bounding rectangle in the same input task;
- clear a collection root because one descendant changed;
- start a resource fetch from a disclosure toggle;
- retain `will-change` after an animation settles;
- create one document-level listener per open/close cycle;
- use arbitrary sleeps or timeouts to make state appear synchronized;
- animate `left`, `top`, `width`, `min-width`, or `flex-grow` when a transform can express the same visual change;
- use `transition: all`;
- keep both old and new rendering paths behind a permanent branch.

## Surface behavior

### Simplified turns

`_turnSetExpanded` becomes a pure target-state request to the disclosure controller. It no longer exposes a large subtree, activates a tab, and synchronously scrolls in one click task.

The controller:

- stops the ephemeral cue surface without deleting unrelated DOM;
- reveals and measures the selected panel in a batched phase;
- animates the detail wrapper;
- anchors the selected panel at the newest row after the opening paint;
- retains the existing “follow only if already near the bottom” rule during streaming;
- reverses cleanly if the header is clicked during opening;
- restarts the live cue surface only after a working turn is logically collapsed.

Tab changes remain keyboard accessible and update only the selected tab/panel. A tab change inside an open turn does not replay the outer disclosure animation.

### Resources sidebar and inner sections

Opening Resources must be immediate even on a cold cache.

The data pipeline and visibility pipeline are separated:

- opening changes only the active sidebar section and disclosure target;
- cached section DOM remains mounted across close/open;
- initial load, explicit refresh, SSE refresh, and conversation change use one generation-owned request pipeline;
- concurrent identical requests are deduplicated;
- late generations cannot render into the focused conversation;
- variables/secrets join the data model without delaying unrelated sections;
- a cold section may show its existing loading state inside the already-open panel;
- disclosure animation restores the section's pre-existing inline height, opacity,
  and overflow declarations after settling, so bounded inner viewports such as
  Services keep wheel and trackpad scrolling instead of leaking it to Resources;
- root `resourcesContent.innerHTML` replacement is removed.

`resources_render.js` is already near the repository's 800-line ceiling. Incremental section rendering must be split by responsibility rather than expanding that file. Each section renderer owns a stable container and patches rows keyed by durable resource identity. A refresh preserves disclosure, focus, and scroll state.

Accordion headers become real buttons with `aria-expanded` and `aria-controls`; inline click-only spans are removed. True hierarchical repository rows use tree semantics and roving focus:

- Arrow Right opens a closed node or moves to its first child;
- Arrow Left closes an open node or moves to its parent;
- Arrow Up/Down move between visible tree items;
- Home/End move to the first/last visible item;
- Enter/Space perform the row's primary action.

Section accordions that contain arbitrary interactive controls do not pretend to be ARIA trees. They use disclosure semantics. Tree roles are reserved for actual hierarchical rows.

The desktop sidebar is a fixed-width shell outside the workspace layout. Opening
or closing translates that whole shell, including its grip and mounted content,
over 900 ms while the workspace position and width remain unchanged. Content is
never hidden during the slide, and rapid reversals start from the currently
painted position. Its Conversations/Resources accordion still interpolates both
bounded section bodies over 500 ms, so the upper body closes while the lower body
opens without a blank or snapped frame. The 300 ms workspace-tile motion remains
independent. Mobile retains a fixed drawer; its toggle and task rail remain
reachable throughout the state change.

The top header and bottom composer grips animate their panel height and opacity over the same balanced 500 ms path. Their existing `display: none` terminal states are applied only after closing settles, so neither expander snaps, and rapid reversals restart from the current visual height. The header and its top grip share a relative shell: the grip is anchored to the shell's animated block size, so layout itself keeps its visual center on the painted separation line without a second animation, observer lag, or hover-scale drift.

The independent desktop tab rail at the right edge uses the same balanced 500 ms path. Its buttons live in one counter-transformed content wrapper: the rail shell slides while the content keeps its final screen position, fades throughout both directions, remains visible until closing settles, and becomes non-interactive only in the terminal hidden state. This does not change the 300 ms workspace-tile motion or the mobile rail/sidebar coupling.

### Workspace layouts

Changing between a tiled layout and a full tile captures every mounted surface, commits the target grid once, and applies one replaceable group FLIP using translation and scale. Maximize and Restore therefore follow the same 300 ms path in both directions, rapid reversals begin from the current visual geometry, and reduced motion commits the identical final layout without temporal work.

### Transcript projections

Filtered task tabs and OpenSpace share `ui_projection.js`, but each supplies its own filter and hydration callback.

For every mutation batch:

- find the closest canonical top-level message/turn row for each record;
- deduplicate dirty keys;
- skip destinations that are not visible;
- clone/hydrate only dirty or inserted rows;
- remove explicit deleted keys;
- reorder existing projected rows only when canonical order changed;
- preserve the destination's stick-to-bottom decision made before writes.

Conversation switches destroy the old projection owner before binding the new source. Projection nodes remain read-only where they are currently read-only.

### Native details and other disclosures

Thinking, task, delegate, tool-result, metadata, scheduled-task, confirmation, and context-preview disclosures migrate surface by surface. Semantic `<details>/<summary>` may remain where it is valid, but close animation keeps the element logically owned until the visual exit ends. Invalid nested-interactive summary markup must be corrected instead of wrapped in compatibility code.

Small disclosures use measured block-size motion. Large panels use containment and, when necessary, FLIP. Each migrated surface deletes its direct `style.display` or competing transition path.

### Floating layers and dialogs

Tooltips, conversation menus, Resources menus, file menus, permission menus, popovers, and overlays share placement and lifecycle behavior.

Opening:

- mounts once in the portal;
- reads anchor/layer geometry in the shared read phase;
- clamps coordinates and computes transform origin;
- writes final placement;
- animates from a small translation/scale and opacity.

Closing:

- makes the layer non-interactive immediately;
- runs the exit animation;
- unmounts and restores focus after the current generation settles.

Menus support Escape, arrows, Home/End, and typeahead where applicable. Tooltips use `aria-describedby`, abortable delays, pointer cancellation, and grouped timing. A menu is not a modal and does not trap focus; a dialog is modal and does.

### Workflow views

Kanban disclosure updates only the selected card and drawer. It does not call a whole-board `render()`. Lane/card keyed nodes survive snapshot refreshes when their relevant fields are unchanged.

The run inspector patches its run list and active detail regions independently. Progress bars use a fixed track and `scaleX` from the left. Metadata disclosure uses the shared controller.

### Live cues, buttons, steps, and notifications

Canvas rain caches its dimensions with `ResizeObserver`, renders only while its owning turn is visible and active, and shares a visibility-aware frame scheduler. Text scrambling and blur are disabled under reduced motion and paused offscreen. `will-change` exists only from immediately before an animation until settle/cancel.

Loading and action buttons use a stable outer box with overlaid idle, pending, success, and error faces. A request/run generation owns each transition, so a late response cannot restore an older face.

Task-step transitions update only the affected icon, label, and connector. A status change never rerenders or reanimates the entire list.

Existing compositor-friendly toast motion may remain initially. It migrates only if lifecycle tests find timer/listener leaks or if traces show measurable contention.

## Implementation work packages

Each work package lands as a dedicated implementation change with tests and relevant documentation. PawFlow remains usable and green after every package.

### WP0 — Characterize and freeze the baseline

Files:

- existing relevant JavaScript/Python tests;
- new deterministic browser-performance fixture and test module;
- no production behavior change.

Work:

1. Preserve the 97-test functional baseline.
2. Build a 500-row and a 1,000-row transcript fixture containing long turns, thinking, tool calls/results, tasks, delegates, and active streaming character mutations.
3. Exercise simplified-turn, Resources, sidebar, filtered view, OpenSpace, Kanban, menu, and dialog interactions.
4. Record click-to-next-paint, long tasks, layout/update-layout-tree events, clone counts, mutation batch sizes, frame timing, and lifecycle counts.
5. Capture baseline screenshots and computed geometry for collapsed, expanding, and expanded states at desktop and mobile widths, with normal and reduced motion; exercise the real sidebar height exchange and tile/full-tile group-FLIP keyframes in Chromium.
6. Store only deterministic fixtures and summarized thresholds in the repository; do not commit machine-specific trace binaries.

Exit gate:

- functional baseline is reproducible;
- current expensive paths fail at least one new performance/clone assertion for the expected reason;
- the harness distinguishes a visual regression from a timing regression.

### WP1 — Add the shared motion foundation

Files:

- new `tasks/io/chat_ui/ui_motion.js`;
- new `tasks/io/chat_ui/ui_disclosure.js`;
- new `tasks/io/chat_ui/css/05_motion.css`;
- `tasks/io/serve_chat_ui.py`;
- focused Node DOM and Python asset-order tests;
- `docs/CHAT_UI_TEMPLATES.md`.

Work:

1. Add tokens, reduced-motion handling, read/write batching, replaceable animation channels, ownership, and cleanup.
2. Add the measured disclosure state machine and ResizeObserver retargeting.
3. Register modules before all consumers and include them in asset cache busting.
4. Add deterministic fake-rAF/fake-animation tests for open, close, reverse, abort, removal, resize, and reduced motion.
5. Expose diagnostic counters only when the test harness enables them.

Exit gate:

- no application surface has migrated yet;
- controller tests prove there is exactly one terminal state and no stale completion mutation;
- owner teardown returns all counters to baseline.

### WP2 — Remove critical main-thread blockers

Files:

- new `tasks/io/chat_ui/ui_projection.js`;
- `tasks/io/chat_ui/turn_view.js`;
- `tasks/io/chat_ui/task_tabs.js`;
- `tasks/io/chat_ui/openspace_scene.js`;
- `tasks/io/chat_ui/messages.js`;
- `tasks/io/chat_ui/messages_render.js`;
- corresponding JS and Python tests.

Work:

1. Move simplified-turn scroll measurement/write out of the click task.
2. Add stable top-level DOM keys where absent.
3. Replace full filtered/OpenSpace projection clearing with dirty-row reconciliation.
4. Disconnect projection observation while destinations are hidden.
5. Limit technical grouping/reparenting to the affected top-level owner.
6. Preserve load-more, turn expansion, hydration, and stick-to-bottom behavior.

Exit gate:

- one character mutation causes no whole-projection clone;
- hidden projections perform no clone work;
- a turn click contains no post-write synchronous layout read;
- current functional tests and new reconciliation tests pass.

### WP3 — Migrate simplified turns and common disclosures

Files:

- `turn_view.js`;
- `css/20_messages.css`;
- task/delegate/thinking/tool-result renderers and their CSS only as each surface migrates;
- focused DOM, keyboard, and lifecycle tests.

Work:

1. Migrate simplified turns first.
2. Migrate native details/disclosure surfaces in bounded groups.
3. Preserve class names and styling that are genuine UI contracts, while deleting direct display toggles and competing transitions.
4. Add focus, ARIA, interruption, live-content resize, scroll-anchor, and reduced-motion coverage.

Exit gate:

- repeated open/close/reverse is smooth and ends in the requested state;
- no input waits for panel content work;
- accessibility and geometry tests pass in both motion modes;
- old visibility code for each migrated surface is deleted.

### WP4 — Rebuild Resources rendering around stable sections

Files:

- `resources.js`;
- split modules derived from `resources_render.js`;
- `css/00_base.css` plus narrowly scoped resource styles;
- `serve_chat_ui.py`;
- Resources/sidebar tests and documentation.

Work:

1. Separate resource acquisition, normalized model state, and section rendering.
2. Make open/close independent of `loadResources()`.
3. Remove root HTML replacement and patch stable section/row nodes.
4. Preserve focused conversation generation fencing.
5. Convert headers to semantic controls.
6. Add keyboard behavior to true trees and disclosure behavior to accordion sections.
7. Replace sidebar `width`/`flex-grow` transitions with a desktop FLIP transition and preserve the mobile drawer layout.
8. Delete the old renderer/toggle path once each section is migrated.

Exit gate:

- a cold Resources open paints immediately;
- refresh preserves focus, scroll, open nodes, and DOM identity;
- opening causes no request by itself;
- a single resource update patches only its section/row;
- desktop and mobile performance/geometry gates pass.

### WP5 — Consolidate floating surfaces

Files:

- new `tasks/io/chat_ui/ui_floating_layer.js`;
- `tooltips.js`;
- `resources_menus.js`;
- `conversations_menu.js`;
- `files_panel.js`;
- other menu/popover owners found by the WP0 inventory;
- `css/10_chrome.css`, `css/80_dialogs.css`, and `css/95_action_dock.css` where required;
- focused accessibility and lifecycle tests.

Work:

1. Move common portal placement and cleanup into the controller.
2. Migrate tooltip grouping first, then menus/popovers, then dialog motion.
3. Remove duplicated delayed document listeners and local clamp logic.
4. Preserve surface-specific commands, markup, and visual style.
5. Verify scroll/resize/blur/pointercancel/Escape and focus restoration.

Exit gate:

- 100 repeated open/close cycles return listener, timer, observer, animation, and portal-node counts to baseline;
- all keyboard and focus paths work;
- geometry remains inside the viewport at every tested edge/corner.

### WP6 — Make workflow and active-state surfaces incremental

Files:

- `workflow_kanban.js`;
- `workflow_run_inspector.js`;
- workflow CSS;
- `turn_view.js` live-cue scheduler;
- button/progress/step owners identified in WP0;
- corresponding tests.

Work:

1. Patch Kanban cards, lanes, drawer, run rows, and detail regions by key.
2. Convert progress motion to transforms.
3. Add stable button faces and generation fencing.
4. Update task steps locally.
5. Cache canvas size, centralize visibility scheduling, and remove permanent `will-change`.
6. Pause or disable decorative work when hidden or reduced-motion is active.

Exit gate:

- local workflow state changes do not rebuild their parent surface;
- active streaming plus repeated interaction stays inside the performance budget;
- no stale async completion regresses button or step state;
- hidden/reduced-motion surfaces consume no decorative timer or frame work.

### WP7 — Full validation, cleanup, and documentation

Files:

- all focused and full WebChat suites;
- browser performance and visual fixtures;
- `docs/02_REFERENCE_TASKS_SERVICES.md`;
- `docs/CHAT_UI_TEMPLATES.md`;
- this plan, updated with completion evidence;
- `CHANGELOG.md`.

Work:

1. Run all focused suites, then the full WebChat and repository gates.
2. Run normal/reduced-motion browser matrices at desktop and mobile sizes.
3. Compare screenshots and computed geometry.
4. Audit remaining layout-bound transitions, direct disclosure display toggles, projection-wide clones, and orphan lifecycle owners.
5. Delete obsolete selectors, tests that assert the costly implementation, and unused helpers.
6. Record final files, test counts, trace summary, and any intentionally unanimated visibility changes.

Exit gate:

- every definition-of-done item below is evidenced;
- no old/new dual path remains;
- documentation describes the shipped behavior rather than the plan alone.

## Test strategy

### Fast controller and DOM tests

Add focused tests for:

- all disclosure state transitions and every mid-animation reversal;
- resize retargeting without observer loops;
- stale animation completion and stale async generation rejection;
- ARIA, `inert`, `hidden`, focus restoration, and keyboard navigation;
- read-before-write scheduling;
- dirty-key derivation from mutation records;
- insertion, update, removal, reorder, load-more, and hidden projection behavior;
- resource refresh preserving node identity and UI state;
- owner teardown and diagnostic counts;
- reduced-motion behavior with zero active animations/timers.

Update tests that currently require the expensive mechanism. In particular, tests must stop requiring full projection cloning and sidebar `flex-grow` transitions. Replacement assertions should verify behavior and cost boundaries, not merely new source strings.

### Real Chromium tests

Use Playwright with a deterministic local fixture built from the real served HTML, CSS, and JavaScript modules. Backend calls and SSE are controlled by the fixture; no external service or timing is involved.

For each performance case:

1. warm the page and perform one unmeasured interaction;
2. run at least 30 measured interactions;
3. measure from input dispatch to the next presented paint;
4. observe Long Tasks;
5. collect a Chrome DevTools Protocol trace for layout, style, scripting, and paint;
6. collect clone/reconcile and lifecycle diagnostics;
7. repeat during character-by-character streaming;
8. repeat in normal and reduced-motion modes.

Timing gates run only on declared reference CI hardware/browser images. Functional, lifecycle, clone-count, accessibility, and geometry gates run everywhere. This prevents slow arbitrary developer machines from producing false timing failures without weakening the reference gate.

### Visual and geometry tests

For collapsed, expanding, and expanded states, capture:

- screenshot;
- trigger and panel bounding rectangles;
- overflow and clipping state;
- computed opacity/transform/block size;
- active element;
- ARIA/`inert`/`hidden` values.

Cover at least:

- default desktop;
- narrow mobile;
- 75% and 150% PawFlow UI scale;
- long localized labels;
- normal and reduced motion;
- content growth while opening;
- rapid reversal.

Screenshots guard appearance. Geometry assertions guard behavior that a screenshot can miss.

### Lifecycle soak

Repeat each relevant open/close/switch operation 100 times, then remove or switch its owner. Assert these return to the captured baseline:

- `MutationObserver` and `ResizeObserver` instances;
- document/window listeners;
- timeouts and intervals;
- requestAnimationFrame callbacks;
- live Web Animations;
- portal nodes;
- retained projected rows;
- controller ownership records.

## Performance budgets

All values below are release gates on the reference CI browser image.

| Metric | Budget |
|---|---:|
| Click-to-next-paint, p95 | less than 16.7 ms |
| Long tasks over 50 ms attributable to one tested click | 0 |
| Whole-projection clones for one character mutation | 0 |
| Updated top-level rows for one character mutation | at most 1 per visible projection |
| Hidden-projection clone/update work | 0 |
| Resource requests caused solely by open/close | 0 |
| Whole Resources-root replacements after initialization | 0 |
| Whole Kanban-board rebuilds for one card disclosure | 0 |
| Active animations after settle/cancel | 0 |
| Observer/listener/timer/frame count after lifecycle soak | exactly the pre-test baseline |
| Reduced-motion temporal animations and decorative timers | 0 |
| Unbounded permanent `will-change` hints | 0 |

Additionally, the trace must show no forced layout caused by a DOM/style write followed by a geometry read inside the originating input task.

A duration target is not a performance target by itself. An animation can last 220 ms and still respond on the first frame; a 120 ms animation that begins after a 200 ms rebuild is a failure.

## Accessibility acceptance criteria

- Every disclosure trigger is keyboard reachable and exposes `aria-expanded` plus `aria-controls`.
- Closed content is not reachable by sequential focus or accessibility traversal.
- Closing a surface containing focus restores focus to a valid owner.
- Menu, tree, disclosure, tab, and dialog keyboard patterns remain distinct and valid.
- Escape closes the topmost dismissible surface only.
- Tooltips use descriptions, not label replacement.
- Reduced motion removes non-essential movement without removing status information.
- No animation is required to understand state.
- Live-region behavior is unchanged unless a dedicated accessibility test proves a correction is needed.

## Observability and diagnostics

Production behavior must not depend on telemetry. The motion modules expose counters and performance marks only when a test/development diagnostic flag is enabled.

Required diagnostic fields:

- controller type and owner ID;
- target logical state and generation;
- active animation channel count;
- observer/listener/timer/frame count;
- projection batch size, dirty-key count, clone count, and patched-row count;
- resource request generation and deduplication result;
- interaction start, first-paint acknowledgement, and settle marks.

Diagnostic output must not include message content, resource values, secrets, or user identifiers.

## Rollout and commit discipline

This plan authorizes no implementation by itself. When implementation starts:

- use one dedicated commit per work package or independently reviewable sub-package;
- do not combine unrelated fixes;
- keep every intermediate commit functional and tested;
- migrate a surface atomically and delete its old path in the same commit;
- do not keep a runtime legacy flag;
- update the relevant documentation and tests in the same change;
- do not copy `interior` source verbatim. If code is ever copied rather than behavior reimplemented, preserve its MIT notice and update third-party attribution before merging.

Rollback is by reverting the relevant atomic commit, not by preserving dormant old code.

## Risks and mitigations

| Risk | Mitigation |
|---|---|
| Dynamic content changes height during disclosure | One ResizeObserver per open owner, coalesced once per frame, retargeting from the current visual value |
| Animation hides rather than fixes latency | WP2 and Resources data/render separation must pass before those surfaces are considered complete |
| Height motion creates layout work | Contain the wrapper, prohibit simultaneous rerender, trace it, and use FLIP for large shell surfaces |
| Rapid toggles finish in the wrong state | Monotonic generation and replaceable named animation channels |
| Projection keys are missing or collide | Expose existing UUIDs in canonical DOM and fail tests on missing/duplicate keys |
| Refresh destroys focus/scroll/open state | Stable section/row DOM identity and keyed in-place patching |
| Shared controllers become a new monolith | Four narrow modules, no generic component system, and the repository file-size rule |
| Central listeners leak across conversations | Abort-owned controller lifecycle and 100-cycle soak tests |
| Performance gates become flaky | Deterministic fixtures, warm-up, fixed sample count, pinned browser image, timing gate only on reference CI |
| Reduced motion is fragmented | One CSS token policy plus one live JavaScript media-query owner |
| Custom themes regress | Preserve theme variables and visual selectors; test default themes and operator CSS ordering |
| Mobile drawer becomes unreachable | Preserve fixed positioning and explicitly test toggle/rail geometry throughout the transition |

Task containers and terminal-output rows are canonical top-level projection
sources even though they are not transcript messages. They therefore carry an
explicit `data-projection-key`: task keys include the iteration while retaining
`data-task-id` for filtering, and terminal rows prefer the SSE event/message ID
with a unique local fallback. Missing identities remain a hard reconciler error.

## Definition of done

The project is complete only when all of the following are true:

- [x] Shared motion, disclosure, projection, and floating-layer owners are small, documented, and tested.
- [x] No React, Next.js, `motion`, polyfill, legacy flag, or parallel UI path was added.
- [x] Simplified-turn input handlers contain no forced post-write layout.
- [x] Filtered and OpenSpace projections update only dirty keyed rows.
- [x] Resources opens independently of fetching and never replaces its root after initialization.
- [x] Resource refresh preserves disclosure, focus, scroll, and DOM identity.
- [x] Sidebar and progress motion use compositor-friendly transforms.
- [x] Kanban and inspector local changes are incremental.
- [x] Repeated interruption always settles in the latest requested state.
- [x] Reduced motion reaches identical logical and accessibility states with no temporal/decorative work.
- [x] Keyboard, ARIA, focus, and viewport-placement checks pass.
- [x] The lifecycle soak returns every counter to baseline.
- [x] The 97-test baseline remains green and all new focused/full suites pass.
- [x] Chromium functional, geometry, screenshot, trace, and streaming-stress gates pass; timing remains reference-CI-only.
- [x] Migrated display toggles, layout-bound transitions, full-clone paths, obsolete helpers, and tests asserting them are deleted.
- [x] `docs/CHAT_UI_TEMPLATES.md`, `docs/02_REFERENCE_TASKS_SERVICES.md`, `CHANGELOG.md`, and this plan describe the final implementation and evidence.

## Completion evidence — 2026-09-04

- The full Chat UI, workflow, OpenSpace, header, mobile, and repository-sidebar
  Python selection passes **368 tests**. It includes the original 97-test
  baseline and the seven real-Chromium cases.
- All **23** JavaScript specs pass (**195** assertions/check cases), including
  interruption, keyed Resources/projection behavior, floating-layer lifecycle,
  110 turn-view cases, and generation-owned workflow action faces.
- Chromium exercises 500- and 1,000-row projections; one streamed character
  clones and patches one visible keyed row, hidden projections do no work, and
  controller ownership returns to zero. Four desktop/mobile × normal/reduced
  matrices run 30 warmed disclosure interactions and compare collapsed/expanded
  screenshots, geometry, ARIA, inertness, focus restoration, and settled motion.
  The CDP path records layout/update-layout-tree events, and the 100-cycle
  Resources/disclosure/floating-layer soak returns observers, listeners,
  animations, frames, and portal ownership to the captured baseline.
- The current relay Chromium run passes deterministic gates in about 25 seconds.
  It is not declared reference hardware, so it makes no p95 or Long Task release
  claim. `PAWFLOW_REFERENCE_BROWSER=1` enables the documented 16.7 ms p95 and
  zero-Long-Task budgets on the pinned reference environment.
- The final source audit finds no CSS layout-bound transition or permanent
  `will-change` hint, and no whole-root clear path for the migrated OpenSpace,
  Resources, or workflow projections.
- This evidence describes the uncommitted shared worktree. Runtime validation
  follows the requested combined beta.264 hotpatch and server restart.

## Recommended first implementation slice

The smallest useful first slice is WP0 plus the turn/projection subset of WP1–WP3:

1. add the deterministic performance fixture;
2. add `ui_motion.js`, `ui_disclosure.js`, and `05_motion.css`;
3. remove synchronous turn scroll work;
4. migrate simplified-turn disclosure;
5. add `ui_projection.js`;
6. migrate filtered tabs and OpenSpace;
7. prove the click-to-paint, long-task, clone-count, reduced-motion, and lifecycle gates.

That slice attacks the highest measured jank, establishes the reusable foundation, and produces hard evidence before the larger Resources renderer migration.
