"""Edit-family filesystem actions, split from fs_actions.py
(find_replace / edit / batch_edit / apply_patch + diagnostics).
"""
import re
import subprocess  # nosec B404
from pathlib import Path
from typing import Any, Dict, List, Optional

from _fs_paths import _is_host_absolute_path, _resolve_tool_path, _rel


def action_find_replace(root_dir: str, path: str, req: Dict[str, Any]) -> Any:
    pattern = req.get("pattern", "")
    replacement = req.get("replacement", "")
    flags = re.MULTILINE if bool(req.get("multiline", False)) else 0
    if not pattern:
        raise ValueError("Missing 'pattern' parameter")
    compiled = re.compile(pattern, flags)
    p = Path(path)
    text = p.read_text(encoding="utf-8", errors="replace")
    new_text, count = compiled.subn(replacement, text)
    if count > 0:
        p.write_text(new_text, encoding="utf-8")
    return {"replacements": count, "path": _rel(path, root_dir)}


def _line_key(line: str) -> str:
    """Normalize one line for safe whitespace-only edit matching."""
    return line.expandtabs(4).rstrip()


def _window_start(lines: List[str], index: int) -> int:
    return sum(len(line) for line in lines[:index])


def _find_window_matches(text: str, old_string: str, *, fuzzy: bool = False,
                         fuzzy_threshold: float = 0.92) -> List[Dict[str, Any]]:
    """Find exact, whitespace-safe, or explicit fuzzy candidates.

    Exact string matching remains authoritative. Whitespace-safe matching is
    limited to line-ending, trailing-whitespace, and tab/space indentation
    drift, where applying the actual file substring is deterministic. General
    fuzzy matching only runs when requested by the caller.
    """
    matches: List[Dict[str, Any]] = []
    start = 0
    while True:
        pos = text.find(old_string, start)
        if pos < 0:
            break
        matches.append({
            "kind": "exact",
            "start": pos,
            "end": pos + len(old_string),
            "actual": old_string,
            "score": 1.0,
        })
        start = pos + max(1, len(old_string))
    if matches:
        return matches

    old_lines = old_string.splitlines(True)
    text_lines = text.splitlines(True)
    if not old_lines or not text_lines:
        return []
    old_key = [_line_key(line.rstrip("\r\n")) for line in old_lines]
    span = len(old_lines)

    for i in range(0, len(text_lines) - span + 1):
        actual = "".join(text_lines[i:i + span])
        key = [_line_key(line.rstrip("\r\n")) for line in text_lines[i:i + span]]
        if key == old_key:
            start_pos = _window_start(text_lines, i)
            matches.append({
                "kind": "whitespace",
                "start": start_pos,
                "end": start_pos + len(actual),
                "actual": actual,
                "score": 1.0,
            })
    if matches or not fuzzy:
        return matches

    best: List[Dict[str, Any]] = []
    import difflib
    for span_len in {span, max(1, span - 1), span + 1}:
        if span_len > len(text_lines):
            continue
        for i in range(0, len(text_lines) - span_len + 1):
            actual = "".join(text_lines[i:i + span_len])
            score = difflib.SequenceMatcher(None, old_string, actual).ratio()
            if score >= fuzzy_threshold:
                start_pos = _window_start(text_lines, i)
                best.append({
                    "kind": "fuzzy",
                    "start": start_pos,
                    "end": start_pos + len(actual),
                    "actual": actual,
                    "score": score,
                })
    if not best:
        return []
    best.sort(key=lambda item: item["score"], reverse=True)
    top_score = best[0]["score"]
    return [item for item in best if top_score - item["score"] < 0.02]


def _apply_replacements(text: str, matches: List[Dict[str, Any]],
                        new_string: str, replace_all: bool) -> str:
    selected = matches if replace_all else matches[:1]
    out = []
    pos = 0
    for match in sorted(selected, key=lambda item: item["start"]):
        out.append(text[pos:match["start"]])
        out.append(new_string)
        pos = match["end"]
    out.append(text[pos:])
    return "".join(out)


_DIFF_MAX_ROWS = 200


