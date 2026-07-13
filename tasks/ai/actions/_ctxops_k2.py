"""AgentLoopTask actions — context ops"""

import json
import logging

from tasks.ai.actions._ctxops_base import (
    _UNHANDLED,
)

logger = logging.getLogger(__name__)


def _handle_ctxops_k2(self, action, body, store, user_id, flowfile, _helpers):
    """context_ops cluster _ctxops_k2. Returns result or _UNHANDLED."""
    (_ctx_agent_name, _ctx_load, _ctx_save, _ctx_cached_usage,
     _ctx_visible_contexts, _ctx_llm_service_config, _ctx_real_context_size, _ctx_max_tokens) = _helpers
    if action == "compact":
        conv_id = body.get("conversation_id", "")
        _ctx_agent = body.get("agent_name", "")
        if not conv_id:
            flowfile.set_content(json.dumps({"error": "Missing conversation_id"}).encode())
            flowfile.set_attribute("http.response.status", "400")
            return [flowfile]
        # Membership guard — `/compact ghost` used to silently create a
        # per-agent dir (data/.../ghost/) and write a compacted ctx for
        # an agent that was never added to this conv, producing orphan
        # state. require_agent_member auto-registers from a global
        # definition when possible (matches the user's mental model
        # "I have qwen configured globally"); otherwise fails loud.
        from core.conv_agent_config import require_agent_member
        _cp_err = require_agent_member(conv_id, _ctx_agent,
                                         user_id=user_id)
        if _cp_err:
            flowfile.set_content(json.dumps({"error": _cp_err}).encode())
            flowfile.set_attribute("http.response.status", "400")
            return [flowfile]
        if store.message_count(conv_id) < 4:
            flowfile.set_content(json.dumps({"error": "Not enough messages to compact"}).encode())
            return [flowfile]
        # Shared-context compaction is the same deterministic hot path as
        # provider-triggered compact. A summarizer client is only required for
        # isolated independent contexts; _compact_context_from_store enforces
        # that case. Do not make /compact a second procedure with a stricter
        # service prerequisite than the trigger path.
        _compact_client, _, _compact_svc_id = self._get_summarizer_client(
            user_id, conversation_id=conv_id)
        _compact_budget_config = _ctx_llm_service_config(conv_id, _ctx_agent)
        _compact_max = _ctx_max_tokens(conv_id, _ctx_agent)
        _compact_conv = conv_id
        _compact_agent_name = _ctx_agent_name(_ctx_agent)

        _compact_instructions = body.get("instructions", "")

        def _do_compact():
            stats = {}
            compacted = self._compact_context_from_store(
                store,
                conversation_id=_compact_conv,
                agent_name=_compact_agent_name,
                user_id=user_id,
                max_tokens=_compact_max,
                compact_client=_compact_client,
                compact_instructions=_compact_instructions,
                force=True,
                budget_config=_compact_budget_config,
                stats=stats,
            )
            before = int(stats.get("before", 0) or 0)
            estimated = int(stats.get("tokens_before", 0) or 0)
            after_tokens = self._estimate_tokens(compacted)
            # CC session invalidation (extra clear + jsonl+companion purge on disk)
            # is handled by `_run_bg_context_op` via `_clear_claude_session` after
            # _do_compact returns. Do NOT clear the extra here — that would
            # make the subsequent purge a no-op (helper bails early on empty sid).
            return {"before": before, "after": len(compacted),
                    "tokens_before": estimated, "tokens_after": after_tokens,
                    "agent": _compact_agent_name or "shared",
                    "focus": _compact_instructions or None}

        # Scope the compact lock to the target agent: /compact claude
        # must NOT block other agents on the same conv. Only a
        # whole-conv /compact (agent_name=="" or "ALL"/"shared") uses
        # the sentinel that blocks everyone.
        _compact_lock_agent = (
            "" if _ctx_agent in ("", "ALL", "shared") else _ctx_agent)
        return self._run_bg_context_op(
            conv_id, "compact", _do_compact, flowfile,
            agent_name=_compact_lock_agent)

    if action == "rebuild":
        conv_id = body.get("conversation_id", "")
        if not conv_id:
            flowfile.set_content(json.dumps({"error": "Missing conversation_id"}).encode())
            flowfile.set_attribute("http.response.status", "400")
            return [flowfile]
        transcript = store.load(conv_id, user_id=user_id)
        if transcript is None:
            flowfile.set_content(json.dumps({"error": "Conversation not found"}).encode())
            flowfile.set_attribute("http.response.status", "404")
            return [flowfile]
        _compact_client, _, _compact_svc_id = self._get_summarizer_client(user_id, conversation_id=conv_id)
        if not _compact_client:
            flowfile.set_content(json.dumps({
                "error": "No summarizer service configured — rebuild needs compaction.",
            }).encode())
            return [flowfile]
        from core.conv_agent_config import get_all_agent_configs
        agent_names = sorted((get_all_agent_configs(conv_id) or {}).keys())

        def _do_rebuild():
            from core.bucket_store import BucketStore
            from core.bg_bucket_builder import BgBucketBuilder
            from core.conversation_event_bus import ConversationEventBus
            from tasks.ai.context_usage import (
                compute_context_usage, persist_context_usage,
                usage_event_payload)

            shared_candidates = store.filter_for_shared(transcript)
            shared_msgs = [store._transform_for_shared(m) for m in shared_candidates]
            store.save_agent_context(conv_id, "", shared_msgs)

            bucket_store = BucketStore.get(store._conv_dir(conv_id))
            buckets_before = bucket_store.object_count
            bucket_store.wipe()
            bucket_result = BgBucketBuilder.instance().build_now_sync(
                conv_id, user_id, allow_partial=True)

            for existing_name in store.list_agent_contexts(conv_id):
                if existing_name != "*" and existing_name not in agent_names:
                    store.delete_agent_context(conv_id, existing_name)

            compacted_agents = {}
            total_before = 0
            total_after = 0
            for name in agent_names:
                msgs = self._load_compact_source_messages(
                    store, conv_id, name, user_id=user_id)
                if len(msgs) < 4:
                    serialized = self._serialize_messages(msgs)
                    store.save_agent_context(conv_id, name, serialized)
                    compacted_agents[name] = {
                        "before": len(msgs), "after": len(msgs),
                        "skipped": "not_enough_messages",
                    }
                else:
                    compacted = self._compact(
                        msgs, _compact_client, _ctx_max_tokens(conv_id, name),
                        conversation_id=conv_id,
                        agent_name=name,
                        compact_instructions="",
                        force=True,
                        user_id=user_id,
                        budget_config=_ctx_llm_service_config(conv_id, name),
                    )
                    serialized = self._serialize_messages(compacted)
                    store.save_agent_context(conv_id, name, serialized)
                    compacted_agents[name] = {
                        "before": len(msgs), "after": len(serialized),
                    }
                total_before += int(compacted_agents[name]["before"])
                total_after += int(compacted_agents[name]["after"])
                usage = compute_context_usage(
                    conv_id, name, user_id=user_id, store=store,
                    owner=self, source="rebuild_compact")
                persist_context_usage(conv_id, name, usage, store=store)
                ConversationEventBus.instance().publish_event(
                    conv_id, "message_meta", usage_event_payload(usage))

            store.invalidate_claude_sessions(conv_id)
            return {
                "agent": "ALL",
                "shared_messages": len(shared_msgs),
                "buckets_before": buckets_before,
                "buckets_built": bucket_result.get("buckets_built", 0),
                "rollups_fired": bucket_result.get("rollups_fired", 0),
                "agents": compacted_agents,
                "before": total_before,
                "after": total_after,
                "summarizer_service": _compact_svc_id,
            }

        return self._run_bg_context_op(
            conv_id, "rebuild", _do_rebuild, flowfile, agent_name="")

    if action == "rebuild_full":
        conv_id = body.get("conversation_id", "")
        requested_agent = body.get("agent_name", "").strip()
        if not conv_id:
            flowfile.set_content(json.dumps({"error": "Missing conversation_id"}).encode())
            flowfile.set_attribute("http.response.status", "400")
            return [flowfile]
        transcript = store.load(conv_id, user_id=user_id)
        if transcript is None:
            flowfile.set_content(json.dumps({"error": "Conversation not found"}).encode())
            flowfile.set_attribute("http.response.status", "404")
            return [flowfile]

        from core.conv_agent_config import get_all_agent_configs, require_agent_member
        if requested_agent and requested_agent not in ("ALL", "shared"):
            membership_error = require_agent_member(
                conv_id, requested_agent, user_id=user_id)
            if membership_error:
                flowfile.set_content(json.dumps({"error": membership_error}).encode())
                flowfile.set_attribute("http.response.status", "400")
                return [flowfile]
            agent_names = [requested_agent]
        else:
            agent_names = sorted((get_all_agent_configs(conv_id) or {}).keys())

        def _do_rebuild_full():
            rebuilt = {}
            if requested_agent in ("", "ALL", "shared"):
                shared_candidates = store.filter_for_shared(transcript)
                shared_messages = [
                    store._transform_for_shared(message)
                    for message in shared_candidates
                ]
                store.save_agent_context(conv_id, "", shared_messages)
                rebuilt["shared"] = len(shared_messages)

            for name in agent_names:
                messages = self._load_compact_source_messages(
                    store, conv_id, name, user_id=user_id)
                serialized = self._serialize_messages(messages)
                store.save_agent_context(conv_id, name, serialized)
                rebuilt[name] = len(serialized)

            store.invalidate_claude_sessions(conv_id)
            return {"rebuilt_full": True, "contexts": rebuilt}

        lock_agent = (
            requested_agent
            if requested_agent not in ("", "ALL", "shared") else "")
        return self._run_bg_context_op(
            conv_id, "rebuild_full", _do_rebuild_full, flowfile,
            agent_name=lock_agent)

    return _UNHANDLED
