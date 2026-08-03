"""render_template — pure ${path.to.var} placeholder substitution.

No filesystem or host tool/service access: this entrypoint only transforms
text it receives in the invocation payload and returns text. The caller
(agent) decides where the rendered draft goes (chat message, a file it
writes itself with its own tool grants, etc).

Missing variable => an explicit "[path missing]" marker in the output,
never a guessed value — a rendered document must never assert a fact it
does not actually have.
"""

import re

from pawflow import pfp

_PLACEHOLDER_RE = re.compile(r"\$\{([a-zA-Z0-9_.]+)\}")


def _resolve(path: str, variables: dict):
    """Resolve a dotted path like 'dossier.client' against nested dict.

    Returns (found: bool, value). A path segment missing at any level, or
    hitting a non-dict where a further segment is expected, counts as not
    found rather than raising — the caller renders the explicit marker.
    """
    current = variables
    for part in path.split("."):
        if not isinstance(current, dict) or part not in current:
            return False, None
        current = current[part]
    return True, current


def _render(template: str, variables: dict) -> str:
    def _sub(match: "re.Match") -> str:
        path = match.group(1)
        found, value = _resolve(path, variables)
        if not found or value is None:
            return f"[{path} manquant]"
        return str(value)

    return _PLACEHOLDER_RE.sub(_sub, template)


def main() -> None:
    args = pfp.payload.get("arguments", {}) if isinstance(pfp.payload, dict) else {}
    template = args.get("template")
    if not isinstance(template, str) or not template:
        pfp.error("'template' is required and must be a non-empty string")
        raise SystemExit(1)

    variables = args.get("variables") or {}
    if not isinstance(variables, dict):
        pfp.error("'variables' must be a JSON object")
        raise SystemExit(1)

    rendered = _render(template, variables)
    missing = _PLACEHOLDER_RE.findall(template)
    missing_paths = sorted({p for p in missing if not _resolve(p, variables)[0]})

    pfp.result({
        "rendered": rendered,
        "missing_variables": missing_paths,
    })


if __name__ == "__main__":
    main()