def _build_diff(old_text: str, new_text: str,
                context: int = 3) -> List[Dict[str, Any]]:
    """Line diff of what was ACTUALLY written, +/-context lines around changes.

    Derived from the two file texts, never from old_string/new_string. Building
    it from the operands was wrong in three ways at once, and all three showed
    correct edits as mangled ones:

    - A match may start or end mid-line. Rendering the operands marked the
      WHOLE line removed and printed only the replacement fragment, so the
      untouched remainder of that line appeared nowhere -- it looked deleted
      when it had never moved.
    - Added rows were appended after the entire context window instead of
      sitting at the replacement point, which read as code jumping downwards.
    - Added rows were numbered `line_num + j`, i.e. as if the new text had the
      same line count as the old one, and trailing context kept its pre-edit
      numbering.

    Removed rows carry their number in the OLD file, added and context rows
    theirs in the NEW file -- the removed lines do not exist in the result.
    Every changed region is reported, not just the first: `replace_all` used to
    announce N replacements while showing one.
    """
    import difflib
    old_lines = old_text.splitlines()
    new_lines = new_text.splitlines()
    matcher = difflib.SequenceMatcher(None, old_lines, new_lines, autojunk=False)
    rows: List[Dict[str, Any]] = []
    truncated = False

    def _push(line: int, text: str, kind: str) -> bool:
        """Append a row; False once the cap is reached.

        The cap is enforced here rather than between opcodes: a single
        `replace` opcode can span thousands of lines, and a check that only
        runs between opcodes never fires inside one.
        """
        if len(rows) >= _DIFF_MAX_ROWS:
            return False
        rows.append({"line": line, "text": text, "type": kind})
        return True

    for group in matcher.get_grouped_opcodes(context):
        for tag, i1, i2, j1, j2 in group:
            if tag == "equal":
                spans = [(range(j1, j2), new_lines, "context")]
            else:
                spans = [(range(i1, i2), old_lines, "remove"),
                         (range(j1, j2), new_lines, "add")]
            for indices, source, kind in spans:
                for k in indices:
                    if not _push(k + 1, source[k], kind):
                        truncated = True
                        break
                if truncated:
                    break
            if truncated:
                break
        if truncated:
            break
    if truncated:
        # Never let a bounded view read as a complete one.
        rows.append({"line": 0, "type": "context",
                     "text": f"... diff truncated at {_DIFF_MAX_ROWS} rows"})
    return rows


def _diagnose_edit_mismatch(old_string: str, text: str, filename: str) -> str:
    """Build an actionable error message explaining WHY old_string doesn't match.

    Emits several hints that cover the common causes agents hit repeatedly:
    CRLF vs LF, trailing whitespace, tab/space indentation, and a best-effort
    longest-prefix match pointing at the exact divergence position. The goal
    is to replace 5 useless retries with a single corrective read.
    """
    hints = []

    # CRLF vs LF mismatch
    if '\r\n' in text and '\r\n' not in old_string:
        if old_string.replace('\n', '\r\n') in text:
            hints.append(
                "File uses CRLF line endings; your old_string uses LF. "
                "Re-send old_string with \\r\\n between lines.")
    elif '\r\n' in old_string and '\r\n' not in text:
        if old_string.replace('\r\n', '\n') in text:
            hints.append(
                "File uses LF line endings; your old_string has CRLF. "
                "Strip the \\r from line endings in old_string.")

    # Trailing whitespace mismatch (either direction) — only emit if no
    # more specific hint already covers it. CRLF and tab/space mismatches
    # also make rstripped content match, but their hints are more actionable.
    _specific_hint = bool(hints)
    old_rstripped = '\n'.join(ln.rstrip() for ln in old_string.split('\n'))
    text_rstripped = '\n'.join(ln.rstrip() for ln in text.split('\n'))
    if not _specific_hint and old_rstripped in text_rstripped:
        hints.append(
            "Content matches after rstripping each line — trailing whitespace "
            "differs between your old_string and the file. Re-read the target "
            "lines and copy them verbatim (cat -A or repr() to see exact bytes).")

    # Tabs vs spaces
    if '\t' in text and '\t' not in old_string:
        # Guess the indent width that turns spaces into tabs
        for _w in (4, 2, 8):
            _swapped = old_string.replace(' ' * _w, '\t')
            if _swapped in text:
                hints.append(
                    f"File uses tabs for indentation; your old_string uses "
                    f"{_w}-space indent. Convert runs of {_w} spaces to tabs.")
                break
    elif '\t' in old_string and '\t' not in text:
        for _w in (4, 2, 8):
            _swapped = old_string.replace('\t', ' ' * _w)
            if _swapped in text:
                hints.append(
                    f"File uses spaces for indentation ({_w}-wide); your "
                    f"old_string has tabs. Replace each \\t with {_w} spaces.")
                break

    # Longest-prefix match — where does old_string start diverging?
    _first_line = old_string.split('\n', 1)[0]
    if len(_first_line) >= 8:
        best_prefix = 0
        best_pos = -1
        _pos = 0
        while True:
            _pos = text.find(_first_line, _pos)
            if _pos < 0:
                break
            _mlen = 0
            _stop = min(len(old_string), len(text) - _pos)
            while _mlen < _stop and text[_pos + _mlen] == old_string[_mlen]:
                _mlen += 1
            if _mlen > best_prefix:
                best_prefix = _mlen
                best_pos = _pos
            _pos += 1
        if best_pos >= 0 and best_prefix >= len(_first_line):
            _line_num = text[:best_pos].count('\n') + 1
            _diverge_line = old_string[:best_prefix].count('\n') + 1
            _old_tail = old_string[best_prefix:best_prefix + 60].replace('\n', '\\n')
            _file_tail = text[best_pos + best_prefix:best_pos + best_prefix + 60].replace('\n', '\\n')
            hints.append(
                f"Partial match starts at file line {_line_num}, "
                f"diverges on line {_diverge_line} of old_string "
                f"(after {best_prefix} chars). "
                f"You sent: {_old_tail!r} | File has: {_file_tail!r}")

    if not hints:
        hints.append(
            "No similar content found anywhere in the file. "
            "Re-read the exact lines you want to edit before retrying.")

    return (f"old_string not found in {filename}.\n  - "
            + "\n  - ".join(hints)
            + "\n\nDo NOT retry with the same old_string. "
            "Read the file at the expected line range and copy the exact bytes.")


