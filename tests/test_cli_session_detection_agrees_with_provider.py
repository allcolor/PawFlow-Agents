"""The context phase and the CLI providers must answer one question the same way.

Observed in production on beta.57:

    [context:f9be0300] cold CLI session — gauge reset for assistant
    [context:f9be0300] loaded diverged context: 241 messages
    [compact] Removed 70 orphan tool result(s)
    [codex-app-live] restored live key ... pool_idx=0 thread=019fb2dc-e69
    [codex-app-live] REUSE ... session=019fb2dc-e69 reuse=2
    [codex-app] gauge: prompt_tokens=51 mode=resume (msgs=171, input=148 chars)

The context phase announced a cold start and paid for loading and compacting
the whole transcript; the provider then found the very same process alive,
resumed it, and sent 148 characters. Nothing was actually dead -- the two were
asking different questions. The provider asks its live registry; the context
phase asked whether a session id was still persisted.

Once anything clears that id -- a stale-thread reset, a compaction
invalidation, a pool index that no longer matches -- the disagreement is
permanent, because the codex reuse path never wrote the id back.
"""


from core.cli_live_sessions import find_live_cli_session


class _Session:
    def __init__(self, alive=True, session_id="thread-1"):
        self._alive = alive
        self.session_id = session_id

    def is_process_alive(self):
        return self._alive


class _Registry:
    """Keyed exactly like the real ones: (user, conv, agent, service, pool)."""

    def __init__(self, exact=None, compatible=None):
        self._exact = exact or {}
        self._compatible = compatible
        self.exact_keys_asked = []

    def get(self, key):
        self.exact_keys_asked.append(key)
        return self._exact.get(key)

    def get_compatible(self, user_id, conv_id, agent, service_id):
        return self._compatible


KEY = ("u", "conv", "agent", "svc", 0)


def test_an_exact_key_hit_is_a_session():
    reg = _Registry(exact={KEY: _Session()})
    assert find_live_cli_session(reg, "u", "conv", "agent", "svc", 0) is not None


def test_a_missing_pool_index_still_finds_the_live_process():
    """This is the production case: the stored pool index was gone, so the
    exact lookup used -1 and missed, and only get_compatible found it."""
    session = _Session()
    reg = _Registry(exact={KEY: session}, compatible=(KEY, session))
    found = find_live_cli_session(reg, "u", "conv", "agent", "svc", -1)
    assert found is session
    assert reg.exact_keys_asked == [("u", "conv", "agent", "svc", -1)]


def test_a_dead_process_is_not_a_session():
    reg = _Registry(exact={KEY: _Session(alive=False)})
    assert find_live_cli_session(reg, "u", "conv", "agent", "svc", 0) is None


def test_a_dead_process_found_via_the_compatible_lookup_is_not_a_session():
    dead = _Session(alive=False)
    reg = _Registry(compatible=(KEY, dead))
    assert find_live_cli_session(reg, "u", "conv", "agent", "svc", -1) is None


def test_nothing_registered_is_no_session():
    assert find_live_cli_session(_Registry(), "u", "conv", "agent", "svc") is None


def test_a_registry_that_raises_means_no_session_rather_than_a_lost_turn():
    class _Broken:
        def get(self, key):
            raise RuntimeError("registry down")

        def get_compatible(self, *a):
            raise RuntimeError("registry down")

    assert find_live_cli_session(_Broken(), "u", "conv", "agent", "svc") is None


def test_a_liveness_probe_that_raises_means_no_session():
    class _Rude(_Session):
        def is_process_alive(self):
            raise OSError("process gone")

    reg = _Registry(exact={KEY: _Rude()})
    assert find_live_cli_session(reg, "u", "conv", "agent", "svc", 0) is None


# ── The context phase asks the live registry, not the store ────────────────

def _p1_source():
    from pathlib import Path
    return Path("tasks/ai/_agentctx_p1.py").read_text(encoding="utf-8")


def _branch(src, marker, end=""):
    """The slice starting at ``marker``.

    ``end`` is searched for AFTER the marker; without one the slice runs to the
    end of the file, which is enough for the last branch.
    """
    start = src.index(marker)
    return src[start:src.index(end, start + len(marker))] if end else src[start:]


def test_codex_detection_is_not_gated_on_the_persisted_thread_id():
    src = _p1_source()
    block = _branch(src, "elif st._is_codex_app_server:")
    # The stored id may still be read (it seeds the pool index and the log),
    # but it must not be what decides whether a session exists.
    assert "st._cli_has_session = bool(st._session_val)" not in block
    assert "find_live_cli_session(" in block
    assert "st._cli_has_session = st._session_valid" in block


def test_gemini_detection_is_not_gated_on_the_persisted_session_id():
    src = _p1_source()
    block = _branch(src, "elif st._is_gemini_acp:", "elif st._is_codex_app_server:")
    assert 'st._cli_has_session = bool(st._session_val) and st._session_ver == "2"' not in block
    assert "find_live_cli_session(" in block
    assert "st._cli_has_session = st._live is not None" in block


