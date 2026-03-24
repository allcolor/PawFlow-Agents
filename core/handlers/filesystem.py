"""Auto-extracted from core/tool_registry.py — see core/handlers/__init__.py"""

import json
import logging
import re
import threading
from typing import Dict, Any, List, Optional

from core.tool_handler import ToolHandler

logger = logging.getLogger(__name__)



class FilesystemToolHandler(ToolHandler):
    """Agent tool for filesystem operations via a filesystem service.

    Auto-detects the user's filesystem service, or uses the explicitly
    specified service name. Supports all FilesystemBackend operations
    including git.
    """

    _user_id: str = ""
    _conversation_id: str = ""
    _checkpoint_id: str = ""
    _available_services: List[Dict[str, Any]] = []  # Plan D: list of compatible services

    # Filesystem service types (checked in order for auto-detection)
    _FS_TYPES = ("filesystem", "browserFilesystem", "serverFilesystem",
                 "googleDrive", "oneDrive")

    @property
    def name(self) -> str:
        return "filesystem"

    @property
    def description(self) -> str:
        desc = (
            "Access files and run commands on the user's filesystem through a configured service. "
            "Actions: list_dir, read_file (supports offset/limit for pagination), read_pdf, read_notebook (.ipynb), edit_notebook (edit/insert/delete cells), "
            "write_file (use content for text OR file_id to copy a server file like generated images), "
            "edit (exact string replace OR line-based: use start_line/end_line/new_string to replace a range of lines), "
            "batch_edit (atomic multi-file edit), apply_patch (unified diff), "
            "delete_file, mkdir, stat, exists, search (glob), grep (regex), find_replace. "
            "Shell: exec — run any shell command (e.g. exec with command='cat file.txt' or command='ls -la'). "
            "Git: git_status, git_log, git_diff, git_commit (files, amend), git_pull, git_push, git_checkout, "
            "git_add, git_reset, git_stash, git_branch, git_merge, git_rebase, git_cherry_pick, git_tag, git_blame, "
            "git_worktree_list, git_worktree_add, git_worktree_remove. "
            "Transfer: copy_to_store (filesystem→FileStore), "
            "copy_between (any combination: filesystem↔filesystem, FileStore↔filesystem — "
            "use 'FileStore' as source_service or dest_service to read/write from the server file store), "
            "list_store (list FileStore files), delete_from_store (delete from FileStore). "
            "Project: project_init (generate .pawflow.md). "
            "Paths support fs:// URLs: fs://service_id/path or fs://filestore/file_id for server FileStore. "
            "Paths are relative to the service root. "
            "If only one filesystem is connected, any service name resolves to it. Use the 'service' parameter with the exact service name from the conversation context. "
            "Git workflow: use git_tag to create checkpoints before major changes (e.g. 'v33-stable'). "
            "Use git_branch to try alternatives. Use git_stash to save work-in-progress. "
            "Use git_diff to review changes before committing. "
        )
        if len(self._available_services) > 1:
            svc_desc = ", ".join(
                f"'{s['id']}' ({s.get('type', '?')}, root={s.get('root', '?')})"
                for s in self._available_services
            )
            desc += f" Available services: {svc_desc}. Use 'service' parameter to choose."
        return desc

    @property
    def parameters_schema(self) -> Dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "action": {
                    "type": "string",
                    "enum": [
                        "list_dir", "read_file", "read_pdf", "read_notebook",
                        "edit_notebook",
                        "write_file", "edit", "batch_edit", "apply_patch",
                        "delete_file", "mkdir", "stat", "exists",
                        "search", "grep", "find_replace", "exec",
                        "git_status", "git_log", "git_diff", "git_commit",
                        "git_pull", "git_push", "git_checkout",
                        "git_add", "git_reset", "git_stash", "git_branch",
                        "git_merge", "git_rebase", "git_cherry_pick",
                        "git_tag", "git_blame",
                        "project_init",
                        "git_worktree_list", "git_worktree_add", "git_worktree_remove",
                        "copy_to_store", "copy_between", "list_store", "delete_from_store",
                    ],
                    "description": "The filesystem operation to perform",
                },
                "path": {
                    "type": "string",
                    "description": "Relative path within the service root",
                },
                "content": {
                    "type": "string",
                    "description": "File content for write_file (text). For binary files, use file_id instead.",
                },
                "file_id": {
                    "type": "string",
                    "description": "Copy a server file (from generate_image, create_file, etc.) to the filesystem path. Use this instead of content for images/binary files.",
                },
                "pattern": {
                    "type": "string",
                    "description": "Glob pattern for search, or regex for find_replace",
                },
                "regex": {
                    "type": "string",
                    "description": "Regex pattern for grep",
                },
                "replacement": {
                    "type": "string",
                    "description": "Replacement text for find_replace",
                },
                "recursive": {
                    "type": "boolean",
                    "description": "Recursive search/grep (default: true)",
                },
                "service": {
                    "type": "string",
                    "description": "Service name (optional — auto-detects if omitted)",
                },
                "old_string": {
                    "type": "string",
                    "description": "Exact string to find (for edit action). Alternative: use start_line/end_line for line-based edit.",
                },
                "new_string": {
                    "type": "string",
                    "description": "Replacement string (for edit action — both string-match and line-based modes)",
                },
                "command": {
                    "type": "string",
                    "description": "Shell command to execute ON THE USER'S MACHINE (for exec action). cwd is the filesystem root. fs:// URLs are auto-resolved to real paths. $PAWFLOW_FS_ROOT env var points to the root.",
                },
                "max_pages": {
                    "type": "integer",
                    "description": "Max pages to extract from PDF (default: 50, for read_pdf action)",
                },
                "ref": {
                    "type": "string",
                    "description": "Git ref for diff/checkout",
                },
                "message": {
                    "type": "string",
                    "description": "Commit message for git_commit",
                },
                "count": {
                    "type": "integer",
                    "description": "Number of entries for git_log (default: 10)",
                },
                "cell_index": {
                    "type": "integer",
                    "description": "Cell index for edit_notebook",
                },
                "new_source": {
                    "type": "string",
                    "description": "New cell source for edit_notebook",
                },
                "cell_type": {
                    "type": "string",
                    "description": "Cell type (code/markdown) for edit_notebook",
                },
                "operation": {
                    "type": "string",
                    "description": "Operation for edit_notebook: edit, insert, or delete",
                },
                "branch": {
                    "type": "string",
                    "description": "Branch name for git_worktree_add",
                },
                "worktree_path": {
                    "type": "string",
                    "description": "Worktree path for git_worktree_add/remove",
                },
                "create_new_branch": {
                    "type": "boolean",
                    "description": "Create a new branch for git_worktree_add (default: false)",
                },
                "files": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "File paths for git_add, git_reset, or selective git_commit",
                },
                "commits": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Commit hashes for git_cherry_pick",
                },
                "tag": {
                    "type": "string",
                    "description": "Tag name for git_tag",
                },
                "onto": {
                    "type": "string",
                    "description": "Target branch for git_rebase",
                },
                "no_ff": {
                    "type": "boolean",
                    "description": "No fast-forward for git_merge",
                },
                "amend": {
                    "type": "boolean",
                    "description": "Amend last commit for git_commit",
                },
                "mode": {
                    "type": "string",
                    "description": "Reset mode: mixed, soft, hard (for git_reset)",
                },
                "index": {
                    "type": "integer",
                    "description": "Stash index for git_stash drop",
                },
                "force": {
                    "type": "boolean",
                    "description": "Force flag for git_branch delete",
                },
                "base": {
                    "type": "string",
                    "description": "Base ref for git_branch create",
                },
                "file": {
                    "type": "string",
                    "description": "File path for git_blame",
                },
                "start_line": {
                    "type": "integer",
                    "description": "Start line (1-based) for line-based edit or git_blame. Use with end_line + new_string to replace lines.",
                },
                "end_line": {
                    "type": "integer",
                    "description": "End line (inclusive) for line-based edit or git_blame.",
                },
                "edits": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "path": {"type": "string"},
                            "old_string": {"type": "string"},
                            "new_string": {"type": "string"},
                        },
                    },
                    "description": "List of edits for batch_edit: [{path, old_string, new_string}]",
                },
                "patch": {
                    "type": "string",
                    "description": "Unified diff content for apply_patch",
                },
                "source_service": {
                    "type": "string",
                    "description": "Source for copy_between: a filesystem service name OR 'FileStore' for server files",
                },
                "source_path": {
                    "type": "string",
                    "description": "Source file path for copy_between",
                },
                "dest_service": {
                    "type": "string",
                    "description": "Destination for copy_between: a filesystem service name OR 'FileStore' for server files",
                },
                "dest_path": {
                    "type": "string",
                    "description": "Destination file path for copy_between",
                },
                "offset": {
                    "type": "integer",
                    "description": "Start line (1-based) for read_file. Use with limit to read large files in chunks.",
                },
                "limit": {
                    "type": "integer",
                    "description": "Max lines to read for read_file (default: all if file < 4000 chars, else 100 lines). Also limits grep/git_log output lines.",
                },
                "max_output": {
                    "type": "integer",
                    "description": "Max output chars for exec (default: 4000). Set higher only if you need the full output.",
                },
            },
            "required": ["action"],
        }

    def set_user_id(self, user_id: str):
        self._user_id = user_id

    def set_conversation_id(self, conversation_id: str):
        self._conversation_id = conversation_id

    def set_checkpoint_id(self, checkpoint_id: str):
        self._checkpoint_id = checkpoint_id

    def set_available_services(self, services: List[Dict[str, Any]]) -> None:
        """Plan D: set list of available filesystem services for multi-service selection."""
        self._available_services = services

    def _execute_filestore(self, action: str, path: str, arguments: dict) -> str:
        """Handle read/write/list/delete on the server FileStore.

        Routed when service_name is 'filestore'/'store'/'server'.
        Path format: file_id/filename or just file_id.
        """
        from core.file_store import FileStore
        import re as _re_fs
        store = FileStore.instance()

        # Extract file_id from path (could be "abc123/file.png" or "/files/abc123/file.png" or just "abc123")
        _fid_match = _re_fs.search(r'/?(?:files/)?([a-f0-9]{12})(?:/|$)', path)
        file_id = _fid_match.group(1) if _fid_match else path.split("/")[0]

        if action in ("read_file", "read"):
            entry = store.get(file_id)
            if not entry:
                found = store.find_by_name(file_id)
                if found:
                    entry = store.get(found)
            if not entry:
                return f"Error: '{file_id}' not found in FileStore"
            fname, data, ct = entry
            if ct and ct.startswith("image/"):
                import base64 as _b64
                b64 = _b64.b64encode(data).decode("ascii")
                url = f"/files/{file_id}/{fname}"
                return f"Image: {url}\n__image_data__:{ct}:{b64}"
            try:
                text = data.decode("utf-8")
                return text
            except UnicodeDecodeError:
                return f"Binary file: {fname} ({len(data):,} bytes, {ct})"

        elif action in ("list_dir", "list", "ls"):
            entries = store.list_all() if hasattr(store, 'list_all') else []
            if not entries:
                return "(FileStore is empty)"
            lines = [f"📄 fs://filestore/{e['id']}/{e['name']} ({e.get('size', '?')} bytes)"
                     for e in entries[:50]]
            if len(entries) > 50:
                lines.append(f"... +{len(entries) - 50} more")
            return "\n".join(lines)

        elif action in ("delete_file", "delete", "rm"):
            store.delete(file_id)
            return f"Deleted '{file_id}' from FileStore"

        elif action == "exists":
            entry = store.get(file_id)
            return "true" if entry else "false"

        elif action == "stat":
            entry = store.get(file_id)
            if not entry:
                return f"Error: '{file_id}' not found in FileStore"
            fname, data, ct = entry
            return json.dumps({"name": fname, "size": len(data), "content_type": ct})

        return (f"Error: action '{action}' not supported on FileStore. "
                f"FileStore is read-only (temporary server storage). "
                f"Supported: read_file, list_dir, delete_file, exists, stat. "
                f"To write files, use a filesystem service (fs://service_name/path).")

    def _find_service(self, service_name: str = ""):
        """Find a filesystem service by name or auto-detect.

        Search order: GlobalServiceRegistry → UserServiceRegistry.
        If service_name is given, resolve that specific service.
        If empty, find the first available filesystem service.
        Fallback: if service_name not found but only one FS exists, use it.
        """
        # "workspace" alias — always resolves to the first available FS
        if service_name.lower() in ("workspace", "ws", "local"):
            return self._find_service("")  # auto-detect

        def _set_uid(svc):
            if hasattr(svc, 'set_user_id') and self._user_id:
                svc.set_user_id(self._user_id)
            return svc

        # Search GlobalServiceRegistry
        try:
            from gui.services.global_service_registry import GlobalServiceRegistry
            greg = GlobalServiceRegistry.get_instance()
            if service_name:
                svc = greg.get_live_instance(service_name)
                if svc:
                    return _set_uid(svc)
            else:
                for sid, sdef in greg.get_all_definitions().items():
                    if not getattr(sdef, "enabled", True):
                        continue
                    if getattr(sdef, "service_type", "") in self._FS_TYPES:
                        svc = greg.get_live_instance(sid)
                        if svc:
                            return _set_uid(svc)
        except Exception:
            pass

        # Search UserServiceRegistry
        if self._user_id:
            try:
                from gui.services.user_service_registry import UserServiceRegistry
                ureg = UserServiceRegistry.get_instance()
                if service_name:
                    svc = ureg.get_live_instance(self._user_id, service_name)
                    if svc:
                        return _set_uid(svc)
                else:
                    for fs_type in self._FS_TYPES:
                        compatible = ureg.get_compatible(fs_type, self._user_id)
                        for sdef in compatible:
                            if sdef.enabled:
                                svc = ureg.get_live_instance(self._user_id, sdef.service_id)
                                if svc:
                                    return _set_uid(svc)
            except Exception:
                pass

        # Fallback: if a specific name was requested but not found,
        # and there's exactly one FS service available, use it
        if service_name:
            only = self._find_service("")  # auto-detect (no name)
            if only:
                return only

        return None

    def _checkpoint_before(self, svc, path: str, content_after: bytes = None,
                           is_delete: bool = False, service_name: str = "") -> None:
        """Capture file state for /rewind support (if checkpointing is active)."""
        if not self._conversation_id or not self._checkpoint_id:
            return
        try:
            from core.checkpoint import CheckpointManager
            if is_delete:
                CheckpointManager.capture_before_delete(
                    svc, path, self._conversation_id, self._checkpoint_id,
                    service_name)
            else:
                CheckpointManager.capture_before_write(
                    svc, path, content_after or b"",
                    self._conversation_id, self._checkpoint_id,
                    service_name)
        except Exception as e:
            logger.debug(f"[checkpoint] capture failed for {path}: {e}")

    def execute(self, arguments: Dict[str, Any]) -> str:
        # Resolve expressions in all arguments (secrets in commands, paths, etc.)
        from core.expression import resolve_value
        arguments = resolve_value(arguments, owner=self._user_id)
        result = self._execute_inner(arguments)
        # Append service hint if a fallback was used
        if hasattr(self, '_last_service_hint') and self._last_service_hint:
            hint = self._last_service_hint
            self._last_service_hint = ""
            return result + hint
        return result

    def _execute_inner(self, arguments: Dict[str, Any]) -> str:
        action = arguments.get("action", "")
        path = arguments.get("path", ".")
        service_name = arguments.get("service", "")
        self._last_service_hint = ""

        # Parse fs:// URLs: fs://service_id/path/to/file
        if path.startswith("fs://"):
            parts = path[5:].split("/", 1)
            service_name = parts[0]
            path = parts[1] if len(parts) > 1 else "."

        # Route filestore:// to FileStore directly (read/write/delete)
        _fs_aliases = ("filestore", "store", "server")
        if service_name.lower() in _fs_aliases:
            return self._execute_filestore(action, path, arguments)

        # Plan D: try explicit service first, then injected, then search
        svc = None
        if service_name:
            svc = self._find_service(service_name)
            # Check if fallback was used (service found under different name)
            if svc:
                actual_id = getattr(svc, 'service_id', '') or getattr(svc, '_service_id', '')
                if actual_id and actual_id != service_name:
                    self._last_service_hint = f"\n[Note: '{service_name}' not found — using '{actual_id}'. Use service='{actual_id}' in future calls.]"
        if svc is None:
            svc = getattr(self, '_fs_service', None)
        if svc is None:
            return (
                "Error: No filesystem service configured. "
                "Install one with: /service install localFilesystem <name> "
                "host=localhost,port=9876,secret=<secret>,mode=readwrite\n"
                "Then run: python tools/pawflow_relay.py --port 9876 "
                "--dir <path> --secret <secret>"
            )

        # Normalize common LLM aliases
        _action_aliases = {
            "read": "read_file", "write": "write_file", "delete": "delete_file",
            "ls": "list_dir", "cat": "read_file", "rm": "delete_file",
        }
        action = _action_aliases.get(action, action)

        try:
            if action == "list_dir":
                entries = svc.list_dir(path)
                # Determine service name for fs:// URLs
                _svc_name = service_name or getattr(svc, 'service_id', '') or 'fs'
                _base = f"fs://{_svc_name}/{path.rstrip('/')}/" if path != "." else f"fs://{_svc_name}/"
                lines = []
                for e in entries:
                    kind = "📁" if e.kind == "directory" else "📄"
                    size = f" ({e.size} bytes)" if e.kind == "file" else ""
                    lines.append(f"{kind} {_base}{e.name}{size}")
                return "\n".join(lines) if lines else "(empty directory)"

            elif action == "read_file":
                data = svc.read_file(path)
                fname = path.rsplit("/", 1)[-1] if "/" in path else path
                # Images: store in FileStore and return viewable URL
                _img_exts = (".png", ".jpg", ".jpeg", ".gif", ".webp", ".svg", ".bmp")
                if any(fname.lower().endswith(ext) for ext in _img_exts):
                    from core.file_store import FileStore
                    import mimetypes
                    mime = mimetypes.guess_type(fname)[0] or "image/png"
                    fid = FileStore.instance().store(fname, data, mime,
                                                       user_id=self._user_id)
                    file_base = self.config.get("file_base_url", "") or ""
                    if file_base:
                        url = f"{file_base}/files/{fid}/{fname}"
                    else:
                        url = f"/files/{fid}/{fname}"
                    # Include base64 so agent loop can send as multimodal image
                    import base64 as _b64img
                    b64 = _b64img.b64encode(data).decode("ascii")
                    return f"Image: {url}\n__image_data__:{mime}:{b64}"
                # PDF: auto-redirect to read_pdf
                if fname.lower().endswith(".pdf"):
                    max_pages = arguments.get("max_pages", 50)
                    result = svc._request("read_pdf", path, max_pages=max_pages)
                    if isinstance(result, dict) and "pages" in result:
                        lines = [f"PDF: {result.get('total_pages', '?')} pages"]
                        for p_data in result["pages"]:
                            lines.append(f"\n--- Page {p_data['page']} ---\n{p_data['text']}")
                        return "\n".join(lines)
                    return json.dumps(result)
                # Notebook: auto-redirect to read_notebook
                if fname.lower().endswith(".ipynb"):
                    result = svc._request("read_notebook", path)
                    if isinstance(result, dict) and "cells" in result:
                        lines = [f"Notebook: {result.get('total_cells', '?')} cells "
                                 f"(kernel: {result.get('kernel', '?')})"]
                        for c in result["cells"]:
                            header = f"\n### Cell {c['index']} [{c['type']}]"
                            lines.append(header)
                            if c["source"]:
                                lines.append(f"```\n{c['source']}\n```")
                            if c.get("output"):
                                lines.append(f"Output:\n```\n{c['output']}\n```")
                        return "\n".join(lines)
                    return json.dumps(result)
                # Text files — support offset/limit for pagination
                _MAX_PAGE = 4096  # max chars per read — forces pagination
                try:
                    text = data.decode("utf-8")
                except UnicodeDecodeError:
                    return f"(binary file, {len(data)} bytes)"
                _offset = int(arguments.get("offset", 0) or 0)
                _limit = int(arguments.get("limit", 0) or 0)
                lines = text.split("\n")
                total_lines = len(lines)
                total_chars = len(text)

                # Always paginate: select lines by offset/limit
                start = max(0, _offset - 1) if _offset > 0 else 0
                end = start + _limit if _limit else total_lines
                selected = lines[start:end]

                # Enforce max page size — trim lines until under 4KB
                output_lines = []
                output_chars = 0
                for i, ln in enumerate(selected):
                    line_text = f"{start + i + 1:4d}\t{ln}\n"
                    if output_chars + len(line_text) > _MAX_PAGE and output_lines:
                        end = start + i  # actual end
                        break
                    output_lines.append(line_text)
                    output_chars += len(line_text)

                has_more = end < total_lines
                header = f"[{fname}: {total_lines} lines, {total_chars:,} chars"
                if start > 0 or has_more:
                    header += f", showing lines {start+1}-{min(end, total_lines)}"
                if has_more:
                    header += (f" — use offset={end+1} to read next page"
                               f" (MUST paginate, max {_MAX_PAGE} chars/page)")
                header += "]"

                # Small files that fit entirely — return as-is with line numbers
                if not has_more and start == 0:
                    if total_chars <= _MAX_PAGE:
                        return header + "\n" + "".join(output_lines)

                return header + "\n" + "".join(output_lines)

            elif action == "read_pdf":
                max_pages = arguments.get("max_pages", 50)
                result = svc._request("read_pdf", path, max_pages=max_pages)
                if isinstance(result, dict) and "pages" in result:
                    lines = [f"PDF: {result.get('total_pages', '?')} pages "
                             f"({result.get('extracted_pages', '?')} extracted)"]
                    for p_data in result["pages"]:
                        lines.append(f"\n--- Page {p_data['page']} ---\n{p_data['text']}")
                    return "\n".join(lines)
                return json.dumps(result)

            elif action == "edit_notebook":
                cell_index = arguments.get("cell_index")
                new_source = arguments.get("new_source", "")
                cell_type = arguments.get("cell_type", "")
                operation = arguments.get("operation", "edit")
                result = svc._request("edit_notebook", path,
                                       cell_index=cell_index, new_source=new_source,
                                       cell_type=cell_type, operation=operation)
                op = result.get("operation", operation)
                idx = result.get("cell_index", cell_index)
                total = result.get("total_cells", "?")
                return f"Notebook {op}: cell {idx} ({total} cells total)"

            elif action == "write_file":
                # Checkpoint before write
                _wf_content = (arguments.get("content") or arguments.get("command")
                               or arguments.get("data") or arguments.get("text") or "")
                self._checkpoint_before(svc, path,
                    _wf_content.encode("utf-8") if isinstance(_wf_content, str) else _wf_content,
                    service_name=service_name)
                file_id = arguments.get("file_id", "")
                if file_id:
                    # Extract file_id from URL if the LLM passed one
                    # e.g. "http://host/files/abc123/file.png" → "abc123"
                    import re as _re_fid
                    url_match = _re_fid.search(r'/files/([^/]+)/', file_id)
                    if url_match:
                        file_id = url_match.group(1)
                    # Copy from FileStore to filesystem
                    from core.file_store import FileStore
                    store = FileStore.instance()
                    # Try file_id directly, then search by filename
                    entry = store.get(file_id)
                    if not entry:
                        found_id = store.find_by_name(file_id)
                        if found_id:
                            entry = store.get(found_id)
                    if not entry:
                        return f"Error: file_id '{file_id}' not found in FileStore"
                    fname, data, _ct = entry
                    svc.write_file(path, data)
                    return f"Copied {fname} ({len(data):,} bytes) to {path}"
                # Accept "content" (schema) or common LLM mistakes: "command", "data", "text"
                content = (arguments.get("content")
                           or arguments.get("command")
                           or arguments.get("data")
                           or arguments.get("text")
                           or "")
                if not content:
                    return f"Error: write_file requires 'content' or 'file_id' parameter"
                svc.write_file(path, content.encode("utf-8"))
                return f"Written {len(content)} chars to {path}"

            elif action == "delete_file":
                self._checkpoint_before(svc, path, is_delete=True,
                                        service_name=service_name)
                svc.delete_file(path)
                return f"Deleted: {path}"

            elif action == "mkdir":
                svc.mkdir(path)
                return f"Created directory: {path}"

            elif action == "stat":
                from dataclasses import asdict
                entry = svc.stat(path)
                return json.dumps(asdict(entry), default=str, indent=2)

            elif action == "exists":
                exists = svc.exists(path)
                return f"{'Exists' if exists else 'Does not exist'}: {path}"

            elif action == "search":
                pattern = arguments.get("pattern", "*")
                recursive = arguments.get("recursive", True)
                results = svc.search(path, pattern, recursive)
                return "\n".join(results) if results else "(no matches)"

            elif action == "grep":
                regex = arguments.get("regex", "")
                recursive = arguments.get("recursive", True)
                _glimit = int(arguments.get("limit", 50) or 50)
                results = svc.grep(path, regex, recursive)
                lines = [f"{r['path']}:{r['line_number']}: {r['line']}" for r in results[:_glimit]]
                total = len(results)
                if total > _glimit:
                    lines.append(f"... and {total - _glimit} more matches (use limit to see more)")
                return "\n".join(lines) if lines else "(no matches)"

            elif action == "find_replace":
                self._checkpoint_before(svc, path, service_name=service_name)
                pattern = arguments.get("pattern", "")
                replacement = arguments.get("replacement", "")
                result = svc.find_replace(path, pattern, replacement)
                return f"Replaced {result.get('replacements', 0)} occurrences in {result.get('path', path)}"

            elif action == "edit":
                self._checkpoint_before(svc, path, service_name=service_name)
                old_string = arguments.get("old_string", "")
                new_string = arguments.get("new_string", "")
                replace_all = arguments.get("replace_all", False)
                _start_ln = int(arguments.get("start_line", 0) or 0)
                _end_ln = int(arguments.get("end_line", 0) or 0)

                if _start_ln > 0 and _end_ln > 0:
                    # Line-based edit: pass start_line/end_line to relay
                    result = svc._request("edit", path,
                                          start_line=_start_ln, end_line=_end_ln,
                                          new_string=new_string)
                    return (f"Edited {result.get('path', path)}: "
                            f"replaced lines {_start_ln}-{_end_ln} "
                            f"({result.get('lines_removed', 0)} removed, "
                            f"{result.get('lines_inserted', 0)} inserted)")
                else:
                    result = svc.edit(path, old_string, new_string, replace_all)
                    # Format diff for display
                    diff = result.get("diff", [])
                    if diff:
                        diff_text = f"Edited {result.get('path', path)} (line {result.get('line', '?')}), " \
                                    f"{result.get('replacements', 0)} replacement(s):\n"
                        for d in diff:
                            prefix = "- " if d["type"] == "remove" else "+ " if d["type"] == "add" else "  "
                            diff_text += f"{d['line']:4d} {prefix}{d['text']}\n"
                        return diff_text
                    return f"Edited {result.get('path', path)}: {result.get('replacements', 0)} replacement(s)"

            elif action == "exec":
                command = arguments.get("command", "")
                timeout = arguments.get("timeout", 30)
                _max_out = min(int(arguments.get("max_output", 4000) or 4000), 4096)
                result = svc.exec(path, command, timeout)
                output = result.get("stdout", "")
                if result.get("stderr"):
                    output += "\nSTDERR:\n" + result["stderr"]
                if result.get("returncode", 0) != 0:
                    output += f"\n(exit code: {result['returncode']})"
                if not output:
                    return "(no output)"
                if len(output) > _max_out:
                    output = output[:_max_out] + f"\n\n... [{len(output) - _max_out} chars truncated — use max_output to see more]"
                return output

            # Git operations
            elif action == "git_status":
                result = svc.git_status(path)
                return json.dumps(result, indent=2)

            elif action == "git_log":
                count = int(arguments.get("count", 0) or arguments.get("limit", 10) or 10)
                result = svc.git_log(path, count)
                lines = [f"{e['hash'][:8]} {e['date']} {e['message']}" for e in result]
                return "\n".join(lines) if lines else "(no commits)"

            elif action == "git_diff":
                ref = arguments.get("ref", "")
                _max_out = int(arguments.get("max_output", 8000) or 8000)
                diff = svc.git_diff(path, ref) or "(no changes)"
                if len(diff) > _max_out:
                    diff = diff[:_max_out] + f"\n\n... [{len(diff) - _max_out} chars truncated]"
                return diff

            elif action == "git_commit":
                message = arguments.get("message", "")
                files = arguments.get("files", [])
                amend = arguments.get("amend", False)
                result = svc.git_commit(path, message, files=files, amend=amend)
                return f"Committed: {result.get('hash', '')[:8]} — {result.get('message', '')}"

            elif action == "git_pull":
                result = svc.git_pull(path)
                return json.dumps(result, indent=2)

            elif action == "git_push":
                result = svc.git_push(path)
                return json.dumps(result, indent=2)

            elif action == "git_checkout":
                ref = arguments.get("ref", "")
                result = svc.git_checkout(path, ref)
                return f"Checked out: {result.get('branch', ref)}"

            elif action == "git_worktree_list":
                result = svc.git_worktree_list(path)
                if not result:
                    return "(no worktrees)"
                lines = []
                for wt in result:
                    branch = wt.get("branch", "detached")
                    lines.append(f"{wt['path']} [{branch}] HEAD={wt.get('head', '?')[:8]}")
                return "\n".join(lines)

            elif action == "git_worktree_add":
                branch = arguments.get("branch", "")
                worktree_path = arguments.get("worktree_path", "")
                create_new = arguments.get("create_new_branch", False)
                result = svc.git_worktree_add(path, branch, worktree_path, create_new)
                return f"Worktree created: {result.get('worktree_path', '')} (branch: {result.get('branch', '')})"

            elif action == "git_worktree_remove":
                worktree_path = arguments.get("worktree_path", "")
                result = svc.git_worktree_remove(path, worktree_path)
                return f"Worktree removed: {result.get('removed', '')}"

            elif action == "git_add":
                files = arguments.get("files", [])
                result = svc._request("git_add", path, files=files)
                return f"Staged: {', '.join(result.get('staged', []))}"

            elif action == "git_reset":
                files = arguments.get("files", [])
                ref = arguments.get("ref", "")
                mode = arguments.get("mode", "mixed")
                result = svc._request("git_reset", path, files=files, ref=ref, mode=mode)
                return result.get("output", "Reset done")

            elif action == "git_stash":
                operation = arguments.get("operation", "push")
                message = arguments.get("message", "")
                index = arguments.get("index", 0)
                result = svc._request("git_stash", path, operation=operation, message=message, index=index)
                output = result.get("output", "") if isinstance(result, dict) else str(result)
                if operation == "list":
                    return output or "(no stashes)"
                return output or f"Stash {operation} done"

            elif action == "git_branch":
                operation = arguments.get("operation", "list")
                branch = arguments.get("branch", "")
                base = arguments.get("base", "")
                force = arguments.get("force", False)
                if operation == "list":
                    result = svc._request("git_branch", path, operation=operation)
                    if isinstance(result, list):
                        lines = [f"{b['name']} {b.get('hash','')} {b.get('upstream','')}" for b in result]
                        return "\n".join(lines) if lines else "(no branches)"
                    return str(result)
                result = svc._request("git_branch", path, operation=operation, branch=branch, base=base, force=force)
                return result.get("output", f"Branch {operation} done")

            elif action == "git_merge":
                branch = arguments.get("branch", "")
                no_ff = arguments.get("no_ff", False)
                result = svc._request("git_merge", path, branch=branch, no_ff=no_ff)
                prefix = "CONFLICT: " if result.get("conflict") else ""
                return prefix + result.get("output", "Merge done")

            elif action == "git_rebase":
                onto = arguments.get("onto", "")
                operation = arguments.get("operation", "start")
                result = svc._request("git_rebase", path, onto=onto, operation=operation)
                prefix = "CONFLICT: " if result.get("conflict") else ""
                return prefix + result.get("output", f"Rebase {operation} done")

            elif action == "git_cherry_pick":
                commits = arguments.get("commits", [])
                result = svc._request("git_cherry_pick", path, commits=commits)
                prefix = "CONFLICT: " if result.get("conflict") else ""
                return prefix + result.get("output", "Cherry-pick done")

            elif action == "git_tag":
                operation = arguments.get("operation", "list")
                tag = arguments.get("tag", "")
                message = arguments.get("message", "")
                if operation == "list":
                    result = svc._request("git_tag", path, operation=operation)
                    if isinstance(result, list):
                        lines = [f"{t['name']} {t.get('hash','')}" for t in result]
                        return "\n".join(lines) if lines else "(no tags)"
                    return str(result)
                result = svc._request("git_tag", path, operation=operation, tag=tag, message=message)
                return result.get("output", f"Tag {operation} done")

            elif action == "git_blame":
                file = arguments.get("file", "") or path
                start_line = arguments.get("start_line", 0)
                end_line = arguments.get("end_line", 0)
                result = svc._request("git_blame", path, file=file, start_line=start_line, end_line=end_line)
                if isinstance(result, list):
                    lines = [f"{e.get('hash','?')} {e.get('author','?'):20s} L{e.get('line','?')}: {e.get('content','')}" for e in result[:50]]
                    total = len(result)
                    if total > 50:
                        lines.append(f"... and {total - 50} more lines")
                    return "\n".join(lines) if lines else "(no blame data)"
                return str(result)

            elif action == "project_init":
                force = arguments.get("force", False)
                result = svc._request("project_init", path, force=force)
                return f"Generated {result.get('path', '.pawflow.md')} ({result.get('size', 0)} bytes)"

            elif action == "batch_edit":
                edits = arguments.get("edits", [])
                # Checkpoint each file that will be edited
                for _be in (edits if isinstance(edits, list) else []):
                    _be_path = _be.get("path", "") if isinstance(_be, dict) else ""
                    if _be_path:
                        self._checkpoint_before(svc, _be_path,
                                                service_name=service_name)
                result = svc._request("batch_edit", ".", edits=edits)
                n = result.get("edits_applied", 0)
                files = result.get("files_modified", [])
                return f"Batch edit: {n} edits applied across {len(files)} file(s): {', '.join(files)}"

            elif action == "apply_patch":
                self._checkpoint_before(svc, path, service_name=service_name)
                patch = arguments.get("patch", "")
                result = svc._request("apply_patch", path, patch=patch)
                method = result.get("method", "?")
                if method == "git_apply":
                    return f"Patch applied (git): {result.get('stats', 'ok')}"
                files = result.get("files_modified", [])
                hunks = result.get("hunks_applied", 0)
                return f"Patch applied (manual): {hunks} hunks across {len(files)} file(s): {', '.join(files)}"

            elif action == "copy_to_store":
                data = svc.read_file(path)
                fname = path.rsplit("/", 1)[-1] if "/" in path else path
                import mimetypes as _mt_copy
                mime = _mt_copy.guess_type(fname)[0] or "application/octet-stream"
                from core.file_store import FileStore
                fid = FileStore.instance().store(fname, data, mime, user_id=self._user_id)
                return f"Stored '{fname}' ({len(data):,} bytes) in FileStore\nFile ID: {fid}\nURL: /files/{fid}/{fname}"

            elif action == "copy_between":
                source_service = arguments.get("source_service", "")
                source_path = arguments.get("source_path", "") or path
                dest_service = arguments.get("dest_service", "")
                dest_path = arguments.get("dest_path", "")
                if not source_service or not dest_service:
                    return "Error: copy_between requires 'source_service' and 'dest_service'"
                if not dest_path:
                    return "Error: copy_between requires 'dest_path'"
                from core.file_store import FileStore
                import mimetypes as _mt_cb
                _store = FileStore.instance()
                # Read from source (FileStore or filesystem service)
                _fs_aliases = ("filestore", "store", "server")
                if source_service.lower() in _fs_aliases:
                    # Source is FileStore — resolve file_id or filename
                    entry = _store.get(source_path, user_id=self._user_id)
                    if not entry:
                        fid = _store.find_by_name(source_path, user_id=self._user_id)
                        if fid:
                            entry = _store.get(fid, user_id=self._user_id)
                    if not entry:
                        return f"Error: file '{source_path}' not found in FileStore"
                    fname, data, _ct = entry
                else:
                    src_svc = self._find_service(source_service)
                    if not src_svc:
                        return f"Error: source service '{source_service}' not found"
                    data = src_svc.read_file(source_path)
                    fname = source_path.rsplit("/", 1)[-1] if "/" in source_path else source_path
                # Write to dest (FileStore or filesystem service)
                if dest_service.lower() in _fs_aliases:
                    mime = _mt_cb.guess_type(fname)[0] or "application/octet-stream"
                    fid = _store.store(fname, data, mime, user_id=self._user_id)
                    return f"Copied '{fname}' ({len(data):,} bytes) from {source_service} to FileStore\nFile ID: {fid}\nURL: /files/{fid}/{fname}"
                else:
                    dst_svc = self._find_service(dest_service)
                    if not dst_svc:
                        return f"Error: destination service '{dest_service}' not found"
                    dst_svc.write_file(dest_path, data)
                    return f"Copied '{fname}' ({len(data):,} bytes) from {source_service}:{source_path} to {dest_service}:{dest_path}"

            elif action == "list_store":
                from core.file_store import FileStore
                store = FileStore.instance()
                files = store.list_files(user_id=self._user_id)
                if not files:
                    return "(no files in store)"
                lines = []
                for f in files:
                    fid = f.get("file_id", "?")
                    fname = f.get("filename", "?")
                    size = f.get("size", 0)
                    lines.append(f"{fid}  {fname}  ({size:,} bytes)")
                return "\n".join(lines)

            elif action == "delete_from_store":
                file_id = arguments.get("file_id", "") or path
                if not file_id:
                    return "Error: delete_from_store requires 'file_id' parameter"
                # Extract file_id from URL if needed
                import re as _re_del
                url_match = _re_del.search(r'/files/([^/]+)/', file_id)
                if url_match:
                    file_id = url_match.group(1)
                from core.file_store import FileStore
                deleted = FileStore.instance().delete(file_id, user_id=self._user_id)
                if deleted:
                    return f"Deleted file {file_id} from store"
                return f"Error: file {file_id} not found or access denied"

            else:
                return f"Unknown action: {action}"

        except PermissionError as e:
            return f"Permission denied: {e}"
        except FileNotFoundError as e:
            return f"Not found: {e}"
        except Exception as e:
            return f"Error: {e}"

    def set_fs_service(self, service):
        """Inject the filesystem service (called by agent_loop)."""
        self._fs_service = service
