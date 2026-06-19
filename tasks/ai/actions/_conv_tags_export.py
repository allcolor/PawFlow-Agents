"""AgentLoopTask actions — conversation"""

import json
import logging
import time

from tasks.ai.actions._conv_base import (
    _UNHANDLED,
    _write_filestore_archive,
)

logger = logging.getLogger(__name__)


def _handle_conv_tags_export(self, action, body, store, user_id, flowfile):
    """Conversation actions cluster: _conv_tags_export. Returns result or _UNHANDLED."""
    if action == "conv_tag":
        conv_id = body.get("conversation_id", "")
        tag_name = body.get("tag_name", "").strip()
        message = body.get("message", "")
        if not conv_id or not tag_name:
            flowfile.set_content(json.dumps({"error": "Missing conversation_id or tag_name"}).encode())
            return [flowfile]
        ok = store.git_tag(conv_id, tag_name, message)
        flowfile.set_content(json.dumps({"ok": ok, "tag": tag_name}).encode())
        return [flowfile]

    if action == "conv_list_tags":
        conv_id = body.get("conversation_id", "")
        if not conv_id:
            flowfile.set_content(json.dumps({"error": "Missing conversation_id"}).encode())
            return [flowfile]
        tags = store.git_list_tags(conv_id)
        flowfile.set_content(json.dumps({"tags": tags}).encode())
        return [flowfile]

    if action == "conv_delete_tag":
        conv_id = body.get("conversation_id", "")
        tag_name = body.get("tag_name", "").strip()
        if not conv_id or not tag_name:
            flowfile.set_content(json.dumps({"error": "Missing conversation_id or tag_name"}).encode())
            return [flowfile]
        ok = store.git_delete_tag(conv_id, tag_name)
        flowfile.set_content(json.dumps({"ok": ok}).encode())
        return [flowfile]

    if action == "conv_export_pawflow":
        conv_id = body.get("conversation_id", "")
        include_filestore = bool(body.get("include_filestore", False))
        if not conv_id:
            flowfile.set_content(json.dumps({"error": "Missing conversation_id"}).encode())
            return [flowfile]
        import io
        import zipfile
        from pathlib import Path as _Path
        from core.file_store import FileStore
        from core.segmented_jsonl import SegmentedJsonl
        conv_dir = store._conv_dir(conv_id)
        if not conv_dir.is_dir():
            flowfile.set_content(json.dumps({"error": "Conversation not found"}).encode())
            return [flowfile]
        buf = io.BytesIO()
        manifest = {
            "format": "pawflow-conversation-archive",
            "version": 2,
            "mode": "full",
            "conversation_id": conv_id,
            "exported_at": time.time(),
            "includes": {
                "transcript": True,
                "shared_context": True,
                "agent_contexts": True,
                "buckets": True,
                "extras": True,
                "bindings": (conv_dir / "bindings.json").exists(),
                "filestore": include_filestore,
            },
            "filestore": {"included": False, "count": 0, "bytes": 0},
        }
        with zipfile.ZipFile(buf, 'w', zipfile.ZIP_DEFLATED) as zf:
            written = set()

            def _write_logical_jsonl(rel_path: _Path):
                log = SegmentedJsonl(conv_dir / rel_path)
                if not log.exists():
                    return
                data = "".join(
                    json.dumps(row, ensure_ascii=False) + "\n"
                    for row in log.iter_rows()
                )
                arcname = str(rel_path)
                zf.writestr(arcname, data)
                written.add(arcname)

            _write_logical_jsonl(_Path("transcript.jsonl"))
            _write_logical_jsonl(_Path("shared.jsonl"))
            for entry in sorted(conv_dir.iterdir()):
                if entry.is_dir() and entry.name not in (".git", "transcript", "shared", "summaries"):
                    _write_logical_jsonl(_Path(entry.name) / "context.jsonl")

            for f in sorted(conv_dir.rglob('*')):
                if not f.is_file() or '.git' in f.parts:
                    continue
                rel = f.relative_to(conv_dir)
                arcname = str(rel)
                if arcname in written:
                    continue
                parts = rel.parts
                if parts and parts[0] in ("filestore",):
                    continue
                if parts and parts[0] in ("transcript", "shared"):
                    continue
                if len(parts) >= 2 and parts[1] == "context":
                    continue
                zf.write(f, arcname)
            if include_filestore:
                manifest["filestore"] = _write_filestore_archive(zf, conv_id, user_id)
            zf.writestr("manifest.json", json.dumps(manifest, ensure_ascii=False, indent=2))
        filename = f"conversation_{conv_id[:8]}.pfconv.zip"
        fid = FileStore.instance().store(filename, buf.getvalue(),
            "application/zip", user_id=user_id, conversation_id=conv_id)
        flowfile.set_content(json.dumps({
            "ok": True, "url": f"/files/{fid}/{filename}", "filename": filename,
            "include_filestore": include_filestore,
            "filestore": manifest.get("filestore", {}),
        }).encode())
        return [flowfile]

    if action == "conv_export_claude_code":
        conv_id = body.get("conversation_id", "")
        if not conv_id:
            flowfile.set_content(json.dumps({"error": "Missing conversation_id"}).encode())
            return [flowfile]
        from core.file_store import FileStore
        msgs = store.load(conversation_id=conv_id, user_id=user_id)
        if not msgs:
            flowfile.set_content(json.dumps({"error": "Conversation empty"}).encode())
            return [flowfile]
        lines = []
        i = 0
        while i < len(msgs):
            m = msgs[i]
            role = m.get("role", "") if isinstance(m, dict) else getattr(m, "role", "")
            content = m.get("content", "") if isinstance(m, dict) else getattr(m, "content", "")
            if role == "user":
                lines.append(json.dumps({"type": "human", "message": {"role": "user", "content": content}}, ensure_ascii=False))
            elif role == "assistant":
                blocks = []
                if content:
                    blocks.append({"type": "text", "text": content})
                parent = m.get("msg_id", "") if isinstance(m, dict) else getattr(m, "msg_id", "")
                j = i + 1
                while j < len(msgs):
                    child = msgs[j]
                    child_role = child.get("role", "") if isinstance(child, dict) else getattr(child, "role", "")
                    if child_role not in ("thinking", "tool_call"):
                        break
                    child_parent = child.get("parent_message_id", "") if isinstance(child, dict) else getattr(child, "parent_message_id", "")
                    if child_parent and parent and child_parent != parent:
                        break
                    if child_role == "thinking":
                        blocks.append({"type": "thinking", "thinking": child.get("content", "")})
                    else:
                        blocks.append({
                            "type": "tool_use",
                            "id": child.get("tool_call_id", ""),
                            "name": child.get("tool_name") or child.get("name") or "",
                            "input": child.get("arguments", {}) or {},
                        })
                    j += 1
                lines.append(json.dumps({"type": "assistant", "message": {"role": "assistant", "content": blocks or content}}, ensure_ascii=False))
                i = j
                continue
            elif role == "tool":
                tcid = m.get("tool_call_id", "") if isinstance(m, dict) else getattr(m, "tool_call_id", "")
                lines.append(json.dumps({"type": "tool_result", "tool_use_id": tcid, "message": {"role": "user", "content": content}}, ensure_ascii=False))
            i += 1
        export = "\n".join(lines) + "\n"
        filename = f"conversation_{conv_id[:8]}.cc.jsonl"
        fid = FileStore.instance().store(filename, export.encode("utf-8"),
            "application/jsonl", user_id=user_id, conversation_id=conv_id)
        flowfile.set_content(json.dumps({
            "ok": True, "url": f"/files/{fid}/{filename}", "filename": filename,
        }).encode())
        return [flowfile]

    return _UNHANDLED