def action_edit(root_dir: str, path: str, req: Dict[str, Any]) -> Any:
    """Edit a file, either by exact string replacement or by line range.

    Two mutually-exclusive modes (matches EditHandler's JSON schema):
      - string-based: req has `old_string` + `new_string` (+ `replace_all`)
      - line-based:   req has `start_line` + `end_line` + `new_string`
                      (1-based, inclusive end)

    Previously only string-based was implemented; a line-based request
    (no `old_string` in the payload) crashed with
      "Missing 'old_string' parameter"
    even though EditHandler advertises the line-based API and routes to
    it. The line-based branch originally lived in the pawflow_relay.py
    dispatcher copy, which had become dead code and was removed — but the
    fs_actions copy (the one actually reached by the relay) never got it.
    """
    if "old_string" not in req and "old" in req:
        req["old_string"] = req.get("old", "")
    if "new_string" not in req and "new" in req:
        req["new_string"] = req.get("new", "")
    if "old_string" not in req and "old_str" in req:
        req["old_string"] = req.get("old_str", "")
    if "new_string" not in req and "new_str" in req:
        req["new_string"] = req.get("new_str", "")
    new_string = req.get("new_string", "")
    start_line = int(req.get("start_line", 0) or 0)
    end_line = int(req.get("end_line", 0) or 0)
    p = Path(path)

    if start_line > 0 and end_line > 0:
        text = p.read_text(encoding="utf-8")
        lines = text.split("\n")
        if start_line > len(lines) or end_line < start_line:
            raise ValueError(
                f"Invalid line range {start_line}-{end_line} for file "
                f"{p.name} ({len(lines)} lines)")
        s = max(0, start_line - 1)
        e = min(len(lines), end_line)
        removed = lines[s:e]
        new_lines = new_string.split("\n")
        lines[s:e] = new_lines
        p.write_text("\n".join(lines), encoding="utf-8")
        return {
            "lines_replaced": f"{start_line}-{end_line}",
            "lines_removed": len(removed),
            "lines_inserted": len(new_lines),
            "path": _rel(path, root_dir),
        }

    old_string = req.get("old_string", "")
    replace_all = req.get("replace_all", False)
    fuzzy = bool(req.get("fuzzy", False))
    fuzzy_threshold = float(req.get("fuzzy_threshold", 0.92) or 0.92)
    if not old_string:
        raise ValueError(
            "Missing 'old_string' parameter (or provide start_line/end_line "
            "for a line-based edit)")
    text = p.read_text(encoding="utf-8")
    matches = _find_window_matches(
        text, old_string, fuzzy=fuzzy, fuzzy_threshold=fuzzy_threshold)
    count = len(matches)
    if count == 0:
        raise ValueError(_diagnose_edit_mismatch(old_string, text, p.name))
    if count > 1 and not replace_all:
        kinds = ", ".join(sorted({m["kind"] for m in matches}))
        raise ValueError(
            f"old_string found {count} times ({kinds}; use replace_all=true)")
    if replace_all and any(m["kind"] == "fuzzy" for m in matches):
        raise ValueError("fuzzy replace_all is not supported; make the match exact or use one edit per occurrence")

    first_match = matches[0]
    pos = first_match["start"]
    line_num = text[:pos].count("\n") + 1 if pos >= 0 else 0

    # Apply replacement, then diff the before/after texts. Order matters: the
    # diff must describe what was written, not what we intended to write.
    new_text = _apply_replacements(text, matches, new_string, bool(replace_all))
    p.write_text(new_text, encoding="utf-8")
    diff_lines = _build_diff(text, new_text)
    return {
        "replacements": count if replace_all else 1,
        "path": _rel(path, root_dir),
        "diff": diff_lines,
        "line": line_num,
        "match_type": first_match["kind"],
        "similarity": round(float(first_match.get("score", 1.0)), 4),
    }


