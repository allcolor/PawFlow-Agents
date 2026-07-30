'''Release the warm CLI container bound to one (conversation, agent) pair.

Every CLI provider keeps its process and Docker container alive between turns,
keyed on (user, conversation, agent, service, pool slot). That is what makes a
conversation cheap to continue -- and what makes a *finished* run expensive:
the container is acquired 1:1 from a capped pool (PAWFLOW_CODEX_POOL_MAX, 50 by
default) and only an eviction returns it. Left alone, a sub-agent that ran for
four seconds holds its slot until the idle sweeper reaps it one full idle TTL
later -- 30 minutes with the default service timeout.

A one-shot run -- a flash agent, a delegate, a task, a plan step -- has no next
turn to keep anything warm for. Its container goes back to the pool the moment
it finishes. Only an explicitly persistent delegate, which the caller intends to
speak to again, keeps its session.

Reuse is by identity, never by availability: nothing looks for an idle container
to borrow, so a slot held by a finished run is a slot nobody can use.
'''

import logging

logger = logging.getLogger(__name__)


def find_live_cli_session(registry, user_id: str, conversation_id: str,
                          agent_key: str, service_id: str,
                          pool_idx: int = -1,
                          allow_pool_fallback: bool = True):
    """The live session for this (user, conversation, agent, service), or None.

    Exact key first, then -- if the caller allows it -- the compatible lookup
    that ignores the pool index: the stored index can be missing or stale
    while the process is very much alive. Only a process that answers counts
    as a session.

    ``allow_pool_fallback`` is the CALLER's policy, never this helper's,
    because the providers do not agree on it: codex takes any compatible
    session, gemini only when the stored slot is missing -- a concrete index
    that misses means the slot changed on purpose (rotation, slot removal),
    and the old-slot container would resurrect the previous account's session.
    Deciding here would align one caller by breaking the other.

    This is THE question the context phase and every CLI provider must answer
    the same way, and they did not: the providers asked their live registry,
    while the context phase asked whether a session id was persisted. Once
    anything cleared that id -- a stale-thread reset, a compaction
    invalidation, a pool index we could not match -- the context phase declared
    a cold start and loaded and compacted the entire transcript, and the
    provider then found the live process, resumed, and threw all of it away.
    Every turn, because nothing on the reuse path wrote the id back.

    Answering the same way also means asking with the same inputs: both
    providers read the stored pool index ONLY when they still hold a session
    id, so a caller that read it unconditionally would pass a concrete index
    where the provider passes -1, and the two would part company again on the
    fallback.

    Never raises: a registry that cannot answer means no session, which is the
    safe verdict -- it costs a context load, not a lost conversation.
    """
    session = None
    try:
        session = registry.get(
            (user_id, conversation_id, agent_key, service_id, pool_idx))
        if session is None and allow_pool_fallback:
            compatible = registry.get_compatible(
                user_id, conversation_id, agent_key, service_id)
            session = compatible[1] if compatible else None
    except Exception:
        logger.debug("live CLI session lookup failed for %s/%s",
                     str(conversation_id)[:8], agent_key, exc_info=True)
        return None
    try:
        return session if (session is not None
                           and session.is_process_alive()) else None
    except Exception:
        logger.debug("liveness check failed for %s/%s",
                     str(conversation_id)[:8], agent_key, exc_info=True)
        return None


def _live_registries():
    '''Yield (label, registry) for every CLI provider that holds containers.

    Imported one at a time on purpose: a provider whose module fails to load
    must not stop the others from giving their containers back.
    '''
    out = []
    try:
        from core.cc_live_registry import LiveSessionRegistry
        out.append(('claude-code', LiveSessionRegistry.instance()))
    except Exception:
        logger.debug('cc live registry unavailable', exc_info=True)
    try:
        from core.claude_code_interactive_pool import InteractiveClaudeCodePool
        out.append(('claude-code-interactive',
                    InteractiveClaudeCodePool.instance()))
    except Exception:
        logger.debug('cci pool unavailable', exc_info=True)
    try:
        from core.codex_live_registry import CodexLiveRegistry
        out.append(('codex', CodexLiveRegistry.instance()))
    except Exception:
        logger.debug('codex live registry unavailable', exc_info=True)
    try:
        from core.gemini_live_registry import GeminiLiveRegistry
        out.append(('gemini', GeminiLiveRegistry.instance()))
    except Exception:
        logger.debug('gemini live registry unavailable', exc_info=True)
    try:
        from core.antigravity_observer_pool import AntigravityObserverPool
        out.append(('antigravity', AntigravityObserverPool.instance()))
    except Exception:
        logger.debug('antigravity pool unavailable', exc_info=True)
    return out


def release_cli_live_sessions(conversation_id: str, agent_name: str,
                              reason: str) -> int:
    '''Kill every warm CLI session for (conversation_id, agent_name).

    Returns how many were released. Never raises: this runs in a cleanup path,
    and a registry that cannot answer must not fail the run that is ending.
    '''
    conversation_id = str(conversation_id or '').strip()
    agent_name = str(agent_name or '').strip()
    if not conversation_id or not agent_name:
        return 0
    released = 0
    for label, registry in _live_registries():
        try:
            count = int(registry.kill_and_evict_by_conv_agent(
                conversation_id, agent_name, reason=reason) or 0)
        except Exception:
            logger.debug('%s live-session release failed for %s/%s',
                         label, conversation_id[:8], agent_name, exc_info=True)
            continue
        if count:
            released += count
            logger.info('Released %d live %s container(s) for %s/%s (%s)',
                        count, label, conversation_id[:8], agent_name, reason)
    return released
