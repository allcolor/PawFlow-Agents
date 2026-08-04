# PawFlow starter avatar pack

This independent PFP package contributes the `pawflow-bot` model to the
repository owned by `pawflow.avatar-runtime`. It contains no executable
runtime and requires no PawFlow core change.

The GLB is original PawFlow project material under the MIT License. Rebuild it
deterministically from the repository root:

```bash
python scripts/build-starter-avatar.py \
  packages/pawflow.avatar-pack.starter.pfpdir/content/models/pawflow-bot.glb
```

Install `pawflow.avatar-runtime` first, then inspect and install this pack.
After selecting PawFlow Bot, bind any visible PawFlow voice alias in the avatar
repository. The alias is resolved by `SpeakHandler`; rendered audio reaches
the avatar through the generic media tap.
