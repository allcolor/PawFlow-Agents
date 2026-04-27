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