def action_batch_edit(root_dir: str, path: str, req: Dict[str, Any]) -> Any:
    """Apply multiple edits atomically across files."""
    edits = req.get("edits", [])
    if not edits:
        raise ValueError("Missing 'edits' parameter (list of {path, old_string, new_string})")

    allow_host_absolute = bool(req.get("local", False))
    root = Path(root_dir).resolve()

    # Phase 1: Read files once.
    file_contents = {}
    path_labels = {}
    for i, edit in enumerate(edits):
        fpath = edit.get("path", "")
        if not fpath:
            raise ValueError(f"Edit {i}: missing 'path'")
        abs_path = str(_resolve_tool_path(
            root_dir, fpath, allow_host_absolute=allow_host_absolute))
        path_labels[abs_path] = _rel(abs_path, str(root))
        old_string = edit.get("old_string", "")
        if not old_string:
            raise ValueError(f"Edit {i}: missing 'old_string'")
        if abs_path not in file_contents:
            p = Path(abs_path)
            if not p.is_file():
                raise ValueError(f"Edit {i}: file not found: {fpath}")
            file_contents[abs_path] = p.read_text(encoding="utf-8")

    # Phase 2: Validate and apply in memory, sequentially. This keeps the
    # operation atomic while allowing multiple edits in the same file where a
    # later edit targets content produced or shifted by an earlier edit.
    working = dict(file_contents)
    details = []
    total_replacements = 0
    for i, edit in enumerate(edits):
        fpath = edit.get("path", "")
        abs_path = str(_resolve_tool_path(
            root_dir, fpath, allow_host_absolute=allow_host_absolute))
        text = working[abs_path]
        old_string = edit.get("old_string", "")
        replace_all = bool(edit.get("replace_all", req.get("replace_all", False)))
        fuzzy = bool(edit.get("fuzzy", req.get("fuzzy", False)))
        fuzzy_threshold = float(edit.get(
            "fuzzy_threshold", req.get("fuzzy_threshold", 0.92)) or 0.92)
        matches = _find_window_matches(
            text, old_string, fuzzy=fuzzy, fuzzy_threshold=fuzzy_threshold)
        count = len(matches)
        if count == 0:
            raise ValueError(
                f"Edit {i}: " + _diagnose_edit_mismatch(
                    old_string, text, path_labels.get(abs_path, fpath)))
        if count > 1 and not replace_all:
            kinds = ", ".join(sorted({m["kind"] for m in matches}))
            raise ValueError(
                f"Edit {i}: old_string found {count} times in {fpath} "
                f"({kinds}; use replace_all=true)")
        if replace_all and any(m["kind"] == "fuzzy" for m in matches):
            raise ValueError(
                f"Edit {i}: fuzzy replace_all is not supported in {fpath}; "
                "make the match exact or use one edit per occurrence")
        replacements = count if replace_all else 1
        first = matches[0]
        working[abs_path] = _apply_replacements(
            text, matches, edit.get("new_string", ""), replace_all)
        total_replacements += replacements
        details.append({
            "index": i,
            "path": path_labels.get(abs_path, fpath),
            "replacements": replacements,
            "match_type": first["kind"],
            "similarity": round(float(first.get("score", 1.0)), 4),
            "line": text[:first["start"]].count("\n") + 1,
        })

    # Phase 3: Write all files
    for abs_path, content in working.items():
        Path(abs_path).write_text(content, encoding="utf-8")

    modified = sorted(set(path_labels.get(ap, _rel(ap, str(root))) for ap in working))
    return {
        "edits_applied": len(edits),
        "total_replacements": total_replacements,
        "files_modified": modified,
        "files_modified_count": len(modified),
        "details": details,
    }