def test_a_stored_id_with_no_live_process_is_still_cleared():
    """The cleanup that made the stored state truthful must survive the fix."""
    src = _p1_source()
    block = _branch(src, "elif st._is_codex_app_server:")
    assert "if not st._session_valid and st._session_val:" in block
    assert "set_extra(st.conversation_id, st._session_key, \"\")" in block


# ── The provider stops letting the store drift ─────────────────────────────

def test_codex_writes_back_a_thread_id_recovered_from_a_live_session():
    """Without this the store stays empty forever while the session lives, and
    every later turn repeats the same wasted cold load."""
    from pathlib import Path
    src = Path("core/llm_providers/_codex_app_stream.py").read_text(encoding="utf-8")
    assert "_stored_thread_id = thread_id" in src
    assert "thread_id != _stored_thread_id" in src
    write = src.index("thread_id != _stored_thread_id")
    # Anchored on the cold branch's first real action, not on two adjacent
    # lines: the branch legitimately gained a guard above it.
    cold = src.index("self._codex_setup_credentials(")
    assert write < cold, "the write-back must happen on the reuse path"


def test_gemini_already_persists_its_session_every_completed_turn():
    """Gemini reconciles at end of turn, which is why only its detection side
    needed fixing. Pin it so the guarantee is not quietly removed."""
    from pathlib import Path
    src = Path("core/llm_providers/_gemini_stream.py").read_text(encoding="utf-8")
    assert "store.set_extra(conv_id, session_key, session_id)" in src
    assert 'store.set_extra(conv_id, session_version_key, "2")' in src


def test_both_cli_branches_go_through_the_one_helper():
    """One question, one implementation — the divergence started as two."""
    src = _p1_source()
    assert src.count("find_live_cli_session(") == 2   # the two call sites
    assert src.count("import find_live_cli_session") == 2


# ── Same question means same policy AND same inputs ────────────────────────
#
# One helper is not agreement. The providers do not answer alike: codex takes
# any compatible session, gemini refuses one when the stored slot is concrete,
# because a slot that changed on purpose (rotation, removal) means the old
# container holds the previous account's session. A rule baked into the helper
# aligns one caller by breaking the other, so the policy belongs to the caller.
#
# And both providers read the stored pool index ONLY while they still hold a
# session id. A caller that reads it unconditionally passes a concrete slot
# where the provider passes -1, which flips the fallback and parts them again
# -- in the opposite direction from the original bug.

def test_a_concrete_slot_that_misses_does_not_borrow_another_ones_container():
    """Gemini's rule. Without it we resurrect the previous account's session."""
    reg = _Registry(exact={}, compatible=(("u", "conv", "agent", "svc", 3),
                                          _Session()))
    assert find_live_cli_session(reg, "u", "conv", "agent", "svc", 0,
                                 allow_pool_fallback=False) is None
    # ... and the exact lookup still happened, with the slot it was given.
    assert reg.exact_keys_asked == [("u", "conv", "agent", "svc", 0)]


def test_the_fallback_stays_available_to_the_caller_that_wants_it():
    """Codex's rule, and the default: any compatible live session will do."""
    reg = _Registry(exact={}, compatible=(("u", "conv", "agent", "svc", 3),
                                          _Session()))
    assert find_live_cli_session(reg, "u", "conv", "agent", "svc", 0) is not None
    assert find_live_cli_session(reg, "u", "conv", "agent", "svc", 0,
                                 allow_pool_fallback=True) is not None


def test_gemini_reads_the_pool_index_only_while_it_holds_a_session():
    src = _p1_source()
    block = _branch(src, "elif st._is_gemini_acp:", "elif st._is_codex_app_server:")
    assert 'if st._session_val and st._session_ver == "2":' in block, (
        "a legacy-version id is no id at all -- the provider clears one "
        "before it looks at the pool index")
    assert "allow_pool_fallback=st._pool_idx < 0" in block


def test_codex_reads_the_pool_index_only_while_it_holds_a_thread():
    src = _p1_source()
    block = _branch(src, "elif st._is_codex_app_server:")
    assert "if st._session_val:" in block
    assert "allow_pool_fallback=True" in block


def test_the_providers_own_rules_are_what_the_context_phase_mirrors():
    """Pin both provider rules: if one changes, the mirror above is stale and
    this fails instead of drifting silently back into disagreement."""
    from pathlib import Path
    gem = Path("core/llm_providers/_gemini_stream.py").read_text(encoding="utf-8")
    cdx = Path("core/llm_providers/_codex_app_stream.py").read_text(encoding="utf-8")

    # Gemini guards the fallback on an unset slot; codex does not guard it.
    assert "if live_session is None and resume_pool_idx < 0:" in gem
    assert "if live_session is None and resume_pool_idx < 0:" not in cdx
    assert "if live_session is None:\n                    compatible = live_reg.get_compatible(" in cdx

    # Both read the stored slot only while they still hold an id.
    assert "if session_id and conv_id and store is not None:" in gem
    assert "if thread_id and conv_id and store is not None:" in cdx
