#!/usr/bin/env python3
"""One-shot migration: strip scope prefixes from ${...} expressions.

Before: ${global.api_key}, ${user.fast_model}, ${secrets.openai_key}, ${env.HOME}
After:  ${api_key},         ${fast_model},      ${openai_key},         ${HOME:!important(env)}

All prefixes are legacy — the expression engine uses unified cascade.
Use ${key:!important(scope)} to force a specific scope.

Scans:
- data/config/*.json (agents, skills, mcp_servers, etc.)
- data/config/users/**/*.json
- data/conversations/**/*.json (conv extras)

Dry-run by default. Use --apply to write changes.
"""

import json
import re
import sys
from pathlib import Path

# Prefixes to strip (order matters — longest first)
STRIP_PREFIXES = [
    'flow.parameters.',
    'secrets.global.',
    'secrets.conv.',
    'secrets.user.',
    'secrets.',
    'flow.',
    'conv.',
    'user.',
    'global.',
    'var.',
    'env.',
]

# Regex: match ${prefix.name} for all legacy prefixes
EXPR_RE = re.compile(r'\$\{(' + '|'.join(re.escape(p) for p in STRIP_PREFIXES) + r')([^}]+)\}')


def migrate_string(s: str) -> str:
    """Strip legacy prefixes from expressions in a string."""
    def _replace(m):
        prefix = m.group(1)
        rest = m.group(2)
        if prefix == 'env.':
            return '${' + rest + ':!important(env)}'
        return '${' + rest + '}'
    return EXPR_RE.sub(_replace, s)


def migrate_value(value):
    """Recursively migrate expressions in any JSON value."""
    if isinstance(value, str):
        return migrate_string(value)
    if isinstance(value, dict):
        return {k: migrate_value(v) for k, v in value.items()}
    if isinstance(value, list):
        return [migrate_value(v) for v in value]
    return value


def process_file(path: Path, dry_run: bool = True) -> int:
    """Process a single JSON file. Returns number of changes."""
    try:
        raw = path.read_text(encoding='utf-8')
    except Exception as e:
        print(f"  SKIP {path}: {e}")
        return 0

    # Quick check: any expressions at all?
    if '${' not in raw:
        return 0

    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return 0

    migrated = migrate_value(data)
    new_raw = json.dumps(migrated, indent=2, ensure_ascii=False)

    if new_raw == json.dumps(data, indent=2, ensure_ascii=False):
        return 0

    # Count changes
    old_exprs = EXPR_RE.findall(raw)
    count = len(old_exprs)

    if dry_run:
        print(f"  WOULD migrate {path}: {count} expression(s)")
        for prefix, name in old_exprs[:5]:
            print(f"    ${{{prefix}{name}}} -> ${{{name}}}")
        if count > 5:
            print(f"    ... and {count - 5} more")
    else:
        path.write_text(new_raw + '\n', encoding='utf-8')
        print(f"  MIGRATED {path}: {count} expression(s)")

    return count


def main():
    dry_run = '--apply' not in sys.argv
    if dry_run:
        print("DRY RUN — use --apply to write changes\n")
    else:
        print("APPLYING CHANGES\n")

    total = 0
    files = 0

    # All JSON files that may contain expressions
    for pattern in ['data/config/*.json', 'data/config/users/**/*.json',
                    'flows/*.json', 'flows/**/*.json',
                    'data/**/*.json']:
        for path in Path('.').glob(pattern):
            n = process_file(path, dry_run)
            if n:
                total += n
                files += 1

    print(f"\n{'Would migrate' if dry_run else 'Migrated'}: "
          f"{total} expression(s) in {files} file(s)")
    if dry_run and total:
        print("Run with --apply to execute migration.")


if __name__ == '__main__':
    main()
