"""see — View an image, video (frames), or audio (transcription) file.

Unlike read() which returns text content, see() injects the media
as multimodal content so the LLM can actually perceive it.

Returns a special marker that the agent loop detects and converts
to multimodal message content (image_url, etc.).
"""

import logging
import os
import tempfile
from pathlib import Path
from typing import Any, Dict

from core.handlers._fs_base import BaseFsHandler

logger = logging.getLogger(__name__)

_IMG_EXTS = frozenset({"png", "jpg", "jpeg", "gif", "webp", "svg", "bmp", "ico"})
_VID_EXTS = frozenset({"mp4", "avi", "mov", "mkv", "webm", "flv"})
_AUD_EXTS = frozenset({"mp3", "wav", "ogg", "flac", "m4a", "aac", "wma"})


class SeeHandler(BaseFsHandler):

    def __init__(self):
        super().__init__()
        # Audio transcriptions can be long; image/video frames are emitted
        # as __image_data__: markers, exempted from the cap via _returns_images.
        self._tool_result_max_chars = 500_000
        self._returns_images = True

    @property
    def name(self):
        return "see"

    @property
    def description(self):
        return (
            "Analyze an image, video, or audio file — the content is sent to YOU (the LLM) for analysis. "
            "Images: you see them. Videos: key frames extracted. Audio: transcribed to text. "
            "Use this when YOU need to see/understand the file. "
            "To show a file to the USER in their chat viewer, use 'show_file' instead."
        )

    @property
    def parameters_schema(self):
        return {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "File path to view. Use 'screen' or 'screenshot' to capture the screen."},
                "source": {"type": "string", "description": "Filesystem service name. Omit for default."},
                "max_frames": {"type": "integer", "description": "Max frames to extract from video (default: 5)"},
                "local": {"type": "boolean", "description": "If true, capture the user's REAL desktop (relay → host helper). If false (default), capture the Docker virtual desktop (relay's Xvfb / container)."},
            },
            "required": ["path"],
        }

    def execute(self, arguments: Dict[str, Any]) -> str:
        arguments = self._unwrap_json(arguments)

        path = arguments.get("path", "")
        if not path:
            return "Error: 'path' is required"

        # Screen capture shortcut: see(path="screen", local=true)
        if path.lower() in ("screen", "screenshot"):
            return self._see_screen(arguments)

        source = arguments.get("source", "")

        _svc_name, path = self._parse_fs_url(path)
        if _svc_name:
            source = _svc_name

        fname = path.replace("\\", "/").rsplit("/", 1)[-1]
        ext = (fname.rsplit(".", 1)[-1] if "." in fname else "").lower()

        # Resolve or stage the source as a disk path. Arbitrary media files are
        # never materialized as one bytes object on the PawFlow server.
        svc, workdir = self._resolve(source)
        try:
            with tempfile.TemporaryDirectory(
                    prefix="pawflow-see-") as temporary:
                if svc == "filestore":
                    source_path = self._filestore_path(path)
                elif workdir:
                    source_path = Path(self._sandbox_path(path, workdir))
                elif svc:
                    source_path = Path(temporary) / f"source.{ext or 'bin'}"
                    svc.copy_file_to_local(
                        path, str(source_path),
                        local=bool(arguments.get("local", False)))
                else:
                    return self._no_target_error(source)

                size = source_path.stat().st_size
                if size == 0:
                    return f"Error: '{path}' is empty"
                if ext in _IMG_EXTS:
                    return self._see_image_path(fname, source_path, ext)
                if ext in _VID_EXTS:
                    max_frames = int(arguments.get("max_frames", 5) or 5)
                    return self._see_video(fname, source_path, size, max_frames)
                if ext in _AUD_EXTS:
                    return self._see_audio(fname, source_path, size)
                return (f"Error: unsupported file type '{ext}' for see. "
                        "Use read() for text files.")
        except Exception as e:
            return f"Error reading '{path}': {e}"

    def _see_screen(self, arguments: Dict[str, Any]) -> str:
        """Capture screen and return as multimodal image.

        Always routes through the relay — the PawFlow server has no display.
        local=true  → user's REAL desktop (relay → host helper)
        local=false → Docker virtual screen (relay's own Xvfb / container)
        """
        local = self._resolve_local(arguments)
        source = arguments.get("source", "")

        from core.handlers._fs_base import find_fs_service
        svc = (find_fs_service(self._user_id, source, self._conversation_id)
               if source else
               (self._fs_service or
                find_fs_service(self._user_id,
                                conversation_id=self._conversation_id)))
        if not svc:
            return "Error: no relay connected for screen capture."
        try:
            result = svc._request("screen_screenshot", ".", local=local)
        except Exception as e:
            return f"Error: screen capture failed: {e}"

        data = result.get("data", result) if isinstance(result, dict) else result
        if isinstance(data, dict) and not data.get("ok", True):
            return f"Error: {data.get('error', 'unknown error')}"

        b64_data = None
        width = height = None
        if isinstance(data, dict):
            for key in ("image", "base64", "content"):
                if isinstance(data.get(key), str):
                    b64_data = data[key]
                    break
            width = data.get("width")
            height = data.get("height")
        elif isinstance(data, str):
            b64_data = data

        if b64_data:
            try:
                import base64
                img_bytes = base64.b64decode(b64_data)
                from core.handlers._screen_guard import (
                    screen_route_key, store_screen_capture,
                )
                _url, revision = store_screen_capture(
                    img_bytes,
                    user_id=self._user_id,
                    conversation_id=self._conversation_id,
                    route_key=screen_route_key(svc, local),
                )
                rendered = self._see_image("screenshot.png", img_bytes, "png")
                revision_hint = (
                    f"Screen revision: {revision}\n"
                    "Pass this exact revision as expected_screen_revision for the next "
                    "screen click/double_click based on this image. The relay validates "
                    "the target locally without another vision call.\n"
                )
                if width and height:
                    return (
                        revision_hint
                        + f"Screen resolution: {width}x{height}. Use physical screen "
                        "pixels for screen click/move/scroll coordinates; do not "
                        "derive coordinates from the resized image rendered in chat.\n"
                        + rendered
                    )
                return revision_hint + rendered
            except Exception as e:
                return f"Error: screen capture decode failed: {e}"

        return "Error: unexpected screen capture result"

    def _see_image(self, fname: str, data: bytes, ext: str) -> str:
        """Return image as multimodal marker."""
        import base64
        import mimetypes
        mime = mimetypes.guess_type(fname)[0] or f"image/{ext}"

        # Downscale large images to the shared vision ceiling (saves context
        # tokens and keeps payloads within the provider's pixel limit).
        from core.image_resize import resize_image_for_vision
        data, mime = resize_image_for_vision(data, mime)

        b64 = base64.b64encode(data).decode("ascii")

        # Return marker — agent loop converts to multimodal content
        # see does NOT store in FileStore — it only passes data to LLM vision
        return f"Image: {fname} ({len(data):,} bytes, {mime})\n__image_data__:{mime}:{b64}"

    def _see_image_path(self, fname: str, path: Path, ext: str) -> str:
        """Return a bounded image payload decoded directly from a disk path."""
        import base64
        import mimetypes
        from core.image_resize import resize_image_path_for_vision

        mime = mimetypes.guess_type(fname)[0] or f"image/{ext}"
        data, mime = resize_image_path_for_vision(path, mime)
        encoded = base64.b64encode(data).decode("ascii")
        return (f"Image: {fname} ({len(data):,} bytes, {mime})\n"
                f"__image_data__:{mime}:{encoded}")

    def _see_video(self, fname: str, source_path: Path, size: int,
                   max_frames: int) -> str:
        """Extract key frames from video, return as image sequence."""
        import tempfile
        import subprocess  # nosec B404
        import base64

        try:
            # Get duration
            probe = subprocess.run(  # nosec B603, B607
                ["ffprobe", "-v", "quiet", "-print_format", "json",
                 "-show_format", str(source_path)],
                capture_output=True, text=True, timeout=10)
            import json
            duration = 0
            try:
                info = json.loads(probe.stdout)
                duration = float(info.get("format", {}).get("duration", 0))
            except Exception:
                logging.getLogger(__name__).debug("Ignored exception", exc_info=True)

            if duration <= 0:
                return f"Video: {fname} ({size:,} bytes) — could not determine duration"

            # Extract frames at evenly spaced intervals
            interval = max(1, duration / max_frames)
            frames = []
            with tempfile.TemporaryDirectory(
                    prefix="pawflow-see-frames-") as frame_dir:
                for i in range(min(max_frames, int(duration))):
                    ts = i * interval
                    frame_path = Path(frame_dir) / f"frame_{i}.jpg"
                    subprocess.run(  # nosec B603, B607
                        ["ffmpeg", "-ss", str(ts), "-i", str(source_path),
                         "-frames:v", "1", "-q:v", "3", str(frame_path), "-y"],
                        capture_output=True, timeout=10)
                    if frame_path.exists():
                        from core.image_resize import resize_image_path_for_vision
                        frame_data, frame_mime = resize_image_path_for_vision(
                            frame_path, "image/jpeg")
                        b64 = base64.b64encode(frame_data).decode("ascii")
                        frames.append(f"__image_data__:{frame_mime}:{b64}")

            if not frames:
                return f"Video: {fname} ({size:,} bytes, {duration:.1f}s) — ffmpeg frame extraction failed"

            result = f"Video: {fname} ({size:,} bytes, {duration:.1f}s, {len(frames)} frames extracted)\n"
            result += "\n".join(frames)
            return result

        except FileNotFoundError:
            return f"Video: {fname} ({size:,} bytes) — ffmpeg not available for frame extraction"
        except Exception as e:
            return f"Video: {fname} ({size:,} bytes) — frame extraction failed: {e}"

    def _see_audio(self, fname: str, source_path: Path, size: int) -> str:
        """Transcribe audio file."""
        import subprocess  # nosec B404

        try:
            # Try whisper CLI
            subprocess.run(  # nosec B603, B607
                ["whisper", str(source_path), "--model", "base",
                 "--output_format", "txt",
                 "--output_dir", str(source_path.parent)],
                capture_output=True, text=True, timeout=120)

            txt_path = source_path.with_suffix(".txt")
            if txt_path.exists():
                with txt_path.open("r", encoding="utf-8") as handle:
                    transcript = handle.read(500_001)
                txt_path.unlink(missing_ok=True)
                if len(transcript) > 500_000:
                    transcript = transcript[:500_000] + "\n[transcript truncated]"
                return f"Audio transcription of {fname} ({size:,} bytes):\n\n{transcript}"

            return f"Audio: {fname} ({size:,} bytes) — whisper transcription produced no output"

        except FileNotFoundError:
            return f"Audio: {fname} ({size:,} bytes) — whisper not available for transcription"
        except subprocess.TimeoutExpired:
            return f"Audio: {fname} ({size:,} bytes) — transcription timed out"
        except Exception as e:
            return f"Audio: {fname} ({size:,} bytes) — transcription failed: {e}"

    def _filestore_path(self, path: str) -> Path:
        """Resolve an authorized FileStore entry without reading its content."""
        from core.file_store import FileStore
        store = FileStore.instance()
        file_id = self._filestore_id_from_path(path)
        disk_path = store.get_disk_path(file_id, user_id=self._user_id)
        if disk_path is None:
            found = store.find_by_name(file_id, user_id=self._user_id)
            if found:
                disk_path = store.get_disk_path(
                    found, user_id=self._user_id)
        if disk_path is None:
            raise FileNotFoundError(f"'{file_id}' not found in FileStore")
        return Path(disk_path)