def _patch_target(root_dir: str, raw_path: str, *, allow_host_absolute: bool = False):
    root = Path(root_dir).resolve()
    name = (raw_path or "").strip()
    if not name:
        raise ValueError("Patch target path is empty")
    if name.startswith("a/") or name.startswith("b/"):
        name = name[2:]
    target = _resolve_tool_path(
        root_dir, name, allow_host_absolute=allow_host_absolute).resolve()
    try:
        rel = target.relative_to(root).as_posix()
    except ValueError as exc:
        if allow_host_absolute and _is_host_absolute_path(name):
            return target, str(target)
        raise ValueError(f"Patch target escapes workspace: {raw_path}") from exc
    return target, rel


def _find_patch_sequence(lines, seq, start=0):
    if not seq:
        return start
    end = len(lines) - len(seq) + 1
    for idx in range(max(0, start), max(0, end)):
        if lines[idx:idx + len(seq)] == seq:
            return idx
    for idx in range(0, max(0, end)):
        if lines[idx:idx + len(seq)] == seq:
            return idx
    return -1


def _apply_patch_hunks(content: str, hunks, rel: str):
    content_lines = content.splitlines(True)
    cursor = 0
    applied = 0
    for hunk in hunks:
        old_seq = []
        new_seq = []
        for line in hunk:
            if not line:
                continue
            prefix = line[:1]
            body = line[1:]
            if prefix in (" ", "-"):
                old_seq.append(body)
            if prefix in (" ", "+"):
                new_seq.append(body)
        if not old_seq and not new_seq:
            continue
        pos = _find_patch_sequence(content_lines, old_seq, cursor)
        if pos < 0:
            preview = "".join(old_seq[:5]).strip().replace("\n", "\\n")
            raise ValueError(f"Patch context not found in {rel}: {preview[:160]}")
        content_lines[pos:pos + len(old_seq)] = new_seq
        cursor = pos + len(new_seq)
        applied += 1
    return "".join(content_lines), applied


def _parse_openai_patch_sections(patch: str):
    raw_lines = patch.splitlines(True)
    stripped = [ln.rstrip("\r\n") for ln in raw_lines]
    if not stripped or stripped[0] != "*** Begin Patch":
        raise ValueError("OpenAI patch must start with '*** Begin Patch'")
    sections = []
    i = 1
    while i < len(raw_lines):
        marker = stripped[i]
        if marker == "*** End Patch":
            return sections
        if marker.startswith("*** Add File: "):
            path = marker[len("*** Add File: "):].strip()
            i += 1
            body = []
            while i < len(raw_lines) and not stripped[i].startswith("*** "):
                if raw_lines[i].startswith("+"):
                    body.append(raw_lines[i][1:])
                elif raw_lines[i].strip():
                    raise ValueError(f"Invalid Add File line for {path}: {stripped[i]}")
                i += 1
            sections.append(("add", path, body, None))
            continue
        if marker.startswith("*** Delete File: "):
            sections.append(("delete", marker[len("*** Delete File: "):].strip(), [], None))
            i += 1
            continue
        if marker.startswith("*** Update File: "):
            path = marker[len("*** Update File: "):].strip()
            i += 1
            hunks = []
            current = []
            move_to = None
            while i < len(raw_lines) and not stripped[i].startswith("*** Update File: ") and not stripped[i].startswith("*** Add File: ") and not stripped[i].startswith("*** Delete File: ") and stripped[i] != "*** End Patch":
                line = raw_lines[i]
                sm = stripped[i]
                if sm.startswith("*** Move to: "):
                    move_to = sm[len("*** Move to: "):].strip()
                elif sm == "*** End of File":
                    pass
                elif line.startswith("@@"):
                    if current:
                        hunks.append(current)
                    current = []
                elif line.startswith((" ", "+", "-")):
                    if current is None:
                        current = []
                    current.append(line)
                elif sm.startswith("\\ No newline"):
                    pass
                elif sm.strip():
                    raise ValueError(f"Invalid Update File line for {path}: {sm}")
                i += 1
            if current:
                hunks.append(current)
            sections.append(("update", path, hunks, move_to))
            continue
        if marker.strip():
            raise ValueError(f"Unsupported patch marker: {marker}")
        i += 1
    raise ValueError("OpenAI patch missing '*** End Patch'")


