"""AgentLoopTask actions — conversation"""

import json
import logging
import time
from pathlib import Path

from tasks.ai.actions._conv_base import (
    _UNHANDLED,
    _archive_manifest,
    _extract_conversation_members,
    _patch_conversation_files,
    _ensure_import_summarizer_binding,
    _restore_filestore_archive,
)

logger = logging.getLogger(__name__)


def _handle_conv_import(self, action, body, store, user_id, flowfile):
    """Conversation actions cluster: _conv_import. Returns result or _UNHANDLED."""
    if action == "conv_compare_branches":
        conv_id = body.get("conversation_id", "")
        branch_a = body.get("branch_a", "").strip()
        branch_b = body.get("branch_b", "").strip()
        if not conv_id or not branch_a or not branch_b:
            flowfile.set_content(json.dumps({"error": "Missing parameters"}).encode())
            return [flowfile]
        result = store.git_compare_branches(conv_id, branch_a, branch_b)
        flowfile.set_content(json.dumps(result).encode())
        return [flowfile]

    if action == "conv_import_cleanup":
        import tempfile
        import shutil
        temp_id = body.get("temp_id", "")
        if temp_id:
            temp_dir = Path(tempfile.gettempdir()) / f"pf_import_{temp_id}"
            shutil.rmtree(temp_dir, ignore_errors=True)
        flowfile.set_content(json.dumps({"ok": True}).encode())
        return [flowfile]

    if action == "conv_import_analyze":
        import tempfile
        import uuid
        fmt = body.get("format", "")
        file_id = body.get("file_id", "")
        if not file_id or fmt not in ("pawflow", "claude_code"):
            flowfile.set_content(json.dumps({"error": "Missing file_id or invalid format"}).encode())
            return [flowfile]
        from core.file_store import FileStore
        fs = FileStore.instance()
        result = fs.get(file_id, user_id=user_id)
        if result is None:
            flowfile.set_content(json.dumps({"error": "Upload not found or expired"}).encode())
            return [flowfile]
        _fname, raw, _ct = result
        # Delete the uploaded file from FileStore — we copy raw to temp
        fs.delete(file_id, user_id=user_id)
        temp_id = uuid.uuid4().hex[:16]
        temp_dir = Path(tempfile.gettempdir()) / f"pf_import_{temp_id}"
        temp_dir.mkdir(parents=True, exist_ok=True)
        (temp_dir / "raw").write_bytes(raw)
        agents_found = []
        message_count = 0
        if fmt == "pawflow":
            import zipfile
            import io
            try:
                with zipfile.ZipFile(io.BytesIO(raw)) as zf:
                    names = zf.namelist()
                    transcript_name = "transcript.jsonl" if "transcript.jsonl" in names else "conversation/transcript.jsonl"
                    if transcript_name not in names:
                        flowfile.set_content(json.dumps({"error": "Not a valid PawFlow archive (missing transcript.jsonl)"}).encode())
                        return [flowfile]
                    manifest = _archive_manifest(zf)
                    # Count messages
                    for line in zf.read(transcript_name).decode("utf-8", errors="replace").splitlines():
                        if line.strip():
                            message_count += 1
                    # Extract agents from extras.json
                    extras_name = "extras.json" if "extras.json" in names else "conversation/extras.json"
                    if extras_name in names:
                        extras = json.loads(zf.read(extras_name))
                        conv_agents = extras.get("conv_agents", {})
                        for name, cfg in conv_agents.items():
                            agents_found.append({"name": name, "definition": cfg.get("definition", name)})
                    filestore_entries = []
                    if "filestore/index.json" in names:
                        try:
                            filestore_entries = json.loads(zf.read("filestore/index.json").decode("utf-8"))
                        except Exception:
                            filestore_entries = []
                    bucket_count = len([
                        n for n in names
                        if (n.startswith("summaries/_shared/") or n.startswith("conversation/summaries/_shared/"))
                        and n.endswith(".json") and not n.endswith("meta.json")
                    ])
                    agent_context_count = len([
                        n for n in names
                        if n.endswith("/context.jsonl")
                    ])
            except zipfile.BadZipFile:
                flowfile.set_content(json.dumps({"error": "Invalid zip file"}).encode())
                return [flowfile]
        elif fmt == "claude_code":
            text = raw.decode("utf-8", errors="replace")
            for line in text.splitlines():
                if line.strip():
                    message_count += 1
            agents_found = [{"name": "claude", "definition": "claude"}]
        flowfile.set_content(json.dumps({
            "ok": True, "temp_id": temp_id, "format": fmt,
            "agents": agents_found, "message_count": message_count,
            "full_archive": bool(fmt == "pawflow"),
            "manifest": manifest if fmt == "pawflow" else {},
            "bucket_count": bucket_count if fmt == "pawflow" else 0,
            "agent_context_count": agent_context_count if fmt == "pawflow" else 0,
            "filestore_count": len(filestore_entries) if fmt == "pawflow" else 0,
            "filestore_bytes": sum(int(x.get("size", 0) or 0) for x in filestore_entries) if fmt == "pawflow" else 0,
        }).encode())
        return [flowfile]

    if action == "conv_import_execute":
        import tempfile
        import uuid as _uuid
        temp_id = body.get("temp_id", "")
        fmt = body.get("format", "")
        agent_mapping = body.get("agent_mapping", {})  # {import_name: {definition, params, llm_service}}
        title = body.get("title", "Imported conversation")
        relay_ids = body.get("relays", []) or []
        default_relay = body.get("default_relay", "") or ""
        restore_filestore = bool(body.get("restore_filestore", False))
        file_id_policy = body.get("file_id_policy", "preserve_or_remap") or "preserve_or_remap"
        # Resolve llm_service -> {provider, model, base_url, containerized}
        # once per agent mapping so imported assistant/tool messages carry
        # the right `source` metadata (name, llm_service, provider, model).
        # Without this the message meta bar is empty on imported convs.
        _src_by_agent = {}
        try:
            from core.service_registry import ServiceRegistry
            _reg = ServiceRegistry.get_instance()
        except Exception:
            _reg = None
        for _ag_name, _ag_cfg in (agent_mapping or {}).items():
            _svc_id = (_ag_cfg or {}).get("llm_service", "") or ""
            _src = {"type": "agent", "name": _ag_name,
                    "llm_service": _svc_id,
                    "provider": "", "model": "", "base_url": "",
                    "containerized": False}
            if _reg and _svc_id:
                try:
                    _sdef = _reg.resolve_definition(_svc_id, user_id=user_id)
                    if _sdef is not None:
                        _cfg = _sdef.config or {}
                        _src["provider"] = _cfg.get("provider", "") or ""
                        _src["model"] = _cfg.get("model", "") or ""
                        _src["base_url"] = _cfg.get("base_url", "") or _cfg.get("api_base", "") or ""
                        _src["containerized"] = bool(_cfg.get("docker_image", ""))
                except Exception:
                    logging.getLogger(__name__).debug("Ignored exception", exc_info=True)
            _src_by_agent[_ag_name] = _src
        if not temp_id:
            flowfile.set_content(json.dumps({"error": "Missing temp_id"}).encode())
            return [flowfile]
        temp_dir = Path(tempfile.gettempdir()) / f"pf_import_{temp_id}"
        raw_file = temp_dir / "raw"
        if not raw_file.exists():
            flowfile.set_content(json.dumps({"error": "Import data expired"}).encode())
            return [flowfile]
        raw = raw_file.read_bytes()
        cid = _uuid.uuid4().hex[:16] + _uuid.uuid4().hex[:16]
        conv_dir = store._store_dir / store._safe_name(user_id) / store._safe_name(cid)
        conv_dir.mkdir(parents=True, exist_ok=True)
        if fmt == "pawflow":
            import zipfile
            import io
            filestore_result = {"restored": 0, "bytes": 0, "file_id_map": {}}
            try:
                with zipfile.ZipFile(io.BytesIO(raw)) as zf:
                    _extract_conversation_members(zf, conv_dir)
                    filestore_result = _restore_filestore_archive(
                        zf, cid, user_id, restore_filestore, file_id_policy)
            except (zipfile.BadZipFile, ValueError) as e:
                import shutil as _shutil
                _shutil.rmtree(conv_dir, ignore_errors=True)
                flowfile.set_content(json.dumps({"error": str(e)}).encode())
                return [flowfile]
            store._cid_user[cid] = user_id  # required for segmented log helpers
            now_ts = time.time()
            # Update extras with new cid, user, and agent mapping
            extras_path = conv_dir / "extras.json"
            if extras_path.exists():
                extras = json.loads(extras_path.read_text(encoding="utf-8"))
            else:
                extras = {}
            extras["conversation_id"] = cid
            extras["user_id"] = user_id
            extras["title"] = title or extras.get("title", "Imported")
            extras["_meta_user_id"] = user_id
            extras["_meta_created_at"] = now_ts
            extras["_meta_updated_at"] = now_ts
            extras["_meta_status"] = extras.get("_meta_status", "idle")
            # Remap agents
            if agent_mapping:
                new_conv_agents = {}
                for imp_name, mapping in agent_mapping.items():
                    new_conv_agents[imp_name] = {
                        "definition": mapping.get("definition", imp_name),
                        "params": mapping.get("params", {"name": imp_name}),
                        "llm_service": mapping.get("llm_service", ""),
                    }
                extras["conv_agents"] = new_conv_agents
                if new_conv_agents:
                    extras["selectedAgent"] = list(new_conv_agents.keys())[0]
            extras_path.write_text(json.dumps(extras, ensure_ascii=False, indent=2), encoding="utf-8")
            _patch_conversation_files(
                conv_dir, cid, user_id,
                filestore_result.get("file_id_map", {}) or {})
        elif fmt == "claude_code":
            # Convert CC JSONL to PawFlow transcript.
            # Real Claude Code session files use type="user"/"assistant".
            # PawFlow's own CC export uses type="human"/"tool_result".
            # We handle both. Structured content blocks (text, tool_use,
            # tool_result) are expanded into proper tool_call / tool
            # messages so the UI renders them like native PawFlow traces
            # instead of empty bubbles.
            text = raw.decode("utf-8", errors="replace")
            transcript_lines = []
            import uuid as _u2
            import re as _re
            # CC transcripts have a single agent — pick the (only) entry from
            # the agent mapping so source/name propagate from the dialog.
            _cc_agent_name = next(iter(agent_mapping.keys()), "claude") if agent_mapping else "claude"
            _cc_source = _src_by_agent.get(_cc_agent_name, {
                "type": "agent", "name": _cc_agent_name,
                "llm_service": "", "provider": "", "model": "",
                "base_url": "", "containerized": False,
            })
            # Monotonic seq assigned to every emitted non-system message.
            # _deserialize_messages in agent_serialization.py refuses
            # entries without seq + ts, so missing seq would make the
            # imported conv unusable by any agent.
            _seq_counter = [0]
            # Raw messages collected for later shared.jsonl population.
            # We don't hand-roll the shared filter — ConversationStore
            # owns that logic (skip tools, skip context injections,
            # strip tool_calls, keep source/badges). See
            # ConversationStore.filter_for_shared.
            raw_msgs = []
            def _emit(obj):
                if obj.get("role") != "system" and not obj.get("seq"):
                    _seq_counter[0] += 1
                    obj["seq"] = _seq_counter[0]
                transcript_lines.append(json.dumps(obj, ensure_ascii=False))
                raw_msgs.append(dict(obj))
            # Claude CLI stuffs meta blocks into the user transcript:
            #   <local-command-caveat>...</local-command-caveat>
            #   <command-name>...</command-name><command-message>...</command-message><command-args>...</command-args>
            #   <local-command-stdout>...</local-command-stdout>
            # These are model-facing scaffolding, not user speech. Skip them.
            _CC_META_RE = _re.compile(
                r"^\s*(?:<local-command-(?:caveat|stdout|stderr)>.*?</local-command-(?:caveat|stdout|stderr)>"
                r"|(?:<command-(?:name|message|args)>.*?</command-(?:name|message|args)>\s*)+)\s*$",
                _re.DOTALL,
            )
            def _is_cc_meta(s):
                return isinstance(s, str) and bool(_CC_META_RE.match(s))
            def _stringify(c):
                if isinstance(c, str):
                    return c
                if isinstance(c, list):
                    parts = []
                    for b in c:
                        if not isinstance(b, dict):
                            continue
                        if b.get("type") == "text":
                            parts.append(b.get("text", ""))
                        elif b.get("type") == "tool_result":
                            parts.append(_stringify(b.get("content", "")))
                    return "\n".join(p for p in parts if p)
                return json.dumps(c, ensure_ascii=False)
            base_ts = time.time() - 1.0
            for idx, line in enumerate(text.splitlines()):
                line = line.strip()
                if not line:
                    continue
                try:
                    entry = json.loads(line)
                except json.JSONDecodeError:
                    continue
                msg_type = entry.get("type", "")
                if msg_type == "summary":
                    continue
                message = entry.get("message", {}) or {}
                content = message.get("content", "")
                raw_ts = entry.get("timestamp") or message.get("timestamp") or ""
                try:
                    if isinstance(raw_ts, (int, float)):
                        ts = float(raw_ts)
                    elif isinstance(raw_ts, str) and raw_ts:
                        ts = time.mktime(time.strptime(raw_ts[:19], "%Y-%m-%dT%H:%M:%S"))
                    else:
                        ts = base_ts + idx * 0.001
                except Exception:
                    ts = base_ts + idx * 0.001
                mid = _u2.uuid4().hex[:12]
                if msg_type in ("human", "user"):
                    # User messages may carry tool_results (CC encodes
                    # tool feedback as user role). Emit those as
                    # role=tool so the UI shows them in-line.
                    if isinstance(content, list):
                        tool_results = [b for b in content if isinstance(b, dict) and b.get("type") == "tool_result"]
                        text_parts = [b.get("text", "") for b in content if isinstance(b, dict) and b.get("type") == "text"]
                        user_text = "\n".join(p for p in text_parts if p)
                        if user_text and not _is_cc_meta(user_text):
                            _emit({"role": "user", "content": user_text, "msg_id": mid, "ts": ts})
                        for tr in tool_results:
                            _emit({
                                "role": "tool",
                                "content": _stringify(tr.get("content", "")),
                                "tool_call_id": tr.get("tool_use_id", ""),
                                "msg_id": _u2.uuid4().hex[:12], "ts": ts,
                            })
                        if not user_text and not tool_results:
                            continue
                    else:
                        if not (content or "").strip():
                            continue
                        if _is_cc_meta(content):
                            continue
                        _emit({"role": "user", "content": content, "msg_id": mid, "ts": ts})
                elif msg_type == "assistant":
                    # Split text blocks and tool_use blocks. Preserve
                    # tool calls so the chat UI renders them as proper
                    # tool-call blocks instead of empty assistant bubbles.
                    tool_calls = []
                    assistant_text = ""
                    if isinstance(content, list):
                        text_parts = []
                        for b in content:
                            if not isinstance(b, dict):
                                continue
                            if b.get("type") == "text":
                                text_parts.append(b.get("text", ""))
                            elif b.get("type") == "tool_use":
                                tool_calls.append({
                                    "id": b.get("id", ""),
                                    "name": b.get("name", ""),
                                    "arguments": b.get("input", {}) or {},
                                })
                        assistant_text = "\n".join(p for p in text_parts if p)
                    else:
                        assistant_text = content or ""
                    if not assistant_text and not tool_calls:
                        continue
                    obj = {"role": "assistant", "content": assistant_text,
                           "source": dict(_cc_source),
                           "msg_id": mid, "ts": ts}
                    if tool_calls:
                        obj["tool_calls"] = tool_calls
                    _emit(obj)
                elif msg_type == "tool_result":
                    _emit({
                        "role": "tool",
                        "content": _stringify(content),
                        "source": dict(_cc_source),
                        "tool_call_id": message.get("tool_use_id", "") or entry.get("tool_use_id", ""),
                        "msg_id": mid, "ts": ts,
                    })
            now_ts = time.time()
            store._cid_user[cid] = user_id  # required for _conv_dir lookups
            transcript_rows = []
            _tool_call_parents = {}
            for _msg in raw_msgs:
                for _row in store._canonical_message_rows(cid, _msg, _tool_call_parents):
                    transcript_rows.append(store._stamp_line(cid, _row))
            store._transcript_log(cid).replace_dicts(transcript_rows)
            # Shared context file: agents resuming the imported conv read
            # their LLM context from here (or from their own agent dir, which
            # falls back to shared). Without this file the "Shared" view
            # shows "diverged / no context" and the agent has no memory.
            # Use ConversationStore's filter + shared transform so imported
            # convs match native appends exactly: no tool/detail rows, agent
            # turns prefixed for the agent-neutral shared view.
            store._cid_user[cid] = user_id  # required for _conv_dir lookups
            shared_candidates = store.filter_for_shared(transcript_rows)
            shared_msgs = [store._transform_for_shared(m) for m in shared_candidates]
            if shared_msgs:
                store._append_shared_ctx(cid, shared_msgs)
            # Create minimal extras
            agent_name = list(agent_mapping.keys())[0] if agent_mapping else "claude"
            agent_cfg = agent_mapping.get(agent_name, {"definition": "claude", "params": {"name": agent_name}, "llm_service": ""})
            extras = {
                "conversation_id": cid,
                "user_id": user_id,
                "title": title,
                "selectedAgent": agent_name,
                "_meta_user_id": user_id,
                "_meta_created_at": now_ts,
                "_meta_updated_at": now_ts,
                "_meta_status": "idle",
                "conv_agents": {
                    agent_name: {
                        "definition": agent_cfg.get("definition", "claude"),
                        "params": agent_cfg.get("params", {"name": agent_name}),
                        "llm_service": agent_cfg.get("llm_service", ""),
                    }
                },
            }
            (conv_dir / "extras.json").write_text(json.dumps(extras, ensure_ascii=False, indent=2), encoding="utf-8")
        # Init git and register in the cache so list_conversations
        # picks up the new conversation immediately.
        store._cid_user[cid] = user_id
        store._git_init(cid)
        store._reload_cache(cid)
        summarizer_binding = _ensure_import_summarizer_binding(cid, user_id)
        # Relay bindings (mirrors create_conversation in agent_resource.py).
        if relay_ids:
            from core.relay_bindings import link_relay, set_default_relay
            for rid in relay_ids:
                link_relay(cid, rid)
            if default_relay and default_relay in relay_ids:
                set_default_relay(cid, default_relay)
        # Cleanup temp
        import shutil
        shutil.rmtree(temp_dir, ignore_errors=True)
        result = {"ok": True, "conversation_id": cid}
        if summarizer_binding:
            result["summarizer_binding"] = summarizer_binding
        if fmt == "pawflow":
            result["filestore_restored"] = filestore_result.get("restored", 0)
            result["filestore_bytes"] = filestore_result.get("bytes", 0)
            result["filestore_remapped"] = len(filestore_result.get("file_id_map", {}) or {})
        flowfile.set_content(json.dumps(result).encode())
        return [flowfile]

    return _UNHANDLED
