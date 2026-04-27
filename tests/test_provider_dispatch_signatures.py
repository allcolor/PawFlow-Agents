"""Static check: every provider's `_stream_*` method accepts the kwargs
that `LLMClient.complete_stream` actually passes to it.

Without this test, a kwarg drift between the dispatch site
(`core/llm_client.py:complete_stream._do_stream`) and a per-provider
`_stream_*` signature only surfaces at runtime when the provider is
actually invoked — the bug shipped in `a443a68` (codex/gemini
`_stream_*` got a `thinking_callback` kwarg that the signatures didn't
accept) is exactly that class. The contract enforced here is read live
from the source: each branch of the dispatch is parsed, the kwargs it
passes are extracted, and the corresponding `_stream_*` is checked to
accept them.
"""

import ast
import inspect
from pathlib import Path

import core.llm_client  # registers providers
from core.llm_client import LLMClient


def _parse_dispatch_branches() -> dict:
    """Return {provider: {kwarg_name, ...}} as actually called in
    `complete_stream`'s `_do_stream` block.

    Walks the AST of core/llm_client.py and finds each
    `if/elif self.provider == "X": ... self._stream_*(...)` call,
    collecting the keyword argument names passed.
    """
    src = Path(core.llm_client.__file__).read_text(encoding="utf-8")
    tree = ast.parse(src)
    out: dict = {}
    for node in ast.walk(tree):
        if not isinstance(node, ast.If):
            continue
        # Pattern: comparing self.provider to a string literal.
        provider_name = None
        test = node.test
        if (isinstance(test, ast.Compare)
                and len(test.ops) == 1
                and isinstance(test.ops[0], ast.Eq)
                and isinstance(test.left, ast.Attribute)
                and isinstance(test.left.value, ast.Name)
                and test.left.value.id == "self"
                and test.left.attr == "provider"
                and len(test.comparators) == 1
                and isinstance(test.comparators[0], ast.Constant)
                and isinstance(test.comparators[0].value, str)):
            provider_name = test.comparators[0].value
        if not provider_name:
            continue
        # Find any `self._stream_*(...)` call inside this branch's body.
        for sub in ast.walk(node):
            if (isinstance(sub, ast.Call)
                    and isinstance(sub.func, ast.Attribute)
                    and isinstance(sub.func.value, ast.Name)
                    and sub.func.value.id == "self"
                    and sub.func.attr.startswith("_stream_")):
                kwargs = {kw.arg for kw in sub.keywords if kw.arg is not None}
                out[provider_name] = (sub.func.attr, kwargs)
                break
    return out


def _accepts_kwarg(fn, kwarg: str) -> bool:
    sig = inspect.signature(fn)
    if kwarg in sig.parameters:
        return True
    return any(p.kind is inspect.Parameter.VAR_KEYWORD
               for p in sig.parameters.values())


def test_dispatch_kwargs_match_signatures():
    """Every kwarg the dispatch passes must be accepted by the target.

    Reads the dispatch live from llm_client.py's AST and validates each
    provider's `_stream_*` signature accepts the exact set passed at the
    call site. Drift in either direction (dispatch adds a kwarg the
    method doesn't take, or method drops a kwarg the dispatch still
    passes) fails this test — same class as the codex/gemini
    `thinking_callback` runtime crash from a443a68.
    """
    branches = _parse_dispatch_branches()
    assert branches, "failed to parse any provider branch from llm_client.py"
    failures = []
    for provider, (method_name, kwargs) in branches.items():
        if not hasattr(LLMClient, method_name):
            failures.append(
                f"provider '{provider}' → LLMClient.{method_name} missing")
            continue
        fn = getattr(LLMClient, method_name)
        for kw in sorted(kwargs):
            if not _accepts_kwarg(fn, kw):
                failures.append(
                    f"LLMClient.{method_name} (provider '{provider}') is "
                    f"missing kwarg `{kw}` — the dispatch in "
                    f"complete_stream._do_stream passes it. Add it to the "
                    f"signature or remove it from the dispatch.")
    if failures:
        raise AssertionError("\n".join(failures))


def test_provider_mixins_have_no_method_collisions():
    """Each provider's per-CLI helper methods must NOT collide.

    `LLMClient` inherits from `LLMClaudeCodeMixin`, `LLMCodexMixin`, and
    `LLMGeminiMixin` (each with their own session mixin). Two mixins
    defining a method with the same name silently let Python's MRO pick
    one — and the wrong provider's implementation runs. The bug shipped
    in `a443a68` (codex's `_setup_credentials` was shadowed by CC's,
    sending codex calls through CC's auth pool + claude pool) is exactly
    that.

    Convention enforced here: every method on a provider mixin (or its
    session mixin) MUST be prefixed with the CLI name (`_cc_`, `_codex_`,
    `_gemini_`) UNLESS it is one of the OK_TO_COLLIDE exceptions below.
    """
    from core.llm_providers.claude_code import LLMClaudeCodeMixin as CC
    from core.llm_providers.codex import LLMCodexMixin as CX
    from core.llm_providers.gemini import LLMGeminiMixin as GM
    from core.llm_providers.claude_code_session import ClaudeCodeSessionMixin as CCS
    from core.llm_providers.codex_session import CodexSessionMixin as CXS
    from core.llm_providers.gemini_session import GeminiSessionMixin as GMS

    def _own(c):
        return set(c.__dict__.keys()) - {
            "__module__", "__qualname__", "__doc__",
            "__dict__", "__weakref__",
        }

    cc_all = _own(CC) | _own(CCS)
    cx_all = _own(CX) | _own(CXS)
    gm_all = _own(GM) | _own(GMS)

    # Names that may legitimately appear identically on multiple mixins:
    #   - Constants / regex whose value is identical across CLIs.
    #   - `_get_tool_relay_info` is a classmethod returning the SHARED
    #     PawFlow tool relay service — codex/gemini delegate to CC's.
    #   - `_pool_counter` / `_pool_lock` are accessed via
    #     `<Mixin>._pool_counter` (class-name prefix), so per-class
    #     state is preserved despite the name collision.
    OK_TO_COLLIDE = {
        "_DISALLOWED_BUILTIN_TOOLS",
        "_LEGACY_IMAGE_RE",
        "_OAUTH_REFRESH_MIN_TTL_SEC",
        "_get_tool_relay_info",
        "_pool_counter",
        "_pool_lock",
    }

    failures = []
    for label, a, b in (
        ("CC ∩ codex", cc_all, cx_all),
        ("CC ∩ gemini", cc_all, gm_all),
        ("codex ∩ gemini", cx_all, gm_all),
    ):
        bad = (a & b) - OK_TO_COLLIDE
        if bad:
            failures.append(
                f"{label}: {sorted(bad)} — these names collide on "
                f"LLMClient and Python's MRO will silently pick one. "
                f"Rename with the CLI prefix (`_cc_*` / `_codex_*` / "
                f"`_gemini_*`) or add to OK_TO_COLLIDE if intentional.")
    if failures:
        raise AssertionError("\n\n".join(failures))
