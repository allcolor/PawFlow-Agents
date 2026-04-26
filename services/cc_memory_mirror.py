"""Mirror CC-native memory/*.md files into the PawFlow MemoryStore.

The Claude Code 'memory skill' persists long-term facts by writing
`.md` files with YAML frontmatter to `<CLAUDE_CONFIG_DIR>/memory/`.
On Phase 3 this directory lives under the relay's FUSE mount, so every
write goes through `services.relay_server_fs.RelayServerFs`.

After a successful release (close-after-write), RelayServerFs calls
`mirror_write()` with the user id, the in-slot relative path, and the
file's full content. We parse the frontmatter, derive a stable dedup
tag from `(conversation_id, agent, slug)`, embed the body with the
local provider, and upsert into MemoryStore. `mirror_unlink` handles
deletion and `mirror_rename` handles both sides of a move.

All entry points are **best-effort**: failures are logged and
swallowed — a broken mirror must never fail the FS op that triggered
it, because the user's native memory skill still owns the file itself.
"""

import logging
import re
from pathlib import PurePosixPath
from typing import Optional, Tuple

logger = logging.getLogger(__name__)


_FRONTMATTER_RE = re.compile(
    rb"\A---\s*\n(?P<fm>.*?)\n---\s*\n?(?P<body>.*)\Z", re.DOTALL
)

# CC memory skill uses four types. Map each to a MemoryStore category
# that `recall` already knows how to filter on.
_TYPE_CATEGORY = {
    "user": "facts",
    "feedback": "advice",
    "project": "facts",
    "reference": "facts",
}

# Skip the skill's index file — we can regenerate it from store state
# if anything ever reads it, and its contents duplicate the per-file
# memories we already mirror.
_INDEX_FILE = "MEMORY.md"


def match_memory_path(rel_path: str) -> Optional[Tuple[str, str, str]]:
    """Return `(conversation_id, agent, slug)` if `rel_path` points to a
    mirrorable CC memory file, else None.

    The in-slot layout (relative to `CLAUDE_SESSIONS_DIR/<user_id>/`) is
    `<conversation_id>/<agent>/.../memory/<slug>.md`. We don't pin the
    depth between `<agent>` and `memory/` because CC's project-slug
    component varies.
    """
    if not rel_path:
        return None
    p = PurePosixPath(rel_path.lstrip("/\\"))
    parts = p.parts
    if len(parts) < 4:
        return None
    if parts[-2] != "memory":
        return None
    name = parts[-1]
    if not name.endswith(".md") or name == _INDEX_FILE:
        return None
    conversation_id = parts[0]
    agent = parts[1]
    slug = name[:-3]
    return conversation_id, agent, slug


def _parse_frontmatter(data: bytes) -> Optional[dict]:
    """Return {name, description, type, body} or None on malformed input.

    CC's frontmatter is simple key:value lines between two `---` fences;
    we don't need a YAML parser and pulling one in would add a dep for
    three known keys.
    """
    m = _FRONTMATTER_RE.match(data)
    if not m:
        return None
    fm_raw = m.group("fm").decode("utf-8", errors="replace")
    body = m.group("body").decode("utf-8", errors="replace").strip()
    fields: dict = {}
    for line in fm_raw.splitlines():
        if ":" not in line:
            continue
        key, _, value = line.partition(":")
        fields[key.strip().lower()] = value.strip()
    fields["body"] = body
    return fields


def _dedup_tag(conversation_id: str, agent: str, slug: str) -> str:
    return f"cc-mem:{conversation_id}:{agent}:{slug}"


def _build_text(fields: dict) -> str:
    body = fields.get("body", "").strip()
    name = fields.get("name", "").strip()
    if name:
        return f"[{name}] {body}" if body else f"[{name}]"
    return body