def _apply_openai_patch(root_dir: str, patch: str, *,
                        allow_host_absolute: bool = False) -> Dict[str, Any]:
    sections = _parse_openai_patch_sections(patch)
    if not sections:
        raise ValueError("Patch did not contain any applicable hunks")

    # Build the complete post-patch state in memory first. A later invalid
    # section must not leave earlier files changed while the tool reports an
    # error for the overall call.
    pending: Dict[Path, Optional[str]] = {}
    files_modified = []
    hunks_applied = 0

    def _exists(target: Path) -> bool:
        return pending[target] is not None if target in pending else target.exists()

    def _read(target: Path) -> str:
        if target in pending:
            value = pending[target]
            if value is None:
                raise ValueError(f"Patch target does not exist: {target}")
            return value
        return target.read_text(encoding="utf-8")

    for action, raw_path, payload, move_to in sections:
        target, rel = _patch_target(
            root_dir, raw_path, allow_host_absolute=allow_host_absolute)
        if action == "add":
            if _exists(target):
                raise ValueError(f"Add File target already exists: {rel}")
            pending[target] = "".join(payload)
            if rel not in files_modified:
                files_modified.append(rel)
            hunks_applied += 1
        elif action == "delete":
            if not _exists(target):
                raise ValueError(f"Delete File target does not exist: {rel}")
            pending[target] = None
            if rel not in files_modified:
                files_modified.append(rel)
            hunks_applied += 1
        elif action == "update":
            if not _exists(target):
                raise ValueError(f"Update File target does not exist: {rel}")
            new_content, applied = _apply_patch_hunks(
                _read(target), payload, rel)
            if applied == 0 and not move_to:
                raise ValueError(f"Patch did not contain applicable hunks for {rel}")
            dest = target
            dest_rel = rel
            if move_to:
                dest, dest_rel = _patch_target(
                    root_dir, move_to, allow_host_absolute=allow_host_absolute)
                if dest != target:
                    pending[target] = None
            pending[dest] = new_content
            if dest_rel not in files_modified:
                files_modified.append(dest_rel)
            hunks_applied += applied or 1

    if not files_modified or hunks_applied <= 0:
        raise ValueError("Patch did not modify any files")

    originals = {
        target: target.read_bytes() if target.exists() else None
        for target in pending
    }
    created_dirs = set()
    try:
        for target, content in pending.items():
            if content is None:
                target.unlink(missing_ok=True)
            else:
                parent = target.parent
                missing = []
                while not parent.exists():
                    missing.append(parent)
                    if parent == parent.parent:
                        break
                    parent = parent.parent
                created_dirs.update(missing)
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text(content, encoding="utf-8")
    except Exception as original_error:
        rollback_errors = []
        for target, original in originals.items():
            try:
                if original is None:
                    target.unlink(missing_ok=True)
                else:
                    target.parent.mkdir(parents=True, exist_ok=True)
                    target.write_bytes(original)
            except Exception as rollback_error:
                rollback_errors.append(rollback_error)
        for directory in sorted(
                created_dirs, key=lambda item: len(item.parts), reverse=True):
            try:
                directory.rmdir()
            except FileNotFoundError:
                pass
            except OSError as rollback_error:
                rollback_errors.append(rollback_error)
        if rollback_errors:
            original_error.rollback_errors = tuple(rollback_errors)
        raise

    return {
        "method": "openai_apply_patch",
        "files_modified": files_modified,
        "hunks_applied": hunks_applied,
        "applied": True,
    }


