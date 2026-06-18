"""Shared filesystem actions for HTTP and WS relays.

All actions take (root_dir, abs_path, req) and return a dict result.
The relay is responsible for path resolution and access control.

The action implementations are split across sibling modules to keep each
file <= 800 lines; they are re-exported here so ``fs_actions.ACTIONS`` and the
public ``action_*`` / helper names stay importable from this module:
  _fs_paths  - path-resolution + glob helpers
  _fs_read   - list/read/pdf/notebook/stat/exists/search/write/chunked/init
  _fs_grep   - grep + glob
  _fs_edit   - find_replace / edit / batch_edit / apply_patch
  fs_exec / fs_screen / fs_mcp / fs_http - exec, screen, MCP, http actions
"""
import os
import sys as _sys

# Ensure tools/ is on sys.path so bare imports work from project root (tests)
_tools_dir = os.path.dirname(os.path.abspath(__file__))
if _tools_dir not in _sys.path:
    _sys.path.insert(0, _tools_dir)

# Shared utilities (extracted to break circular import with fs_exec)
from fs_common import (  # noqa: E402,F401
    MAX_EXEC_OUTPUT, _docker_cmd, _translate_path, _to_host_path,
    detect_available_shells, _resolve_shell,
)
from _fs_paths import (  # noqa: E402,F401
    MAX_FILE_SIZE,
    _expand_glob_braces,
    _is_host_absolute_path,
    _is_windows_drive_absolute_path,
    _rel,
    _resolve_tool_path,
)
from _fs_read import (  # noqa: E402,F401
    action_delete_file, action_edit_notebook, action_exists,
    action_list_dir, action_mkdir, action_project_context,
    action_project_init, action_read_chunk, action_read_file,
    action_read_file_chunked, action_read_notebook, action_read_pdf,
    action_search, action_stat, action_write_file,
    action_write_file_chunked,
)
from _fs_grep import action_grep  # noqa: E402,F401
from _fs_edit import (  # noqa: E402,F401
    _diagnose_edit_mismatch,
    action_apply_patch, action_batch_edit, action_edit, action_find_replace,
)
from fs_exec import action_exec, action_exec_stream  # noqa: E402,F401
from fs_screen import (  # noqa: E402,F401
    action_screen_screenshot, action_screen_click, action_screen_double_click,
    action_screen_triple_click, action_screen_right_click,
    action_screen_type, action_screen_key, action_screen_move,
    action_screen_scroll, action_screen_mouse_position, action_screen_drag,
    action_screen_screenshot_region, action_screen_size, action_screen_wait,
    action_screen_open_app,
    action_screen_clipboard_read, action_screen_clipboard_write,
    action_screen_window_list, action_screen_window_focus,
    action_screen_window_close, action_screen_window_resize,
    action_screen_window_minimize, action_screen_window_maximize,
    action_screen_ocr, action_screen_locate,
)
from fs_mcp import (  # noqa: E402,F401
    action_mcp_start, action_mcp_discover, action_mcp_call,
    action_mcp_stop, action_mcp_list,
)
from fs_http import action_http_fetch  # noqa: E402,F401

# Actions that require write access
WRITE_ACTIONS = frozenset({
    "write_file", "delete_file", "mkdir", "find_replace", "edit",
    "batch_edit", "apply_patch",
    "exec", "exec_stream",
    "edit_notebook",
    "project_init",
    "screen_click", "screen_double_click", "screen_type",
    "screen_key", "screen_move", "screen_scroll",
})

ACTIONS = {
    "list_dir": action_list_dir,
    "project_context": action_project_context,
    "read_file": action_read_file,
    "read_pdf": action_read_pdf,
    "read_notebook": action_read_notebook,
    "write_file": action_write_file,
    "delete_file": action_delete_file,
    "mkdir": action_mkdir,
    "stat": action_stat,
    "exists": action_exists,
    "search": action_search,
    "grep": action_grep,
    "find_replace": action_find_replace,
    "edit": action_edit,
    "batch_edit": action_batch_edit,
    "apply_patch": action_apply_patch,
    "exec": action_exec,
    "exec_stream": action_exec_stream,
    "http_fetch": action_http_fetch,
    "read_file_chunked": action_read_file_chunked,
    "read_chunk": action_read_chunk,
    "write_file_chunked": action_write_file_chunked,
    "project_init": action_project_init,
    "edit_notebook": action_edit_notebook,
    "screen_screenshot": action_screen_screenshot,
    "screen_screenshot_region": action_screen_screenshot_region,
    "screen_click": action_screen_click,
    "screen_double_click": action_screen_double_click,
    "screen_triple_click": action_screen_triple_click,
    "screen_right_click": action_screen_right_click,
    "screen_type": action_screen_type,
    "screen_key": action_screen_key,
    "screen_move": action_screen_move,
    "screen_drag": action_screen_drag,
    "screen_scroll": action_screen_scroll,
    "screen_cursor_position": action_screen_mouse_position,
    "screen_size": action_screen_size,
    "screen_wait": action_screen_wait,
    "screen_open_app": action_screen_open_app,
    "screen_clipboard_read": action_screen_clipboard_read,
    "screen_clipboard_write": action_screen_clipboard_write,
    "screen_window_list": action_screen_window_list,
    "screen_window_focus": action_screen_window_focus,
    "screen_window_close": action_screen_window_close,
    "screen_window_resize": action_screen_window_resize,
    "screen_window_minimize": action_screen_window_minimize,
    "screen_window_maximize": action_screen_window_maximize,
    "screen_ocr": action_screen_ocr,
    "screen_locate": action_screen_locate,
    "mcp_start": action_mcp_start,
    "mcp_call": action_mcp_call,
    "mcp_discover": action_mcp_discover,
    "mcp_stop": action_mcp_stop,
    "mcp_list": action_mcp_list,
}
