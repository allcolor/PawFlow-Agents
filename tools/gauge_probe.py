"""Compare the gauge counter and the compaction counter on a real agent context.

Usage:  python3 tools/gauge_probe.py <conversation_dir|context.jsonl> [agent]

<conversation_dir> holds <agent>/context.jsonl (flat or SegmentedJsonl), e.g.
  <pawflow>/data/runtime/conversations/<user>/<conversation_id>
A bare .jsonl also works, which is how you point it at a version recovered
from the conversation's git history with `git show`.

Answers one question: does the gauge disagree with the compaction threshold by
more than the cold-CLI bootstrap boundary can account for? `UNEXPLAINED` must
be 0. Anything else means the gauge is losing messages, and the listed
structural markers say whether the boundary moved instead. A plain grep for
the marker string cannot be used here: it also matches messages that merely
quote it, such as tool output from reading this very source.

Read-only, no network. Works without tiktoken: it falls back to the same
approximation core/token_counter.py uses when its BPE file is unavailable.
Both counters share whichever tokenizer is present, so the RATIO between them
is valid either way -- and the ratio is the whole question.
"""
import importlib.util
import json
import sys
from collections import Counter
from pathlib import Path


def _repo_root():
    """Walk up to whichever ancestor actually holds the module we load.

    The probe is run from wherever the deployment happens to put it -- a relay
    workspace, a container mount, a checkout -- so a fixed parent count is the
    one thing guaranteed to break.
    """
    for candidate in [Path(__file__).resolve().parent, *Path(__file__).resolve().parents]:
        if (candidate / 'tasks' / 'ai' / 'context_usage_cache.py').is_file():
            return candidate
    sys.exit('cannot locate tasks/ai/context_usage_cache.py above %s'
             % Path(__file__).resolve())


HERE = _repo_root()

# Load the real boundary/strip logic straight from its file, so the probe
# measures the shipped behaviour and not a paraphrase of it. Loading by path
# avoids importing the tasks package (and everything it drags in).
_spec = importlib.util.spec_from_file_location(
    '_probe_cuc', HERE / 'tasks' / 'ai' / 'context_usage_cache.py')
cuc = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(cuc)

try:
    import tiktoken
    _enc = tiktoken.get_encoding('cl100k_base')
    TOKENIZER = 'tiktoken cl100k_base (exact)'
except Exception:
    _enc = None
    TOKENIZER = 'len(utf-8)/4 fallback (ratios still valid)'


