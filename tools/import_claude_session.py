#!/usr/bin/env python3
"""Import a Claude Code session (.jsonl) into PawFlow conversation format.

Usage:
    python tools/import_claude_session.py <session.jsonl> [--user USER] [--agent AGENT] [--out DIR]

Reads the Claude Code JSONL transcript and produces a PawFlow conversation
JSONL file that can be placed in data/conversations/.
"""

import argparse
import json
import hashlib
import os
import sys
import time
import uuid
from pathlib import Path
from datetime import datetime


def parse_claude_session(path: str):
    """Parse Claude Code JSONL and yield PawFlow-compatible messages."""
    with open(path, "r", encoding="utf-8") as f:
        for line_no, line in enumerate(f, 1):
            line = line.strip()
            if not line:
                continue
            try:
                entry = json.loads(line)
            except json.JSONDecodeError:
                continue

            etype = entry.get("type", "")
            msg = entry.get("message", {})
            role = msg.get("role", "")
            ts_str = entry.get("timestamp", "")

            # Parse timestamp
            ts = 0.0
            if ts_str:
                try:
                    dt = datetime.fromisoformat(ts_str.replace("Z", "+00:00"))
                    ts = dt.timestamp()
                except (ValueError, TypeError):
                    pass

            # Skip non-message entries
            if etype in ("file-history-snapshot", "summary"):
                continue

            if etype == "user" and role == "user":
                content = _extract_content(msg.get("content", ""))
                if not content or content.startswith("[Request interrupted"):
                    continue
                # Skip tool_result content blocks (internal)
                if isinstance(msg.get("content"), list):
                    has_text = any(
                        b.get("type") == "text" and not b.get("text", "").startswith("[")
                        for b in msg["content"] if isinstance(b, dict)
                    )
                    if not has_text:
                        continue
                yield {
                    "role": "user",
                    "content": content,
                    "ts": ts,
                    "msg_id": uuid.uuid4().hex[:12],
                }

            elif etype == "assistant" and role == "assistant":
                content_blocks = msg.get("content", [])
                if isinstance(content_blocks, str):
                    text = content_blocks
                    thinking = ""
                elif isinstance(content_blocks, list):
                    text_parts = []
                    thinking_parts = []
                    tool_calls = []
                    for block in content_blocks:
                        if not isinstance(block, dict):
                            continue
                        btype = block.get("type", "")
                        if btype == "text":
                            text_parts.append(block.get("text", ""))
                        elif btype == "thinking":
                            thinking_parts.append(block.get("thinking", ""))
                        elif btype == "tool_use":
                            tool_calls.append({
                                "id": block.get("id", ""),
                                "name": block.get("name", ""),
                                "arguments": block.get("input", {}),
                            })
                    text = "\n".join(text_parts)
                    thinking = "\n".join(thinking_parts)
                else:
                    continue

                if not text and not tool_calls:
                    # Tool-only turn without text — emit tool_calls
                    if not tool_calls:
                        continue

                msg_dict = {
                    "role": "assistant",
                    "content": text,
                    "ts": ts,
                    "msg_id": uuid.uuid4().hex[:12],
                }
                if thinking:
                    msg_dict["thinking"] = thinking
                if tool_calls:
                    msg_dict["tool_calls"] = tool_calls
                yield msg_dict

            elif etype == "result":
                # Claude Code result — skip, it's internal
                continue


def _extract_content(content):
    """Extract text from content (string or list of blocks)."""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for block in content:
            if isinstance(block, dict):
                if block.get("type") == "text":
                    text = block.get("text", "")
                    # Skip system/internal messages
                    if text.startswith("[System:") or text.startswith("[TOOL OUTPUT"):
                        continue
                    if text.startswith("[Request interrupted"):
                        continue
                    parts.append(text)
            elif isinstance(block, str):
                parts.append(block)
        return "\n".join(parts)
    return str(content) if content else ""


def convert(session_path: str, user_id: str = "imported",
            agent_name: str = "claude-code", output_dir: str = None):
    """Convert a Claude Code session to PawFlow conversation format."""
    if output_dir is None:
        from core.paths import CONVERSATIONS_DIR; output_dir = str(CONVERSATIONS_DIR)
    os.makedirs(output_dir, exist_ok=True)

    # Generate conversation ID from session filename
    stem = Path(session_path).stem
    cid = hashlib.md5(stem.encode(), usedforsecurity=False).hexdigest()[:16]
    out_path = os.path.join(output_dir, f"{cid}.jsonl")

    messages = list(parse_claude_session(session_path))
    if not messages:
        print(f"No messages found in {session_path}", file=sys.stderr)
        return None

    first_ts = messages[0].get("ts") or time.time()
    source = {"type": "agent", "name": agent_name, "provider": "claude-code"}

    with open(out_path, "w", encoding="utf-8") as f:
        # Meta line
        meta = {
            "t": "meta",
            "user_id": user_id,
            "status": "idle",
            "created_at": first_ts,
            "expires_at": 0,
            "title": f"Imported: {stem[:20]}",
        }
        f.write(json.dumps(meta, ensure_ascii=False) + "\n")

        # Messages
        for msg in messages:
            line = {"t": "msg", **msg}
            if msg["role"] == "assistant":
                line["source"] = source
            f.write(json.dumps(line, ensure_ascii=False) + "\n")

    count_user = sum(1 for m in messages if m["role"] == "user")
    count_assistant = sum(1 for m in messages if m["role"] == "assistant")
    print(f"Converted {len(messages)} messages ({count_user} user, {count_assistant} assistant)")
    print(f"  -> {out_path} (conversation_id: {cid})")
    return cid


def main():
    parser = argparse.ArgumentParser(
        description="Import a Claude Code session into PawFlow conversation format")
    parser.add_argument("session", help="Path to Claude Code .jsonl session file")
    parser.add_argument("--user", default="quentin.anciaux",
                        help="PawFlow user ID (default: quentin.anciaux)")
    parser.add_argument("--agent", default="claude-code",
                        help="Agent name (default: claude-code)")
    parser.add_argument("--out", default=None,
                        help="Output directory (default: data/conversations)")
    args = parser.parse_args()

    convert(args.session, user_id=args.user, agent_name=args.agent,
            output_dir=args.out)


if __name__ == "__main__":
    main()
