# PawFlow Avatar Helper

`pawflow.avatar-helper` is an installable PFP package that turns the realtime
avatar into a non-destructive guide for the PawFlow web interface. It is kept
separate from `pawflow.avatar-runtime`: the runtime owns rendering, voice,
lip-sync, mood, and motion, while the helper owns interface guidance.

## Package contents

The package source is
`packages/pawflow.avatar-helper.pfpdir` and declares four objects:

- `agent:pawflow-helper` — a dedicated interface helper agent;
- `skill:pawflow-helper` — the guidance workflow and safety rules;
- `ui_extension:avatar-helper` — the highlight/callout browser overlay;
- `tool:pawflow-ui` — the agent-to-browser semantic bridge.

It has an explicit package dependency on `pawflow.avatar-runtime` version
`>=0.1.0,<1.0.0`. Installing only a subset that omits the skill prevents the
agent object from installing because its `assigned_skills` reference would be
unresolved.

## Semantic node

The extension registers one node:

```text
pawflow.avatar-helper:ui.guide
```

The node supports these actions:

| Action | Effect |
|--------|--------|
| `describe` | Return bounded state for the available guide targets |
| `open` | Open or reveal one fixed non-destructive surface |
| `focus` | Scroll to a visible target and add a halo/callout |
| `guide` | Open and focus a target in one invocation |
| `clear` | Remove the active halo and callout |

The initial target catalog is deliberately small and stable:

- `sidebar`
- `conversations`
- `resources`
- `pfp.repository`
- `actions`
- `plans`
- `files`
- `agent`
- `composer`

Targets are validated by the semantic action enum. The tool does not accept a
node name, DOM ID, CSS selector, coordinate, script, or raw HTML from the
agent. The extension contains the fixed internal mapping to PawFlow elements.

## Tool usage

The agent should inspect the browser node before guiding:

```json
{"operation":"get"}
```

To open and highlight Resources:

```json
{
  "operation": "invoke",
  "action": "guide",
  "arguments": {
    "target": "resources",
    "message": "Resources and installed packages are shown here."
  }
}
```

When the step is finished:

```json
{"operation":"invoke","action":"clear","arguments":{}}
```

The helper skill may separately invoke `avatar-ui` for a subtle mood or motion.
Avatar animation is optional and never masks a failed interface action.

## Safety boundary

The MVP is intentionally non-destructive. It may reveal panels, scroll, and
draw a visual guide. It does not:

- submit, prefill, or mutate forms;
- create, update, or delete resources;
- change agents, permissions, services, secrets, or server settings;
- invoke administrator operations;
- accept arbitrary selectors or browser automation instructions.

Every tool call is still constrained by the signed PFP browser grant. The
existing semantic bridge requires the same user and conversation, selects an
eligible active browser tab, bounds request/result JSON, and records audit
events. Disabling or uninstalling the UI extension unregisters its semantic
node; `shutdown`, conversation changes, and agent changes clear active visual
guidance.

UI extensions run in the same browser address space as the PawFlow chat after
installation consent. The package therefore remains subject to normal PFP
review, the global UI-extension kill switch, and per-conversation extension
disable controls.

## Development and installation

For local development, install `pawflow.avatar-runtime` first, then load the
helper source through the normal PFP development workflow:

```text
/pfp dev-load packages/pawflow.avatar-helper.pfpdir --scope user
```

The official bundled artifact must be produced by
`scripts/build-bundled-pfps.py` with `PAWFLOW_PFP_SIGNING_KEY`. Never create a
catalog entry or substitute developer key manually: the builder derives the
artifact hash, size, public key, and object list from the signed result.

## Verification

`tests/test_avatar_helper_package.py` covers:

- manifest objects, dependency, and exact browser grant;
- agent/skill safety instructions;
- absence of arbitrary selector/click/eval interfaces;
- browser open/focus/clear behavior in a Node harness;
- pinned semantic tool dispatch;
- missing-dependency behavior;
- signed build, install, resource visibility, and uninstall lifecycle.

The first follow-up phase may add explicitly confirmed, reversible actions.
State-changing operations must remain separate from the navigation target
catalog and require a visible user confirmation contract.
