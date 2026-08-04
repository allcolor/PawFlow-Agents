---
name: pawflow-helper
description: Guide a user through the PawFlow interface with the non-destructive pawflow-ui semantic tool and optional avatar gestures.
---

# PawFlow interface guidance

Use this skill when the user asks how to find, understand, configure, or use a
PawFlow feature in the web interface.

## Required workflow

1. Call `pawflow-ui` with `operation=get` before the first interface action.
2. Use only targets and actions returned by that semantic node.
3. Prefer `guide` when an interface surface must be opened and highlighted in
   one step. Keep the callout message under 200 characters.
4. Explain what was opened and what the user should see.
5. Call `clear` when the highlighted step is complete or the user cancels.

## Safety boundary

- The helper opens, scrolls to, and highlights existing PawFlow surfaces.
- It does not submit forms, create or delete resources, change settings,
  reveal secrets, or invoke administrator operations.
- Never use or invent CSS selectors, DOM IDs, JavaScript, coordinates, or raw
  browser automation. Semantic target IDs are the only UI identifiers.
- If an action is unavailable, report the returned reason instead of claiming
  success.
- Ask the user to perform or explicitly confirm any state-changing step.

## Avatar coordination

When `avatar-ui` is available, a brief `playMotion` or `setMood` call may
accompany a guide step. Do not require avatar animation for the UI operation to
succeed, and do not hide UI errors behind a gesture.
