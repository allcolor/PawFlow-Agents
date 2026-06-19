"""AgentLoopTask actions — agent resource"""

import json
import logging
from tasks.ai.actions._agentres_base import (
    _UNHANDLED,
    _decode_skill_package_files,
)

logger = logging.getLogger(__name__)


def _handle_agentres_k4(self, action, body, store, user_id, flowfile):
    """agent_resource cluster _agentres_k4. Returns result or _UNHANDLED."""
    if action == "rename_voice_clone":
        # Rename a voice clone entry in place. provider voice_id,
        # ref_audio_fid and cache tags are unchanged — only the
        # user-visible identifier changes.
        old = body.get("name", "").strip()
        new = body.get("new_name", "").strip()
        if not old or not new:
            flowfile.set_content(json.dumps({
                "error": "Missing name or new_name",
            }).encode())
            return [flowfile]
        from core import voice_clone_cache as _vcache
        new_safe = _vcache.safe_name(new)
        entry = _vcache.get_by_name(user_id, old)
        if entry is None:
            flowfile.set_content(json.dumps({
                "error": f"voice clone {old!r} not found",
            }).encode())
            flowfile.set_attribute("http.response.status", "404")
            return [flowfile]
        if new_safe == entry.get("name"):
            flowfile.set_content(json.dumps({
                "ok": True, "name": new_safe, "unchanged": True,
            }).encode())
            return [flowfile]
        if _vcache.get_by_name(user_id, new_safe) is not None:
            flowfile.set_content(json.dumps({
                "error": f"voice clone {new_safe!r} already exists",
            }).encode())
            flowfile.set_attribute("http.response.status", "409")
            return [flowfile]
        renamed = dict(entry)
        renamed["name"] = new_safe
        renamed.pop("created_at", None)
        _vcache.save(user_id, renamed)
        _vcache.delete(user_id, old)
        flowfile.set_content(json.dumps({
            "ok": True, "name": new_safe, "previous_name": old,
        }).encode())
        return [flowfile]

    if action == "update_resource":
        rtype = body.get("resource_type", "")
        rname = body.get("name", "").strip()
        data = body.get("data", {})
        scope = body.get("scope", "user")
        if scope == "global" and "admin" not in (flowfile.get_attribute("http.auth.roles") or ""):
            flowfile.set_content(json.dumps({"error": "Requires admin role for global scope"}).encode())
            flowfile.set_attribute("http.response.status", "403")
            return [flowfile]
        if not rtype or not rname:
            flowfile.set_content(json.dumps({"error": "Missing resource_type or name"}).encode())
            return [flowfile]
        # Agent definitions in the repository hold only prompt + description.
        # Runtime params (llm_service, model, tools, skills, max_depth, timeout)
        # live in conv_agents — edited via update_agent_conv_config.
        if rtype == "agent":
            data = {k: v for k, v in data.items() if k in ("prompt", "description")}
        from core.resource_store import ResourceStore
        rs = ResourceStore.instance()
        uid = user_id
        target_uid = "__global__" if scope == "global" else uid
        try:
            if rtype == "skill":
                _skill_conv = body.get("conversation_id", "") if scope == "conversation" else ""
                existing = rs.get("skill", rname, target_uid,
                                  conversation_id=_skill_conv) or {}
                merged = {k: v for k, v in existing.items()
                          if not str(k).startswith("_")}
                merged.update(data if isinstance(data, dict) else {})
                from core.review_bindings import attach_review_metadata, review_for_write
                from core.package_review import ReviewBlocked
                # Decode bundled assets (only when re-uploaded); leaving
                # them out keeps the skill's existing assets on disk.
                _pkg_files = _decode_skill_package_files(
                    merged.get("package_files"))
                if _pkg_files:
                    data["package_files"] = _pkg_files
                else:
                    data.pop("package_files", None)
                merged.pop("package_files", None)
                try:
                    review_meta = review_for_write(
                        merged,
                        operation="update",
                        user_id=target_uid,
                        conversation_id=_skill_conv,
                        package_files=_pkg_files,
                        force=bool(body.get("force", False)),
                    )
                except ReviewBlocked as _rb:
                    flowfile.set_content(json.dumps({
                        "requires_confirmation": True,
                        "name": rname,
                        "review": _rb.review,
                        "message": str(_rb),
                    }, ensure_ascii=False).encode())
                    return [flowfile]
                if review_meta:
                    data = attach_review_metadata(data, review_meta)
            scope_kwargs = {"conversation_id": body.get("conversation_id", "")} if scope == "conversation" else {}
            rs.update(rtype, rname, target_uid, data, **scope_kwargs)
            if rtype == "skill" and body.get("conversation_id", ""):
                from core.skill_lifecycle import notify_skill_updated
                updated = rs.get_any(
                    "skill", rname, uid,
                    conversation_id=body.get("conversation_id", "")) or data
                notify_skill_updated(
                    rname, updated, uid, body.get("conversation_id", ""),
                    resource_store=rs, conversation_store=store)
            flowfile.set_content(json.dumps({"ok": True}).encode())
        except Exception as e:
            flowfile.set_content(json.dumps({"error": str(e)}).encode())
        return [flowfile]

    if action == "create_resource":
        rtype = body.get("resource_type", "")
        rname = body.get("name", "").strip()
        data = body.get("data", {})
        conv_id = body.get("conversation_id", "")
        scope = body.get("scope", "user")
        if scope == "global" and "admin" not in (flowfile.get_attribute("http.auth.roles") or ""):
            flowfile.set_content(json.dumps({"error": "Requires admin role for global scope"}).encode())
            flowfile.set_attribute("http.response.status", "403")
            return [flowfile]
        if scope == "conversation" and not conv_id:
            flowfile.set_content(json.dumps({"error": "Missing conversation_id for conversation scope"}).encode())
            flowfile.set_attribute("http.response.status", "400")
            return [flowfile]
        if not rtype or not rname:
            flowfile.set_content(json.dumps({"error": "Missing resource_type or name"}).encode())
            return [flowfile]
        if rtype == "agent":
            data = {k: v for k, v in data.items() if k in ("prompt", "description")}
        from core.resource_store import ResourceStore
        rs = ResourceStore.instance()
        # Admin may create on behalf of another owner. Default = caller.
        from core import admin_scope
        try:
            _owner_user, _owner_conv = admin_scope.effective_owner(
                body, user_id, conv_id, flowfile, scope)
        except PermissionError as _pe:
            flowfile.set_content(json.dumps({"error": str(_pe)}).encode())
            flowfile.set_attribute("http.response.status", "403")
            return [flowfile]
        except ValueError as _ve:
            flowfile.set_content(json.dumps({"error": str(_ve)}).encode())
            flowfile.set_attribute("http.response.status", "400")
            return [flowfile]
        uid = _owner_user or user_id
        if scope == "conversation":
            conv_id = _owner_conv or conv_id
        target_uid = "__global__" if scope == "global" else uid
        if rtype == "task_def":
            data.setdefault("created_by", uid)
        try:
            if rtype == "skill":
                from core.review_bindings import attach_review_metadata, review_for_write
                from core.package_review import ReviewBlocked
                _pkg_files = _decode_skill_package_files(
                    data.get("package_files"))
                if _pkg_files:
                    data["package_files"] = _pkg_files
                else:
                    data.pop("package_files", None)
                _review_subject = {k: v for k, v in data.items()
                                   if k != "package_files"}
                try:
                    review_meta = review_for_write(
                        _review_subject,
                        operation="create",
                        user_id=target_uid,
                        conversation_id=conv_id if scope == "conversation" else "",
                        package_files=_pkg_files,
                        force=bool(body.get("force", False)),
                    )
                except ReviewBlocked as _rb:
                    flowfile.set_content(json.dumps({
                        "requires_confirmation": True,
                        "name": rname,
                        "review": _rb.review,
                        "message": str(_rb),
                    }, ensure_ascii=False).encode())
                    return [flowfile]
                if review_meta:
                    data = attach_review_metadata(data, review_meta)
            if rtype == "task_def" and scope == "conversation" and conv_id:
                from core.conversation_store import ConversationStore
                cs = ConversationStore.instance()
                conv_defs = cs.get_extra(conv_id, "conversation_task_defs") or {}
                conv_defs[rname] = data
                cs.set_extra(conv_id, "conversation_task_defs", conv_defs)
            else:
                scope_kwargs = {"conversation_id": conv_id} if scope == "conversation" and conv_id else {}
                rs.create(rtype, rname, target_uid, data, **scope_kwargs)
                if rtype == "agent" and scope == "conversation" and conv_id:
                    from core.conv_agent_config import add_agent_to_conv
                    add_agent_to_conv(conv_id, rname, definition=rname)
            flowfile.set_content(json.dumps({"ok": True, "scope": scope}).encode())
        except Exception as e:
            flowfile.set_content(json.dumps({"error": str(e)}).encode())
        return [flowfile]

    if action == "get_conversation_hooks":
        conv_id = body.get("conversation_id", "")
        if not conv_id:
            flowfile.set_content(json.dumps({"error": "Missing conversation_id"}).encode())
            flowfile.set_attribute("http.response.status", "400")
            return [flowfile]
        from core.resource_store import ResourceStore
        from core.agent_hooks import VALID_AGENT_HOOK_EVENTS
        hooks = ResourceStore.instance().list_all(
            "agent_hook", user_id, conversation_id=conv_id)
        bindings = store.get_extra(conv_id, "conversation_hooks") or []
        flowfile.set_content(json.dumps({
            "hooks": hooks,
            "bindings": bindings,
            "events": sorted(VALID_AGENT_HOOK_EVENTS),
        }, ensure_ascii=False).encode())
        return [flowfile]

    if action == "update_conversation_hooks":
        conv_id = body.get("conversation_id", "")
        bindings = body.get("bindings", [])
        if not conv_id:
            flowfile.set_content(json.dumps({"error": "Missing conversation_id"}).encode())
            flowfile.set_attribute("http.response.status", "400")
            return [flowfile]
        if not isinstance(bindings, list):
            flowfile.set_content(json.dumps({"error": "bindings must be a list"}).encode())
            flowfile.set_attribute("http.response.status", "400")
            return [flowfile]
        from core.agent_hooks import VALID_AGENT_HOOK_EVENTS
        from core.resource_store import ResourceStore
        installed = {
            str(h.get("name") or "")
            for h in ResourceStore.instance().list_all(
                "agent_hook", user_id, conversation_id=conv_id)
        }
        clean = []
        for item in bindings:
            if isinstance(item, str):
                item = {"name": item}
            if not isinstance(item, dict):
                continue
            name = str(item.get("name") or item.get("ref") or "").strip()
            if not name:
                continue
            if name not in installed:
                flowfile.set_content(json.dumps({
                    "error": f"agent_hook '{name}' is not installed",
                }).encode())
                flowfile.set_attribute("http.response.status", "400")
                return [flowfile]
            events = item.get("events") or []
            if isinstance(events, str):
                events = [events]
            events = [str(e) for e in events if str(e)] if isinstance(events, list) else []
            invalid_events = [e for e in events if e not in VALID_AGENT_HOOK_EVENTS]
            if invalid_events:
                flowfile.set_content(json.dumps({
                    "error": "invalid agent hook events: " + ", ".join(invalid_events),
                }).encode())
                flowfile.set_attribute("http.response.status", "400")
                return [flowfile]
            clean.append({
                "name": name,
                "enabled": bool(item.get("enabled", True)),
                "events": events,
                "agents": item.get("agents") or [],
                "tools": item.get("tools") or [],
                "priority": int(item.get("priority", 0) or 0),
                "fail_policy": str(item.get("fail_policy") or "open"),
            })
        store.set_extra(conv_id, "conversation_hooks", clean, user_id=user_id)
        flowfile.set_content(json.dumps({"ok": True, "bindings": clean}).encode())
        return [flowfile]

    if action == "delete_resource":
        rtype = body.get("resource_type", "")
        rname = body.get("name", "").strip()
        scope = body.get("scope", "user")
        conv_id = body.get("conversation_id", "")
        if scope == "global" and "admin" not in (flowfile.get_attribute("http.auth.roles") or ""):
            flowfile.set_content(json.dumps({"error": "Requires admin role for global scope"}).encode())
            flowfile.set_attribute("http.response.status", "403")
            return [flowfile]
        if not rtype or not rname:
            flowfile.set_content(json.dumps({"error": "Missing resource_type or name"}).encode())
            return [flowfile]
        from core.resource_store import ResourceStore
        rs = ResourceStore.instance()
        uid = user_id
        target_uid = "__global__" if scope == "global" else uid
        if rtype == "task_def" and scope == "conversation" and conv_id:
            from core.conversation_store import ConversationStore
            cs = ConversationStore.instance()
            conv_defs = cs.get_extra(conv_id, "conversation_task_defs") or {}
            deleted = rname in conv_defs
            if deleted:
                del conv_defs[rname]
                cs.set_extra(conv_id, "conversation_task_defs", conv_defs)
        else:
            scope_kwargs = {"conversation_id": conv_id} if scope == "conversation" and conv_id else {}
            deleted = rs.delete(rtype, rname, target_uid, **scope_kwargs)
            if deleted and rtype == "skill":
                from core.skill_lifecycle import remove_skill_assignments
                remove_skill_assignments(
                    rname, uid, conv_id, resource_store=rs,
                    conversation_store=store, source="skill_delete")
        flowfile.set_content(json.dumps({"ok": True, "deleted": deleted}).encode())
        return [flowfile]

    if action == "copy_resource_scope":
        rtype = body.get("resource_type", "")
        rname = body.get("name", "").strip()
        from_scope = body.get("from_scope", "")
        target_scope = body.get("target_scope", "")
        if not rtype or not rname or not target_scope:
            flowfile.set_content(json.dumps({"error": "Missing resource_type, name, or target_scope"}).encode())
            return [flowfile]
        from core.resource_store import ResourceStore
        rs = ResourceStore.instance()
        uid = user_id
        conv_id = body.get("conversation_id", "")
        if (target_scope == "global" or from_scope == "global") and "admin" not in (flowfile.get_attribute("http.auth.roles") or ""):
            flowfile.set_content(json.dumps({"error": "Requires admin role for global scope"}).encode())
            flowfile.set_attribute("http.response.status", "403")
            return [flowfile]
        if (target_scope == "conversation" or from_scope == "conversation") and not conv_id:
            flowfile.set_content(json.dumps({"error": "Missing conversation_id for conversation scope"}).encode())
            flowfile.set_attribute("http.response.status", "400")
            return [flowfile]
        # The user/conv side belongs to an owner; an admin may target another
        # user (e.g. demote a global resource down to user X / promote user X's
        # resource to global). Default = caller. Used for BOTH the source read
        # and the destination write so the resource lands on the right user.
        from core import admin_scope
        _owner_scope = ("conv" if (target_scope == "conversation"
                                   or from_scope == "conversation") else "user")
        try:
            _owner_user, _owner_conv = admin_scope.effective_owner(
                body, user_id, conv_id, flowfile, _owner_scope)
        except PermissionError as _pe:
            flowfile.set_content(json.dumps({"error": str(_pe)}).encode())
            flowfile.set_attribute("http.response.status", "403")
            return [flowfile]
        except ValueError as _ve:
            flowfile.set_content(json.dumps({"error": str(_ve)}).encode())
            flowfile.set_attribute("http.response.status", "400")
            return [flowfile]
        uid = _owner_user or user_id
        if target_scope == "conversation" or from_scope == "conversation":
            conv_id = _owner_conv or conv_id
        if from_scope == "global":
            item = rs.get(rtype, rname, "__global__")
            if item is not None:
                item["_scope"] = "global"
        elif from_scope == "user":
            item = rs.get(rtype, rname, uid)
            if item is not None:
                item["_scope"] = "user"
        elif from_scope == "conversation" and rtype == "task_def" and conv_id:
            from core.conversation_store import ConversationStore
            conv_defs = ConversationStore.instance().get_extra(conv_id, "conversation_task_defs") or {}
            item = dict(conv_defs.get(rname) or {}) if rname in conv_defs else None
            if item is not None:
                item["name"] = rname
                item["_scope"] = "conversation"
        elif from_scope == "conversation":
            item = rs.get(rtype, rname, uid, conversation_id=conv_id)
            if item is not None:
                item["_scope"] = "conversation"
        else:
            item = rs.get_any(rtype, rname, uid, conversation_id=conv_id)
        if not item:
            flowfile.set_content(json.dumps({"error": f"{rtype} '{rname}' not found"}).encode())
            return [flowfile]
        target_uid = "__global__" if target_scope == "global" else uid
        data = {k: v for k, v in item.items() if k not in ("name", "_scope")}
        source_scope = item.get("_scope", "")
        try:
            if target_scope == "conversation" and conv_id and rtype == "task_def":
                # Copy into conversation scope
                from core.conversation_store import ConversationStore
                cs = ConversationStore.instance()
                conv_defs = cs.get_extra(conv_id, "conversation_task_defs") or {}
                conv_defs[rname] = data
                cs.set_extra(conv_id, "conversation_task_defs", conv_defs)
            else:
                scope_kwargs = {"conversation_id": conv_id} if target_scope == "conversation" and conv_id else {}
                rs.create(rtype, rname, target_uid, data, **scope_kwargs)
            if from_scope and source_scope != target_scope:
                if source_scope == "conversation" and conv_id and rtype == "task_def":
                    from core.conversation_store import ConversationStore
                    cs = ConversationStore.instance()
                    conv_defs = cs.get_extra(conv_id, "conversation_task_defs") or {}
                    conv_defs.pop(rname, None)
                    cs.set_extra(conv_id, "conversation_task_defs", conv_defs)
                else:
                    source_uid = "__global__" if source_scope == "global" else uid
                    source_kwargs = {"conversation_id": conv_id} if source_scope == "conversation" and conv_id else {}
                    rs.delete(rtype, rname, source_uid, **source_kwargs)
            flowfile.set_content(json.dumps({"ok": True, "copied_to": target_scope, "from_scope": source_scope}).encode())
        except Exception:
            # If exists, update instead
            try:
                scope_kwargs = {"conversation_id": conv_id} if target_scope == "conversation" and conv_id else {}
                rs.update(rtype, rname, target_uid, data, **scope_kwargs)
                if from_scope and source_scope != target_scope:
                    if source_scope == "conversation" and conv_id and rtype == "task_def":
                        from core.conversation_store import ConversationStore
                        cs = ConversationStore.instance()
                        conv_defs = cs.get_extra(conv_id, "conversation_task_defs") or {}
                        conv_defs.pop(rname, None)
                        cs.set_extra(conv_id, "conversation_task_defs", conv_defs)
                    else:
                        source_uid = "__global__" if source_scope == "global" else uid
                        source_kwargs = {"conversation_id": conv_id} if source_scope == "conversation" and conv_id else {}
                        rs.delete(rtype, rname, source_uid, **source_kwargs)
                flowfile.set_content(json.dumps({"ok": True, "copied_to": target_scope, "from_scope": source_scope, "updated": True}).encode())
            except Exception as e2:
                flowfile.set_content(json.dumps({"error": str(e2)}).encode())
        return [flowfile]

    return _UNHANDLED