def _tokens(text):
    if not text:
        return 0
    if _enc is not None:
        return len(_enc.encode(text, disallowed_special=()))
    return max(1, (len(text.encode('utf-8')) + 3) // 4)


def count_messages_tokens(messages):
    """Mirror of core.token_counter.count_messages_tokens: content + 4 overhead."""
    total = 0
    for msg in messages:
        content = msg.get('content', '') if isinstance(msg, dict) else ''
        if isinstance(content, str):
            total += _tokens(content) + 4
        elif isinstance(content, list):
            for block in content:
                if isinstance(block, dict) and block.get('text'):
                    total += _tokens(block['text'])
            total += 4
        else:
            total += 4
    return total


conv_dir = Path(sys.argv[1])
agent = sys.argv[2] if len(sys.argv) > 2 else 'claude'
agent_dir = conv_dir / agent


def _read_jsonl(path):
    rows = []
    with path.open(encoding='utf-8') as fh:
        for line in fh:
            line = line.strip()
            if line:
                try:
                    rows.append(json.loads(line))
                except ValueError:
                    pass
    return rows


# SegmentedJsonl turns the logical context.jsonl into a directory of numbered
# segments; zero-padded names sort chronologically. Support both layouts.
flat = agent_dir / 'context.jsonl'
seg_dir = agent_dir / 'context'
if conv_dir.is_file():
    # A bare .jsonl, e.g. one extracted from git history with `git show`.
    sources = [conv_dir]
    seg_dir = agent_dir  # no index/pending to cross-check
elif flat.is_file():
    sources = [flat]
elif seg_dir.is_dir():
    sources = sorted(seg_dir.glob('*.jsonl'))
else:
    sys.exit('no context found under %s' % agent_dir)

msgs = []
for src in sources:
    msgs.extend(_read_jsonl(src))

print('tokenizer    :', TOKENIZER)
print('context from :', ', '.join(p.name for p in sources))
print('messages     :', len(msgs))

# Cross-check against the segment index: if these disagree, the naive
# concatenation is missing rows and every number below is suspect.
index_path = seg_dir / 'index.json'
if index_path.is_file():
    try:
        idx = json.loads(index_path.read_text(encoding='utf-8'))
        rows = (idx.get('total_rows') if isinstance(idx, dict) else None)
        print('index.json total_rows:', rows,
              '(MISMATCH)' if rows not in (None, len(msgs)) else '')
    except (ValueError, OSError):
        pass

pending = agent_dir / 'pending.jsonl'
if pending.is_file():
    print('pending.jsonl rows   :', len(_read_jsonl(pending)),
          '(not yet folded into the context)')

boundary = cuc._cli_bootstrap_boundary_index(msgs)
print('bootstrap boundary index:', boundary,
      '(-1 = no marker at all, nothing gets zeroed)')

# Every STRUCTURAL marker -- not a text match. A plain grep also hits messages
# that merely quote the string (tool output from reading the source, say), so
# it cannot be trusted here. Each real marker reset the gauge to near zero
# while the PawFlow context kept growing; only the last one is in force.
all_marks = [i for i, m in enumerate(msgs) if cuc._is_cli_bootstrap_boundary(m)]
print('all structural markers   :', all_marks or 'none',
      '<- %d reset(s)' % len(all_marks))

# The gauge: content only, everything before the boundary zeroed.
gauge = count_messages_tokens(cuc._strip_for_count(msgs))
# The compaction threshold: content only, nothing zeroed.
all_content = [{'content': cuc._content_text(cuc._message_content(m))}
               for m in msgs]
compact = count_messages_tokens(all_content)
# What the boundary alone is entitled to hide. The zeroed messages still cost
# their 4-token role/separator overhead in the gauge, so only their CONTENT is
# hidden -- subtract that overhead or the arithmetic lands 4 per message short.
hidden = max(0, boundary)
pre = count_messages_tokens(all_content[:hidden]) - 4 * hidden

print()
print('gauge   (post-boundary content) :', gauge)
print('compact (all content)           :', compact)
print('difference                      :', compact - gauge)
print('explained by the boundary       :', pre)
print('UNEXPLAINED                     :', (compact - gauge) - pre)
print('  ^ must be 0. Anything else is the bug.')

# How much history the boundary is hiding, in messages -- if this is far more
# than one bootstrap's worth, the marker is misplaced, not the counting.
print()
print('messages hidden by the boundary :', max(0, boundary))
print('messages counted by the gauge   :', len(msgs) - max(0, boundary))

# Tokens carried by tool_calls, which NEITHER counter looks at.
tc_chars = 0
for m in msgs:
    for tc in cuc._message_tool_calls(m):
        name = tc.get('name') if isinstance(tc, dict) else getattr(tc, 'name', '')
        args = tc.get('arguments') if isinstance(tc, dict) else getattr(tc, 'arguments', None)
        try:
            rendered = json.dumps(args, ensure_ascii=False)
        except (TypeError, ValueError):
            rendered = str(args)
        tc_chars += len(str(name)) + len(rendered)
print()
print('tool_call chars invisible to BOTH counters:', tc_chars,
      '(~%d tokens)' % (tc_chars // 3))

by_role = Counter()
for m in msgs:
    by_role[cuc._message_role(m)] += len(cuc._content_text(cuc._message_content(m)))
print('content chars by role:', dict(by_role))

# The gauge value the UI actually shows, as persisted.
for name in ('extras.json', 'metadata.json', 'conversation.json'):
    path = conv_dir / name
    if not path.exists():
        continue
    try:
        blob = json.loads(path.read_text(encoding='utf-8'))
    except (ValueError, OSError):
        continue
    usage = blob.get('context_usage') if isinstance(blob, dict) else None
    if isinstance(usage, dict) and usage.get(agent):
        print()
        print('persisted gauge entry (%s):' % name)
        print(json.dumps(usage[agent], indent=2)[:1200])
        break
