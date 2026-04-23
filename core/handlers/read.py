"""read — Read a file (text, PDF, notebook, image). Supports pagination."""

import json
import logging
from typing import Any, Dict

from core.handlers._fs_base import BaseFsHandler, cap_binary_output

logger = logging.getLogger(__name__)


class ReadHandler(BaseFsHandler):

    def __init__(self):
        super().__init__()
        self._returns_images = True

    @property
    def name(self):
        return "read"

    @property
    def description(self):
        return (
            "Reads a file from the filesystem and returns its contents.\n\n"
            "Usage:\n"
            " - The path parameter must be provided. Use the source parameter to specify "
            "a non-default filesystem service or relay.\n"
            " - By default, reads up to 2000 lines starting from the beginning of the file.\n"
            " - When you already know which part of the file you need, use offset/limit to "
            "read only that part. This is important for large files.\n"
            " - Results are returned in cat -n format, with line numbers starting at 1.\n\n"
            "Supported file types:\n"
            " - Text files: returned with line numbers and pagination (offset/limit).\n"
            " - PDF files: use the pages parameter (e.g. pages='1-5'). For large PDFs "
            "(more than 10 pages), you MUST provide pages to read specific ranges.\n"
            " - Jupyter notebooks (.ipynb): returns all cells with their outputs.\n"
            " - Images (PNG, JPG, etc.): returns metadata; use see() for visual inspection.\n\n"
            "Important:\n"
            " - This tool can only read files, not directories. To list a directory, use "
            "bash with ls or use glob.\n"
            " - Do NOT re-read a file you just edited to verify — edit would have errored "
            "if the change failed."
        )

    @property
    def parameters_schema(self):
        return {
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "File path to read",
                },
                "offset": {
                    "type": "integer",
                    "description": "Start line (1-based) for text files. Use with limit for pagination.",
                },
                "limit": {
                    "type": "integer",
                    "description": "Max lines to read (default: all, paginated if too large).",
                },
                "pages": {
                    "type": "string",
                    "description": "Page range for PDF files (e.g. '1-5').",
                },
                "source": {
                    "type": "string",
                    "description": "Filesystem service name. Omit for default.",
                },
            },
            "required": ["path"],
        }

    def execute(self, arguments: Dict[str, Any]) -> str:
        arguments = self._unwrap_json(arguments)
        arguments = self._resolve_expressions(arguments)
        path = arguments.get("path", "")
        if not path:
            return "Error: 'path' is required"
        source = arguments.get("source", "")

        # fs:// URL parsing
        _svc_name, path = self._parse_fs_url(path)
        if _svc_name:
            source = _svc_name

        svc, workdir = self._resolve(source)

        offset = int(arguments.get("offset", 0) or arguments.get("start_line", 0) or 0)
        limit = int(arguments.get("limit", 0) or 0)
        # Support start_line/end_line as aliases for offset/limit
        if not limit and arguments.get("end_line"):
            end_line = int(arguments["end_line"])
            if offset > 0 and end_line >= offset:
                limit = end_line - offset + 1
        # Support Claude Code 'ranges' format: "350-420" or "350-420,500-550"
        if not offset and not limit and arguments.get("ranges"):
            try:
                _range = str(arguments["ranges"]).split(",")[0].strip()
                _parts = _range.split("-")
                offset = int(_parts[0])
                if len(_parts) > 1 and _parts[1]:
                    limit = int(_parts[1]) - offset + 1
            except (ValueError, IndexError):
                pass

        # FileStore
        if svc == "filestore":
            return self._filestore_read(path, offset, limit)

        # Workdir
        if workdir:
            return self._workdir_read(path, offset, limit)

        # No target
        if svc is None:
            return self._no_target_error(source)

        # Service — delegate
        try:
            data = svc.read_file(path)
        except Exception as e:
            return f"Error reading '{path}': {e}"

        # Track for Read-before-Edit enforcement.
        from core.handlers._edit_guard import track_read
        track_read(self._user_id, self._conversation_id,
                   self._agent_name, path,
                   data if isinstance(data, (bytes, bytearray)) else b"")

        fname = path.rsplit("/", 1)[-1] if "/" in path else path

        # Images — return metadata + hint to use see() for visual inspection
        _img_exts = (".png", ".jpg", ".jpeg", ".gif", ".webp", ".svg", ".bmp")
        if any(fname.lower().endswith(ext) for ext in _img_exts):
            import mimetypes
            mime = mimetypes.guess_type(fname)[0] or "image/png"
            return (f"Image file: {fname} ({len(data):,} bytes, {mime})\n"
                    f"[To visually inspect this image, use see(path=\"{path}\") instead of read]")

        # Video/audio — hint to use see()
        _vid_exts = (".mp4", ".avi", ".mov", ".mkv", ".webm", ".flv")
        _aud_exts = (".mp3", ".wav", ".ogg", ".flac", ".m4a", ".aac")
        if any(fname.lower().endswith(ext) for ext in _vid_exts):
            return (f"Video file: {fname} ({len(data):,} bytes)\n"
                    f"[To view frames from this video, use see(path=\"{path}\") instead of read]")
        if any(fname.lower().endswith(ext) for ext in _aud_exts):
            return (f"Audio file: {fname} ({len(data):,} bytes)\n"
                    f"[To transcribe this audio, use see(path=\"{path}\") instead of read]")

        # PDF auto-redirect
        if fname.lower().endswith(".pdf"):
            max_pages = int(arguments.get("pages", "50").split("-")[-1]) if arguments.get("pages") else 50
            result = svc._request("read_pdf", path, max_pages=max_pages)
            if isinstance(result, dict) and "pages" in result:
                lines = [f"PDF: {result.get('total_pages', '?')} pages"]
                for p_data in result["pages"]:
                    lines.append(f"\n--- Page {p_data['page']} ---\n{p_data['text']}")
                return "\n".join(lines)
            return json.dumps(result)

        # Notebook auto-redirect
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

        # Text file
        try:
            text = data.decode("utf-8")
        except UnicodeDecodeError:
            return f"(binary file, {len(data)} bytes)"

        return self._format_text_read(fname, text, offset, limit)