def _parse_hunk_body(lines, i):
    """Consume one hunk body. Returns (old_side, new_side, next_index)."""
    old_side, new_side = [], []
    while i < len(lines):
        dl = lines[i]
        if dl.startswith(("@@", "diff ", "--- ", "+++ ")):
            break
        if dl.startswith("\\"):
            # "\ No newline at end of file" annotates the previous line.
            i += 1
            continue
        if dl.startswith("-"):
            old_side.append(dl[1:])
        elif dl.startswith("+"):
            new_side.append(dl[1:])
        elif dl.startswith(" "):
            old_side.append(dl[1:])
            new_side.append(dl[1:])
        elif dl in ("\n", "\r\n", ""):
            # Some emitters drop the leading space on an empty context line.
            old_side.append(dl)
            new_side.append(dl)
        else:
            break
        i += 1
    return old_side, new_side, i


def _parse_hunk_header(line):
    """Return (old_start, old_count, new_start, new_count), or None.

    None means a bare `@@` carrying no numbers at all.
    """
    m = re.match(r"@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@", line)
    if not m:
        return None
    return (int(m.group(1)), 1 if m.group(2) is None else int(m.group(2)),
            int(m.group(3)), 1 if m.group(4) is None else int(m.group(4)))


def _hunk_hint(header, offset):
    """0-based index the header points at, shifted by the hunks before it.

    A zero old-side count means "insert after old line N", so the index is N
    itself rather than N-1.
    """
    if header is None:
        return 0
    old_start, old_count = header[0], header[1]
    return (old_start - 1 if old_count else old_start) + offset


def _header_is_sound(header, old_side, new_side, offset):
    """True when the @@ numbers agree with the body and with what precedes.

    A unified diff header is redundant three times over: each count restates
    its side's line total, and the new-side start restates the old-side start
    shifted by every hunk already applied. That redundancy is what makes a
    hunk with no context checkable at all. If all three agree, the stated
    position is corroborated by the diff's own arithmetic; if any disagrees,
    the numbers were not produced by a diff tool and cannot be trusted to
    place anything.
    """
    if header is None:
        return False
    old_start, old_count, new_start, new_count = header
    if old_count != len(old_side) or new_count != len(new_side):
        return False
    old_index = old_start - 1 if old_count else old_start
    new_index = new_start - 1 if new_count else new_start
    return new_index == old_index + offset


def _locate_hunk(content_lines, old_side, hint):
    """Index where old_side occurs, nearest to hint; -1 when it does not.

    The @@ number is treated as a hint, not an address. A diff written
    against a slightly stale copy, or with hand-counted @@ numbers, still
    applies as long as its context is found somewhere. What is never
    allowed is applying a hunk *without* finding its context - an empty
    old_side has nothing to corroborate, so it is refused here and settled
    by the header's arithmetic instead.
    """
    if not old_side:
        return -1
    last = len(content_lines) - len(old_side)
    if last < 0:
        return -1
    hint = max(0, min(hint, last))
    for delta in range(last + 1):
        for pos in (hint - delta, hint + delta):
            if 0 <= pos <= last and content_lines[pos:pos + len(old_side)] == old_side:
                return pos
    return -1


