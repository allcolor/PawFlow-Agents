"""AgentLoopTask actions — conversation"""

import json
import logging

from core.llm_client import LLMMessage
from tasks.ai.actions._conv_base import (
    _UNHANDLED,
)

logger = logging.getLogger(__name__)


def _handle_conv_core(self, action, body, store, user_id, flowfile):
    """Conversation actions cluster: _conv_core. Returns result or _UNHANDLED."""
    if action == "list_conversations":
        convs = store.list_conversations(user_id=user_id)
        # Override persisted status with real-time active agent state
        try:
            from tasks.ai.agent_loop import AgentLoopTask
            inst = AgentLoopTask._live_instance
            if inst:
                _active_cids = set()
                with inst._active_contexts_lock:
                    for k in inst._active_contexts:
                        _active_cids.add(k.split(":")[0])
                for c in convs:
                    c["status"] = "active" if c["conversation_id"] in _active_cids else "idle"
        except Exception:
            logging.getLogger(__name__).debug("Ignored exception", exc_info=True)
        for c in convs:
            branch = store.git_current_branch(c["conversation_id"])
            c["branch"] = branch or ""
            try:
                c["encryption"] = store.encryption_status(c["conversation_id"])["state"]
            except Exception:
                c["encryption"] = "off"
        result = json.dumps({"conversations": convs}, ensure_ascii=False)
        flowfile.set_content(result.encode("utf-8"))
        return [flowfile]

    if action == "load_history":
        conv_id = body.get("conversation_id", "")
        limit = int(body.get("limit", 50))
        offset = int(body.get("offset", 0))
        if not conv_id:
            flowfile.set_content(json.dumps({"error": "Missing conversation_id"}).encode())
            flowfile.set_attribute("http.response.status", "400")
            return [flowfile]

        # Encrypted-and-locked: do not return ciphertext rows. Tell the client
        # to show the unlock banner instead of rendering enc: blobs.
        try:
            enc = store.encryption_status(conv_id)
        except Exception:
            enc = {"state": "off"}
        if enc.get("state") == "locked":
            flowfile.set_content(json.dumps({
                "conversation_id": conv_id, "messages": [], "message_count": 0,
                "encrypted_locked": True, "encryption": "locked",
            }, ensure_ascii=False).encode("utf-8"))
            return [flowfile]

        page = store.load_page(conv_id, limit=limit, offset=offset, user_id=user_id)
        if page is None:
            flowfile.set_content(json.dumps({"error": "Conversation not found"}).encode())
            flowfile.set_attribute("http.response.status", "404")
            return [flowfile]

        raw_messages = page["messages"]
        raw_count = len(raw_messages)
        history = self._classify_messages_for_display(raw_messages)
        extras = store.get_extras_snapshot(conv_id)
        nicknames = extras.get("agent_nicknames") or {}
        active_res = extras.get("active_resources") or {}
        active_res = self._ensure_active_agent(conv_id, active_res, user_id)
        active_agent = active_res.get("agent", "")
        custom_css = extras.get("custom_css") or ""
        def _resolve_chat_flag(key: str, default: str = "true") -> bool:
            try:
                from core.expression import resolve_expression
                raw = resolve_expression(
                    "$" + '{chat.' + key + ':default("' + default + '")}',
                    owner=user_id,
                    conversation_id=conv_id,
                )
            except Exception:
                raw = default
            return str(raw).strip().lower() in ("1", "true", "yes", "on")

        group_technical_messages = _resolve_chat_flag("group_technical_messages")
        group_task_messages = _resolve_chat_flag("group_task_messages")
        group_delegate_messages = _resolve_chat_flag("group_delegate_messages")

        result = json.dumps({
            "conversation_id": conv_id,
            "messages": history,
            "message_count": page["total_count"],
            "has_more": page["has_more"],
            "offset": page["offset"],
            "raw_count": raw_count,
            "nicknames": nicknames,
            "active_agent": active_agent,
            "custom_css": custom_css,
            "group_technical_messages": group_technical_messages,
            "group_task_messages": group_task_messages,
            "group_delegate_messages": group_delegate_messages,
        }, ensure_ascii=False)
        flowfile.set_content(result.encode("utf-8"))
        return [flowfile]

    if action == "set_conv_title":
        conv_id = body.get("conversation_id", "")
        title = body.get("title", "").strip()
        if not conv_id or not title:
            flowfile.set_content(json.dumps({"error": "Missing conversation_id or title"}).encode())
            return [flowfile]
        store.set_extra(conv_id, "title", title, user_id=user_id)
        flowfile.set_content(json.dumps({"ok": True, "title": title}).encode())
        return [flowfile]

    if action == "search_messages":
        conv_id = body.get("conversation_id", "")
        query = str(body.get("query", "") or "").strip()
        if not conv_id or not query:
            flowfile.set_content(json.dumps({
                "error": "Missing conversation_id or query",
            }).encode())
            flowfile.set_attribute("http.response.status", "400")
            return [flowfile]
        messages = store.load(conv_id, user_id=user_id)
        if messages is None:
            flowfile.set_content(json.dumps({
                "error": "Conversation not found",
            }).encode())
            flowfile.set_attribute("http.response.status", "404")
            return [flowfile]
        needle = query.casefold()
        matches = []
        for index, message in enumerate(messages):
            if isinstance(message, dict):
                content = message.get("content", "")
                role = message.get("role") or message.get("type") or "?"
                msg_id = message.get("msg_id") or message.get("id") or ""
            else:
                content = getattr(message, "content", "")
                role = getattr(message, "role", "?")
                msg_id = getattr(message, "msg_id", "")
            searchable = content if isinstance(content, str) else json.dumps(
                content, ensure_ascii=False)
            if needle not in searchable.casefold():
                continue
            compact = " ".join(searchable.split())
            matches.append({
                "index": index, "msg_id": msg_id, "role": role,
                "preview": compact[:240],
            })
            if len(matches) >= 100:
                break
        flowfile.set_content(json.dumps({
            "query": query, "matches": matches, "count": len(matches),
            "truncated": len(matches) >= 100,
        }, ensure_ascii=False).encode())
        return [flowfile]

    # -- Server-relay workspace encryption (relay_workspace_*) --------
    if action.startswith("relay_workspace_"):
        import core.workspace_encryption as _we
        from core.key_vault import KeyUnwrapError
        from core.conversation_store import ConversationLockedError
        conv_id = body.get("conversation_id", "")
        if not conv_id:
            flowfile.set_content(json.dumps({"error": "Missing conversation_id"}).encode())
            flowfile.set_attribute("http.response.status", "400")
            return [flowfile]
        session_id = flowfile.get_attribute("auth.session_id") or ""
        passphrase = body.get("passphrase", "") or ""

        def _wreply(obj, status="200"):
            flowfile.set_content(json.dumps(obj, ensure_ascii=False).encode("utf-8"))
            flowfile.set_attribute("http.response.status", status)
            return [flowfile]

        def _respawn_if_running():
            # Apply a workspace-encryption change to a live relay: a running
            # relay must restart to pick up the cipher-store layout / DEK env.
            # Best-effort, never fatal to the action result.
            try:
                from core.server_relay_manager import ServerRelayManager
                mgr = ServerRelayManager.get_instance()
                meta = mgr.get_metadata(conv_id)
                if meta and mgr._is_container_running(meta.get("container_id", "")):
                    mgr.destroy(conv_id)
                    mgr.ensure(conv_id, user_id)
            except Exception:
                logging.getLogger(__name__).debug("workspace relay respawn skipped", exc_info=True)

        try:
            relay_meta = None
            try:
                from core.server_relay_manager import ServerRelayManager
                relay_meta = ServerRelayManager.get_instance().get_metadata(conv_id)
            except Exception:
                relay_meta = None
            if action == "relay_workspace_encrypt":
                if not passphrase:
                    return _wreply({"error": "passphrase required"}, "400")
                _st = _we.enable(store, conv_id, passphrase, relay_meta=relay_meta, session_id=session_id)
                _respawn_if_running()
                return _wreply(_st)
            if action == "relay_workspace_unlock":
                _we.unlock(store, conv_id, passphrase, session_id=session_id)
                _respawn_if_running()
                return _wreply(_we.status(store, conv_id))
            if action == "relay_workspace_lock":
                _we.lock(store, conv_id)
                return _wreply(_we.status(store, conv_id))
            if action == "relay_workspace_encrypt_off":
                _st = _we.disable(store, conv_id)
                _respawn_if_running()
                return _wreply(_st)
        except KeyUnwrapError:
            return _wreply({"ok": False, "error": "wrong_passphrase"})
        except ConversationLockedError as e:
            return _wreply({"ok": False, "error": "locked", "detail": str(e)})
        except ValueError as e:
            return _wreply({"ok": False, "error": str(e)}, "400")
        return _wreply({"error": f"unknown workspace action: {action}"}, "400")

    # ── Encryption at rest (conv_encrypt_*) ───────────────────────────
    if action.startswith("conv_encrypt_"):
        from core.key_vault import KeyUnwrapError
        from core.conversation_store import ConversationLockedError
        conv_id = body.get("conversation_id", "")
        if not conv_id:
            flowfile.set_content(json.dumps({"error": "Missing conversation_id"}).encode())
            flowfile.set_attribute("http.response.status", "400")
            return [flowfile]
        session_id = flowfile.get_attribute("auth.session_id") or ""
        passphrase = body.get("passphrase", "") or ""

        def _reply(obj, status="200"):
            flowfile.set_content(json.dumps(obj, ensure_ascii=False).encode("utf-8"))
            flowfile.set_attribute("http.response.status", status)
            return [flowfile]

        try:
            if action == "conv_encrypt_status":
                return _reply(store.encryption_status(conv_id))
            if action == "conv_encrypt_enable":
                if not passphrase:
                    return _reply({"error": "passphrase required"}, "400")
                return _reply(store.enable_encryption(
                    conv_id, passphrase, session_id=session_id))
            if action == "conv_encrypt_unlock":
                store.unlock_encryption(conv_id, passphrase, session_id=session_id)
                return _reply(store.encryption_status(conv_id))
            if action == "conv_encrypt_lock":
                store.lock_encryption(conv_id)
                return _reply(store.encryption_status(conv_id))
            if action == "conv_encrypt_disable":
                return _reply(store.disable_encryption(conv_id, session_id=session_id))
            if action == "conv_encrypt_passwd":
                store.change_encryption_passphrase(
                    conv_id, body.get("old_passphrase", "") or "",
                    body.get("new_passphrase", "") or "")
                return _reply({"ok": True})
            if action == "conv_encrypt_set_relay":
                pub = body.get("relay_pubkey", "") or ""
                if not pub:
                    return _reply({"error": "relay_pubkey required"}, "400")
                return _reply(store.set_conv_relay(conv_id, pub))
            if action == "conv_encrypt_remove_relay":
                return _reply(store.remove_conv_relay(conv_id))
            if action == "conv_encrypt_set_escrow":
                rp = body.get("recovery_passphrase", "") or ""
                if not rp:
                    return _reply({"error": "recovery_passphrase required"}, "400")
                return _reply(store.set_conv_escrow(conv_id, rp))
            if action == "conv_encrypt_remove_escrow":
                return _reply(store.remove_conv_escrow(conv_id))
            if action == "conv_encrypt_recover":
                store.unlock_encryption_with_recovery(
                    conv_id, body.get("recovery_passphrase", "") or "",
                    session_id=session_id)
                return _reply(store.encryption_status(conv_id))
        except KeyUnwrapError:
            # AEAD tag failure — wrong passphrase. Inline, no lockout reveal.
            return _reply({"ok": False, "error": "wrong_passphrase"})
        except ConversationLockedError as e:
            return _reply({"ok": False, "error": "locked", "detail": str(e)})
        except ValueError as e:
            return _reply({"ok": False, "error": str(e)}, "400")
        return _reply({"error": f"unknown encryption action: {action}"}, "400")

    if action == "delete_conversation":
        conv_id = body.get("conversation_id", "")
        if not conv_id:
            flowfile.set_content(json.dumps({"error": "Missing conversation_id"}).encode())
            flowfile.set_attribute("http.response.status", "400")
            return [flowfile]
        # Collect file IDs from conversation before deleting
        history = store.load(conv_id, user_id=user_id)
        if history:
            self._cleanup_conversation_files(history)
        # Cascade cleanup: flows, dynamic tools, secrets
        self._cleanup_conversation_resources(conv_id)
        deleted = store.delete(conv_id, user_id=user_id)
        logger.info(f"[action] delete_conversation {conv_id}: deleted={deleted}, "
                    f"user_id={user_id}")
        result = json.dumps({"deleted": deleted, "conversation_id": conv_id})
        flowfile.set_content(result.encode("utf-8"))
        return [flowfile]

    if action == "resume_conversation":
        conv_id = body.get("conversation_id", "")
        _rs_agent = body.get("agent_name", "")
        max_summary_tokens = int(body.get("max_tokens", 500))
        if not conv_id:
            flowfile.set_content(json.dumps({"error": "Missing conversation_id"}).encode())
            flowfile.set_attribute("http.response.status", "400")
            return [flowfile]
        _rs_msgs = store.load(conv_id, user_id=user_id)
        if not _rs_msgs:
            flowfile.set_content(json.dumps({"error": "Conversation not found"}).encode())
            flowfile.set_attribute("http.response.status", "404")
            return [flowfile]
        # Resolve LLM client
        _summ_client, _, _ = self._get_summarizer_client(user_id, conversation_id=conv_id)
        _rs_client = _summ_client
        if not _rs_client:
            _rs_svc = self._resolve_service_param(
                "llm_service", user_id, conv_id) or "default"
            _rs_client, _ = self._resolve_client(_rs_svc, user_id)
        if not _rs_client:
            flowfile.set_content(json.dumps({"error": "No LLM service for summarization"}).encode())
            return [flowfile]

        def _do_resume():
            deserialized = self._deserialize_messages(_rs_msgs, conversation_id=conv_id)
            content_msgs = [m for m in deserialized if m.role != "system"]
            context_max = int(self.config.get("max_context_size", 64000))
            # Resolve agent's max_tokens
            if _rs_agent:
                _, _, _sv = self._resolve_agent_client(_rs_agent, user_id, conv_id)
                if _sv:
                    _v = int((getattr(_sv, 'config', {}) or {}).get("max_context_size", 0))
                    if _v:
                        context_max = _v
            summary = self._summarize_messages(
                content_msgs, _rs_client, context_max,
                target_tokens=max_summary_tokens,
                conversation_id=conv_id,
            )
            sys_prompt = self.config.get("system_prompt", "You are a helpful assistant.")
            # NO datetime in system prompt — breaks KV cache
            new_context = [
                LLMMessage(role="system", content=sys_prompt,
                            conversation_id=conv_id),
                LLMMessage(role="user",
                           content=f"[Conversation summary â€” earlier messages compacted]\n\n{summary}",
                           conversation_id=conv_id),
                LLMMessage(role="assistant",
                           content="Understood. I have the context from our earlier conversation. Continuing from where we left off.",
                           source={"type": "context"},
                           conversation_id=conv_id),
            ]
            store.save_agent_context(conv_id, _rs_agent, self._serialize_messages(new_context))
            return {"summary_length": len(summary),
                    "messages_summarized": len(_rs_msgs),
                    "agent": _rs_agent or "shared"}

        _rs_lock_agent = (
            "" if _rs_agent in ("", "ALL", "shared") else _rs_agent)
        return self._run_bg_context_op(
            conv_id, "summary", _do_resume, flowfile,
            agent_name=_rs_lock_agent)

    if action == "ping":
        # Keep-alive: session renewal happens in validateSessionAuth upstream
        flowfile.set_content(json.dumps({"status": "ok"}).encode())
        return [flowfile]

    if action == "poll":
        # Efficient delta check: client sends last known message_count,
        # server returns new messages only if count increased.
        conv_id = body.get("conversation_id", "")
        last_count = int(body.get("last_count", 0))
        if not conv_id:
            flowfile.set_content(json.dumps({"new_messages": []}).encode())
            return [flowfile]
        current_count = int(store.get_extra_snapshot(
            conv_id, "_meta_msg_count", last_count) or last_count)
        if current_count <= last_count:
            flowfile.set_content(json.dumps({
                "new_messages": [], "message_count": current_count,
            }).encode())
            return [flowfile]
        # Load only the new messages (max 50) — never the full conversation
        delta = current_count - last_count
        if delta > 50:
            # Too many missed — client should just update count, not render all
            delta = 50
        page = store.load_page(conv_id, limit=delta, offset=0, user_id=user_id)
        if page is None:
            flowfile.set_content(json.dumps({
                "new_messages": [], "message_count": current_count,
            }).encode())
            return [flowfile]
        new_classified = self._classify_messages_for_display(page["messages"])
        flowfile.set_content(json.dumps({
            "new_messages": new_classified,
            "message_count": current_count,
        }, ensure_ascii=False).encode())
        return [flowfile]

    return _UNHANDLED
