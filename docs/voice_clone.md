# Voice Cloning

> Register a voice from a reference audio sample, then synthesise
> arbitrary text in that voice. Outputs stream back as audio URLs
> suitable for direct consumption by `lipsync` / `speech_to_video`.

## Consent & ethics

Voice cloning raises strong consent concerns. Only clone voices for
which you have explicit permission from the speaker. The platform
does not watermark outputs — downstream misuse is the caller's
responsibility. Reference samples and rendered outputs live in the
user-scoped FileStore and are purged on cascade delete.

## Tools

Three agent-facing tools, all bound to the active voice-clone
service via `_make_voice_clone_resolver` in
`tasks/ai/agent_tool_config.py`.

### `clone_voice(name, reference_audio_url[, reference_text, language])`

Register a voice clone for the current user. `reference_audio_url`
may be an absolute HTTP(S) URL or an `fs://filestore/<id>/<name>`
reference. The bytes are fetched, PCM-normalised, hashed, stored
in the FileStore under category `voice_clone_ref`, and a
`voice_clones` entry is persisted in the ScopedRepository at
`scope=user`. For paradigm A providers (see below) the sample is
also uploaded to the provider and a stable `voice_id` is cached in
the entry.

### `speak(voice, text[, language, destination, path])`

Synthesise `text` using a voice registered via `clone_voice`. The
result is written to the FileStore (category `voice_clone_tts`)
and returned as an audio URL. Identical `(voice, text, language)`
inputs hit a content-addressed cache and skip the provider call
entirely.

### `delete_voice(voice)`

Cascade-delete a voice clone: provider-side `voice_id` (paradigm A
only), reference audio, every cached TTS rendering keyed on the
voice, and finally the repository entry. Mirrors the UI
"Voices" panel trash button.

## Paradigms

Two concrete providers are wired, one per paradigm.

### Paradigm A — persistent voice_id (ElevenLabs)

`services/elevenlabs_voice_clone_service.py` — the first
`clone_voice` uploads the sample via multipart POST
`/v1/voices/add` and stores the returned `voice_id` in the
`voice_clones` entry. Subsequent `speak` calls hit
`POST /v1/text-to-speech/{voice_id}` with just the text + model
config — the sample is never re-sent. Quota-bounded by the
ElevenLabs plan (Starter 10, Creator 30, Pro 160) — deleting a
clone calls `DELETE /v1/voices/{voice_id}` to free the slot.

### Paradigm B — zero-shot per request (Fish Audio)

`services/fish_audio_voice_clone_service.py` — every `speak` call
posts `/v1/tts` with the raw reference audio (base64) and the
target text inside the same JSON body. No provider-side state,
no up-front registration step. `ensure_voice_id` returns `""`,
`delete_voice_id` is a no-op. Cheap and stateless, at the cost of
re-uploading the sample on every call. Good default for one-off
or short-lived clones.

### Deciding which paradigm the handler uses

The handler is provider-agnostic. The service's `ensure_voice_id`
method returns `""` for paradigm B services and a non-empty string
for paradigm A. Downstream code branches on
`bool(entry["voice_id"])` — no if/elif on provider names.

## Caching

Two cache layers, both keyed in `core/voice_clone_cache.py`:

1. **Reference audio hash** — before hashing, the incoming bytes
   are decoded through ffmpeg to PCM `s16le / 16kHz / mono` so
   that different encodings of the same source (re-muxed,
   re-tagged, resampled) collide on the same hash. Lifts cache
   hit-rate dramatically in practice. Falls back to raw
   SHA-256 when ffmpeg is unavailable or the payload cannot be
   decoded (tests, non-audio blobs).

2. **Rendered TTS** — keyed on
   `sha256(provider | ref_audio_hash | language | text)`. Hits
   are served from the FileStore (category `voice_clone_tts`).
   Each cached file also carries a `voice_ref_hash` metadata tag
   so that cascade-delete can find and purge every rendering
   produced for a given voice without re-computing every
   possible cache key.

## Cascade delete

`voice_clone_cache.cascade_delete(user_id, name, service)`:

| Step | What is removed                                   | Provider call          |
|------|---------------------------------------------------|------------------------|
| 1    | `voice_id` on the provider (paradigm A only)      | `service.delete_voice_id` |
| 2    | Reference audio FileStore entry                   | —                      |
| 3    | Every cached TTS rendering keyed on this voice    | —                      |
| 4    | The `voice_clones` entry itself                   | —                      |

The helper returns a dict summarising what was touched:
`{"entry": bool, "voice_id": bool, "ref_audio": bool, "tts_cached": int}`.
Invoked by `DeleteVoiceHandler` (agent tool) and the
`delete_voice_clone` server action (UI trash button).

When no service instance of the entry's provider is currently
deployed, cascade_delete is called with `service=None` — local
state is always removed, but the upstream quota may remain in use
until a service for that provider is available again. The UI
prints a summary showing which parts of the cascade ran.

## UI — Resource Panel "Voices"

`tasks/io/chat_ui/resources.js` renders a collapsible "Voices"
section under the Resource Panel, right after Prompts. Each row
shows:

- a paradigm badge (`id` for voice_id, `zs` for zero-shot)
- the voice name + provider label
- a play button (`▶`) that streams `/files/<ref_audio_fid>` via
  `<audio>`
- a rename button (`✎`) that fires the `rename_voice_clone`
  server action after a browser `prompt()`; the new name is
  normalised through `voice_clone_cache.safe_name` and a 409 is
  returned if the target already exists. All other fields
  (`ref_audio_*`, `voice_id`, `reference_text`, `language`) are
  preserved — no provider round-trip, no cache invalidation.
- a trash button (`✖`) that cascade-deletes after a confirm

Data source: the `voices` array in the `list_resources` response
(`tasks/ai/actions/agent_resource.py`), drawn from
`voice_clone_cache.list_for_user(uid)`.

## Data model

Each entry in the ScopedRepository (`type=voice_clones`,
`scope=user`) holds:

```
{
  "name":                  str,
  "provider":              str,   # e.g. "fishAudioVoiceClone"
  "provider_version":      str,   # bump on API contract change
  "voice_id":              str,   # empty for paradigm B
  "ref_audio_hash":        str,   # sha256(PCM s16le 16k mono)
  "ref_audio_fid":         str,   # FileStore id
  "ref_audio_filename":    str,
  "ref_audio_content_type":str,
  "ref_audio_size":        int,
  "reference_text":        str,
  "language":              str,
  "created_at":            float,
  "last_used_at":          float
}
```

## Adding a provider

Subclass `services/base_voice_clone.py::BaseVoiceCloneService`:

1. Fill `TYPE`, `VERSION`, `NAME`, `DESCRIPTION`.
2. Implement `_create_connection` (validate credentials).
3. Override `clone_speak(text, voice_id=..., reference_audio_bytes=..., ...)`.
4. For paradigm A, also override `ensure_voice_id(...)` and
   `delete_voice_id(voice_id)`.
5. Register via `ServiceFactory.register(...)` at module load.
6. Import the module from `tasks/__init__.py` under
   `# Voice-cloning TTS services`.

The handler picks it up automatically via `BaseVoiceCloneService`
discovery in `_discover_media_services`.