def _embed(text: str) -> Optional[list]:
    """Embed with the local provider. Returns None on any failure
    (missing sentence-transformers, empty text, etc.)."""
    if not text:
        return None
    try:
        from core.embeddings import EmbeddingProvider
        results = EmbeddingProvider.instance().embed([text], provider="local")
        return results[0] if results else None
    except Exception:
        logger.debug("[cc-mirror] local embedding unavailable", exc_info=True)
        return None


def mirror_write(user_id: str, rel_path: str, data: bytes) -> None:
    """Called after a memory file is fully written and closed.

    Parses frontmatter, upserts into MemoryStore keyed by the stable
    dedup tag. Any error is logged and swallowed.
    """
    try:
        match = match_memory_path(rel_path)
        if not match:
            return
        conversation_id, agent, slug = match
        fields = _parse_frontmatter(data)
        if not fields:
            logger.debug("[cc-mirror] no frontmatter in %s", rel_path)
            return
        text = _build_text(fields)
        if not text:
            return
        cc_type = (fields.get("type", "") or "").lower()
        category = _TYPE_CATEGORY.get(cc_type, "facts")
        tag = _dedup_tag(conversation_id, agent, slug)
        type_tag = f"cc-type:{cc_type}" if cc_type else "cc-type:unknown"
        tags = ["cc-native", type_tag]

        # Embed up front so semantic_recall can find the entry. If the
        # local provider is unavailable (sentence-transformers not
        # installed in this env, embedding model missing, etc.), we
        # still upsert the entry but tag it 'needs-embedding' for a
        # later backfill pass via MemoryStore.ensure_embeddings(...).
        # The previous version silently stored entries with no vector,
        # making them invisible to semantic_recall but visible to
        # text-based recall — a quiet correctness bug for users who
        # rely on semantic search.
        embedding = _embed(text)
        if embedding is None:
            logger.warning(
                "[cc-mirror] embedding unavailable for user=%s slug=%s "
                "— entry stored without vector, tagged 'needs-embedding'",
                user_id, slug)
            tags.append("needs-embedding")

        from core.memory_store import MemoryStore
        store = MemoryStore.instance()
        entry = store.upsert_by_tag(
            user_id=user_id,
            dedup_tag=tag,
            text=text,
            tags=tags,
            source="cc-memory-skill",
            embedding=embedding,
            agent=agent,
            conversation_id=conversation_id,
            category=category,
        )
        logger.info("[cc-mirror] upsert user=%s slug=%s id=%s embed=%s",
                    user_id, slug, entry.id,
                    "yes" if embedding is not None else "no")
    except Exception:
        logger.exception("[cc-mirror] mirror_write failed for %s", rel_path)


def mirror_unlink(user_id: str, rel_path: str) -> None:
    """Remove the entry bound to this memory file, if any."""
    try:
        match = match_memory_path(rel_path)
        if not match:
            return
        tag = _dedup_tag(*match)
        from core.memory_store import MemoryStore
        n = MemoryStore.instance().forget_by_tag(user_id, tag)
        if n:
            logger.info("[cc-mirror] deleted %d entr%s for %s",
                        n, "y" if n == 1 else "ies", rel_path)
    except Exception:
        logger.exception("[cc-mirror] mirror_unlink failed for %s", rel_path)


def mirror_rename(user_id: str, old_rel: str, new_rel: str,
                  new_data: Optional[bytes] = None) -> None:
    """Delete the entry for `old_rel` (if mirrorable) and upsert the one
    for `new_rel` when `new_data` is provided.

    Callers that can cheaply re-read the renamed file should pass its
    content as `new_data` so the mirror stays in sync without an extra
    disk round-trip. If `new_data` is None, the next write/release on
    the destination will pick it up.
    """
    try:
        old_match = match_memory_path(old_rel)
        if old_match:
            mirror_unlink(user_id, old_rel)
        if new_data is not None and match_memory_path(new_rel):
            mirror_write(user_id, new_rel, new_data)
    except Exception:
        logger.exception("[cc-mirror] mirror_rename failed %s -> %s",
                         old_rel, new_rel)
