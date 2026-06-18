"""ShowFileHandler — extracted from resource_agent.py (<=800 lines).

Re-exported from core.handlers.resource_agent for import stability.
"""

import json
import logging
from typing import Any, Dict

from core.tool_handler import ToolHandler

logger = logging.getLogger(__name__)


class ShowFileHandler(ToolHandler):
    """Display a file in the chat UI viewer (images, PDFs, text, code)."""

    def __init__(self):
        self._base_url = "http://localhost:9090"
        self._user_id = ""
        self._conversation_id = ""

    def set_conversation_id(self, conversation_id: str):
        self._conversation_id = conversation_id or ""

    @property
    def name(self) -> str:
        return "show_file"

    @property
    def description(self) -> str:
        return (
            "Display a file to the USER in their chat viewer panel.\n\n"
            "This opens a file in the user's UI — it does NOT return the file content to you. "
            "Use this when the user asks to SEE something (an image, a PDF, a code file, a chart). "
            "If YOU need to analyze or read the file content yourself, use 'see' or 'read' instead.\n\n"
            "Key parameters:\n"
            "- file_id: The FileStore file ID (from execute_script output, upload results, etc.).\n"
            "- filename: Alternative to file_id — search by filename in FileStore.\n"
            "- path + service: Show a file from a filesystem service (relay). Provide both "
            "the file path and the service name.\n\n"
            "Supports images (PNG, JPG, SVG), PDFs, code files, text, and other file types "
            "that the chat UI can render. The file must exist in FileStore or on the specified "
            "filesystem service."
        )

    @property
    def parameters_schema(self) -> Dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "file_id": {
                    "type": "string",
                    "description": "FileStore file ID",
                },
                "filename": {
                    "type": "string",
                    "description": "Filename to search for in FileStore",
                },
                "path": {
                    "type": "string",
                    "description": "File path on a filesystem service (e.g. 'assets/player.png')",
                },
                "service": {
                    "type": "string",
                    "description": "Filesystem service name (e.g. 'localFS') — required when using path",
                },
            },
        }

    def set_base_url(self, url: str):
        self._base_url = url.rstrip("/")

    def set_user_id(self, user_id: str):
        self._user_id = user_id

    def _find_fs_service(self, service_name: str):
        """Find a filesystem service by name (conv > user > global)."""
        try:
            from core.service_registry import ServiceRegistry
            return ServiceRegistry.get_instance().resolve(
                service_name, user_id=self._user_id,
                conv_id=getattr(self, "_conversation_id", "") or "")
        except Exception:
            return None

    def execute(self, arguments: Dict[str, Any]) -> str:
        from core.file_store import FileStore
        import mimetypes

        store = FileStore.instance()
        file_id = arguments.get("file_id", "")
        filename = arguments.get("filename", "")
        fs_path = arguments.get("path", "")
        fs_service = arguments.get("service", "")

        if file_id:
            # Extract file_id from URL if needed
            import re as _re_sf
            url_match = _re_sf.search(r'/files/([a-f0-9]{12})', file_id)
            if url_match:
                file_id = url_match.group(1)
            result = store.get(file_id, user_id=self._user_id)
            if not result:
                # Try by name
                found_id = store.find_by_name(file_id, user_id=self._user_id)
                if found_id:
                    result = store.get(found_id, user_id=self._user_id)
                    file_id = found_id
            if not result:
                return f"Error: File ID '{file_id}' not found."
            fname, data, content_type = result
        elif fs_path:
            # Read from filesystem service, cache in FileStore
            svc = self._find_fs_service(fs_service) if fs_service else None
            if not svc:
                return f"Error: Filesystem service '{fs_service}' not found or not connected."
            try:
                data = svc.read_file(fs_path)
            except Exception as e:
                return f"Error reading '{fs_path}' from {fs_service}: {e}"
            fname = fs_path.rsplit("/", 1)[-1] if "/" in fs_path else fs_path
            content_type = mimetypes.guess_type(fname)[0] or "application/octet-stream"
            # Store in FileStore for the viewer URL
            file_id = store.store(fname, data, content_type=content_type,
                                  user_id=self._user_id,
                                  conversation_id=getattr(self, '_conversation_id', '') or '')
        elif filename:
            # Search by filename in FileStore
            found = None
            for f in store.list_files(user_id=self._user_id):
                if f["filename"] == filename:
                    found = f
                    break
            if not found:
                # Fuzzy search
                found_id = store.find_by_name(filename, user_id=self._user_id)
                if found_id:
                    found = {"file_id": found_id, "filename": filename}
            if not found:
                return (f"Error: File '{filename}' not found in FileStore. "
                        f"Use path+service to show files from a filesystem service.")
            file_id = found["file_id"]
            fname = found["filename"]
            result = store.get(file_id, user_id=self._user_id)
            if not result:
                return f"Error: Could not load file '{filename}'."
            fname, data, content_type = result
        else:
            return "Error: Provide file_id, filename, or path+service."

        url = f"fs://filestore/{file_id}/{fname}"
        size_kb = len(data) / 1024

        # Return a special marker that the chat UI will intercept
        return json.dumps({
            "__show_file__": True,
            "url": url,
            "filename": fname,
            "content_type": content_type,
            "size_kb": round(size_kb, 1),
            "file_id": file_id,
        })
