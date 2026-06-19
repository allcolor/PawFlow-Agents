"""Claude Code provider: image scrub + extraction helpers (<=800-line split)."""


import json
import logging
import os

from core.interrupt_policy import SOFT_INTERRUPT_USER_COMMAND  # noqa: F401
from core.llm_providers._cc_base import (
    _CC_READER_EOF, _CC401Retry, _CCStreamState)  # noqa: F401

logger = logging.getLogger(__name__)


class _CCIoMixin:
    """Image scrub + extraction for Claude Code messages."""
    @staticmethod
    def _scrub_legacy_image_placeholders(session_file: str) -> None:
        """Remove stale inline image placeholders from user turns on disk."""
        if not session_file or not os.path.exists(session_file):
            return
        import re as _re

        image_re = _re.compile(
            r"\s*\[image:\s*image_\d+_\d+\.(?:png|jpe?g|webp|gif)\]\s*",
            _re.IGNORECASE,
        )

        def _scrub_text(text: str) -> str:
            return _re.sub(r"\s+", " ", image_re.sub(" ", text)).strip()

        changed = False
        output = []
        with open(session_file, "r", encoding="utf-8", errors="replace") as fh:
            for line in fh.read().splitlines():
                try:
                    entry = json.loads(line)
                except json.JSONDecodeError:
                    output.append(line)
                    continue
                msg = entry.get("message") if isinstance(entry, dict) else None
                if not isinstance(msg, dict):
                    output.append(line)
                    continue
                if entry.get("type") != "user" and msg.get("role") != "user":
                    output.append(line)
                    continue
                content = msg.get("content")
                if isinstance(content, str):
                    scrubbed = _scrub_text(content)
                    if scrubbed != content:
                        msg["content"] = scrubbed
                        changed = True
                elif isinstance(content, list):
                    for part in content:
                        if not isinstance(part, dict) or part.get("type") != "text":
                            continue
                        text = part.get("text", "")
                        if not isinstance(text, str):
                            continue
                        scrubbed = _scrub_text(text)
                        if scrubbed != text:
                            part["text"] = scrubbed
                            changed = True
                output.append(json.dumps(entry, ensure_ascii=False) if changed else line)
        if changed:
            with open(session_file, "w", encoding="utf-8") as fh:
                fh.write("\n".join(output) + ("\n" if output else ""))

    @staticmethod
    def _extract_images(messages, user_id: str, conversation_id: str) -> list:
        """Extract images from the LAST user message only.

        Removes image blocks from ALL messages (so they don't bloat the text
        prompt). Only returns image blocks from the LAST user message as
        content blocks for the stream-json message (native vision).

        Older images are replaced with a placeholder text.

        user_id and conversation_id are REQUIRED — image_ref blocks point
        to private attachments stored under (owner × conv × file_id).
        A missing identifier means the caller has a bug; raise loudly
        instead of dropping the image and pretending nothing happened.
        """
        if not user_id:
            raise ValueError(
                "_extract_images: user_id is required to resolve image_ref "
                "attachments (owner-scoped access control)")
        if not conversation_id:
            raise ValueError(
                "_extract_images: conversation_id is required to resolve "
                "image_ref attachments (files belong to a conversation)")
        import base64 as _b64
        image_blocks = []

        # Find the current user message index. Only that message may feed
        # native vision; older image refs stay as FileStore links in text.
        _last_user_idx = -1
        for i, m in enumerate(messages):
            if m.role == "user":
                _last_user_idx = i

        for idx, m in enumerate(messages):
            if not isinstance(m.content, list):
                continue
            new_content = []
            for block in m.content:
                if not isinstance(block, dict):
                    new_content.append(block)
                    continue
                btype = block.get("type", "")

                _is_last_user = (idx == _last_user_idx)

                # Placeholder policy: when we extract an image from the LAST
                # user message into image_blocks, that image is sent to the
                # model natively via vision — emitting a text placeholder on
                # top of it is actively harmful: the agent reads
                # "[image: foo.png]" and calls see() / read() on it, duplicating
                # the image in its context (tokens ×2) for zero benefit.
                # For OLDER user messages we keep a text placeholder so the
                # model knows an image was there, but we DON'T re-send it via
                # vision (would bloat context with every historical image).

                if btype == "image_url":
                    url = (block.get("image_url") or {}).get("url", "")
                    if url.startswith("data:"):
                        if _is_last_user:
                            try:
                                header, data_b64 = url.split(",", 1)
                                mime = header.split(":")[1].split(";")[0]
                                image_blocks.append({
                                    "type": "image",
                                    "source": {
                                        "type": "base64",
                                        "media_type": mime,
                                        "data": data_b64,
                                    },
                                })
                                logger.info("Extracted image for vision: %s (%d chars b64)",
                                            mime, len(data_b64))
                            except Exception as e:
                                logger.warning("Failed to extract image: %s", e)
                            # Image is in vision — no text placeholder.
                        else:
                            new_content.append({"type": "text", "text": "[image]"})
                        continue

                elif btype == "image":
                    source = block.get("source", {})
                    if source.get("type") == "base64":
                        if _is_last_user:
                            image_blocks.append(block)
                            logger.info("Extracted image for vision: %s",
                                        source.get("media_type", "?"))
                            # Image is in vision — no text placeholder.
                        else:
                            new_content.append({"type": "text", "text": "[image]"})
                        continue

                elif btype == "image_ref":
                    # Image stored in FileStore — load for vision on last user message only.
                    # Older image_ref blocks (from prior turns already seen by
                    # the model via session resume) are intentionally dropped
                    # to text to keep the prompt compact.
                    if _is_last_user:
                        from core.file_store import FileStore
                        import base64 as _b64
                        _fid = block.get("file_id", "")
                        if not _fid:
                            raise ValueError(
                                "image_ref block missing file_id — producer bug")
                        _fname, _data, _ct = FileStore.instance().get_required(
                            _fid, user_id=user_id,
                            conversation_id=conversation_id)
                        _data_b64 = _b64.b64encode(_data).decode("ascii")
                        image_blocks.append({
                            "type": "image",
                            "source": {
                                "type": "base64",
                                "media_type": block.get("mime_type", _ct),
                                "data": _data_b64,
                            },
                        })
                        logger.info("Loaded image from FileStore for vision: %s (%d bytes)",
                                    _fid, len(_data))
                        new_content.append({
                            "type": "text",
                            "text": f"Attached image: fs://filestore/{_fid}/{_fname}",
                        })
                    else:
                        _fid = block.get("file_id", "")
                        _fname = block.get("filename", "image") or "image"
                        if _fid:
                            new_content.append({
                                "type": "text",
                                "text": f"Attached image: fs://filestore/{_fid}/{_fname}",
                            })
                        else:
                            new_content.append({"type": "text", "text": f"[image: {_fname}]"})
                    continue

                new_content.append(block)

            m.content = new_content

        return image_blocks