def action_apply_patch(root_dir: str, path: str, req: Dict[str, Any]) -> Any:
    """Apply a unified diff patch or Codex/OpenAI *** Begin Patch block."""
    patch = req.get("patch", "")
    if not patch:
        raise ValueError("Missing 'patch' parameter")

    allow_host_absolute = bool(req.get("local", False))

    if patch.lstrip().startswith("*** Begin Patch"):
        return _apply_openai_patch(
            root_dir, patch.lstrip(), allow_host_absolute=allow_host_absolute)

    # git apply is the reference implementation: it verifies context and
    # names the offending line. Prefer it, and keep what it says when it
    # refuses - that diagnostic used to be discarded, which is how a patch
    # git had already rejected went on to be applied blind below.
    git_error = ""
    try:
        result = subprocess.run(  # nosec B603, B607
            ["git", "apply", "--stat", "-"],
            input=patch, cwd=root_dir,
            capture_output=True, text=True, timeout=10,
        )
        if result.returncode == 0:
            stat_output = result.stdout.strip()
            # --unidiff-zero or git exits 0 having done nothing at all on a
            # zero-context hunk, the shape `diff -U0` emits: it demands one
            # line of context and skips the hunk in silence otherwise, which
            # this tool then reported as a successful patch. The flag only
            # relaxes hunks that carry no context; one measured against a
            # patch that has context is applied identically either way.
            result = subprocess.run(  # nosec B603, B607
                ["git", "apply", "--unidiff-zero", "-"],
                input=patch, cwd=root_dir,
                capture_output=True, text=True,
            )
            if result.returncode == 0:
                if not stat_output:
                    raise ValueError("Patch did not contain any applicable hunks")
                return {"method": "git_apply", "stats": stat_output, "applied": True}
            raise ValueError(f"git apply failed: {result.stderr}")
        git_error = (result.stderr or result.stdout).strip()
    except FileNotFoundError:
        git_error = ""
    except subprocess.TimeoutExpired:
        raise ValueError("Patch application timed out")

    # Manual fallback, for a missing git or a header git will not parse
    # (hand-counted @@ numbers being the common one).
    #
    # It used to apply positionally: it took the @@ number as an index into
    # the buffer and popped/inserted there, never comparing what it removed.
    # That corrupted files two ways. A hunk whose context did not exist was
    # applied anyway, to whatever happened to sit at that offset. And the
    # old-side number was indexed into the buffer *already shifted* by the
    # preceding hunks, so every hunk after the first landed off by the net
    # line delta before it. Both are now located by context or refused.
    pending = {}
    files_modified = []
    current_file = None
    current_rel = ""
    current_content = None
    offset = 0
    hunks_applied = 0

    def _fail(detail):
        raise ValueError(
            f"{detail}{f' (git apply said: {git_error})' if git_error else ''}")

    lines = patch.splitlines(True)
    i = 0
    while i < len(lines):
        line = lines[i]
        if line.startswith("+++ "):
            fname = line[6:].strip() if line.startswith("+++ b/") else line[4:].strip()
            if fname == "/dev/null":
                current_file = None
                current_content = None
                offset = 0
                i += 1
                continue
            target, current_rel = _patch_target(
                root_dir, fname, allow_host_absolute=allow_host_absolute)
            current_file = str(target)
            current_content = pending.get(
                current_file,
                target.read_text(encoding="utf-8") if target.is_file() else "")
            pending[current_file] = current_content
            if current_rel not in files_modified:
                files_modified.append(current_rel)
            offset = 0
            i += 1
            continue
        if line.startswith("--- "):
            i += 1
            continue
        if line.startswith("@@") and current_content is not None:
            header = _parse_hunk_header(line)
            old_side, new_side, i = _parse_hunk_body(lines, i + 1)
            if not old_side and not new_side:
                continue
            content_lines = current_content.splitlines(True)
            where = header[0] if header else "?"
            if old_side:
                # Context is the strongest anchor, and it survives numbers
                # that are wrong or absent.
                pos = _locate_hunk(
                    content_lines, old_side, _hunk_hint(header, offset))
                if pos < 0:
                    _fail(
                        f"hunk at line {where} does not apply to {current_rel}: "
                        f"its context is nowhere in the file"
                    )
            else:
                # A pure insertion (as `diff -U0` emits) has nothing to match,
                # so the header's own arithmetic is the only evidence there is.
                # It therefore has to hold exactly, rather than be trusted.
                if not _header_is_sound(header, old_side, new_side, offset):
                    _fail(
                        f"hunk at line {where} inserts into {current_rel} with no "
                        f"context, and its @@ numbers do not check out against "
                        f"the hunk body or the hunks before it - refusing to "
                        f"guess where it goes"
                    )
                pos = _hunk_hint(header, offset)
                if not 0 <= pos <= len(content_lines):
                    _fail(
                        f"hunk at line {where} inserts past the end of "
                        f"{current_rel}"
                    )
            content_lines[pos:pos + len(old_side)] = new_side
            current_content = "".join(content_lines)
            pending[current_file] = current_content
            offset += len(new_side) - len(old_side)
            hunks_applied += 1
            continue
        i += 1

    if not files_modified or hunks_applied <= 0:
        _fail("Patch did not contain any applicable unified diff hunks")

    # Written only once every hunk has been located, so a bad hunk late in
    # the patch cannot leave the earlier files rewritten.
    for abs_path, content in pending.items():
        Path(abs_path).write_text(content, encoding="utf-8")

    return {"method": "manual_unified", "files_modified": files_modified,
            "hunks_applied": hunks_applied, "applied": True}
