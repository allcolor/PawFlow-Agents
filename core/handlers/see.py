"""see — View an image, video (frames), or audio (transcription) file.

Unlike read() which returns text content, see() injects the media
as multimodal content so the LLM can actually perceive it.

Returns a special marker that the agent loop detects and converts
to multimodal message content (image_url, etc.).
"""

import logging
import os
from typing import Any, Dict

from core.handlers._fs_base import BaseFsHandler

logger = logging.getLogger(__name__)

_IMG_EXTS = frozenset({"png", "jpg", "jpeg", "gif", "webp", "svg", "bmp", "ico"})
_VID_EXTS = frozenset({"mp4", "avi", "mov", "mkv", "webm", "flv"})
_AUD_EXTS = frozenset({"mp3", "wav", "ogg", "flac", "m4a", "aac", "wma"})


class SeeHandler(BaseFsHandler):

    def __init__(self):
        super().__init__()
        # Images/video/audio produce large base64 output;
        # tool_registry skips cap for __image_data__ markers anyway,
        # but audio transcriptions can also be long.
        self._tool_result_max_chars = 500_000

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
                "path": {"type": "string", "description": "File path to view"},
                "source": {"type": "string", "description": "Filesystem service name. Omit for default."},
                "max_frames": {"type": "integer", "description": "Max frames to extract from video (default: 5)"},
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

        _svc_name, path = self._parse_fs_url(path)
        if _svc_name:
            source = _svc_name

        fname = path.rsplit("/", 1)[-1] if "/" in path else path
        ext = (fname.rsplit(".", 1)[-1] if "." in fname else "").lower()

        # Read the file
        svc, workdir = self._resolve(source)
        try:
            if svc == "filestore":
                data = self._read_filestore_bytes(path)
            elif workdir:
                full = self._sandbox_path(path, workdir)
                with open(full, "rb") as f:
                    data = f.read()
            elif svc:
                data = svc.read_file(path)
            else:
                return self._no_target_error(source)
        except Exception as e:
            return f"Error reading '{path}': {e}"

        if not data:
            return f"Error: '{path}' is empty"

        # Image → inject as multimodal
        if ext in _IMG_EXTS:
            return self._see_image(fname, data, ext)

        # Video → extract frames
        if ext in _VID_EXTS:
            max_frames = int(arguments.get("max_frames", 5) or 5)
            return self._see_video(fname, data, max_frames)

        # Audio → transcribe
        if ext in _AUD_EXTS:
            return self._see_audio(fname, data, ext)

        return f"Error: unsupported file type '{ext}' for see. Use read() for text files."

    def _see_image(self, fname: str, data: bytes, ext: str) -> str:
        """Return image as multimodal marker."""
        import base64
        import mimetypes
        mime = mimetypes.guess_type(fname)[0] or f"image/{ext}"
        b64 = base64.b64encode(data).decode("ascii")

        # Store in FileStore for URL access
        from core.file_store import FileStore
        fid = FileStore.instance().store(fname, data, mime, user_id=self._user_id)
        url = f"/files/{fid}/{fname}"

        # Return marker — agent loop converts to multimodal content
        return f"Image: {url} ({len(data):,} bytes, {mime})\n__image_data__:{mime}:{b64}"

    def _see_video(self, fname: str, data: bytes, max_frames: int) -> str:
        """Extract key frames from video, return as image sequence."""
        import tempfile
        import subprocess
        import base64

        # Write to temp file
        tmp = tempfile.NamedTemporaryFile(suffix=f".{fname.rsplit('.', 1)[-1]}", delete=False)
        try:
            tmp.write(data)
            tmp.close()

            # Get duration
            probe = subprocess.run(
                ["ffprobe", "-v", "quiet", "-print_format", "json",
                 "-show_format", tmp.name],
                capture_output=True, text=True, timeout=10)
            import json
            duration = 0
            try:
                info = json.loads(probe.stdout)
                duration = float(info.get("format", {}).get("duration", 0))
            except Exception:
                pass

            if duration <= 0:
                return f"Video: {fname} ({len(data):,} bytes) — could not determine duration"

            # Extract frames at evenly spaced intervals
            interval = max(1, duration / max_frames)
            frames = []
            for i in range(min(max_frames, int(duration))):
                ts = i * interval
                frame_path = f"{tmp.name}_frame_{i}.jpg"
                subprocess.run(
                    ["ffmpeg", "-ss", str(ts), "-i", tmp.name,
                     "-frames:v", "1", "-q:v", "3", frame_path, "-y"],
                    capture_output=True, timeout=10)
                if os.path.exists(frame_path):
                    with open(frame_path, "rb") as f:
                        frame_data = f.read()
                    b64 = base64.b64encode(frame_data).decode("ascii")
                    frames.append(f"__image_data__:image/jpeg:{b64}")
                    os.unlink(frame_path)

            if not frames:
                return f"Video: {fname} ({len(data):,} bytes, {duration:.1f}s) — ffmpeg frame extraction failed"

            result = f"Video: {fname} ({len(data):,} bytes, {duration:.1f}s, {len(frames)} frames extracted)\n"
            result += "\n".join(frames)
            return result

        except FileNotFoundError:
            return f"Video: {fname} ({len(data):,} bytes) — ffmpeg not available for frame extraction"
        except Exception as e:
            return f"Video: {fname} ({len(data):,} bytes) — frame extraction failed: {e}"
        finally:
            try:
                os.unlink(tmp.name)
            except Exception:
                pass

    def _see_audio(self, fname: str, data: bytes, ext: str) -> str:
        """Transcribe audio file."""
        import tempfile
        import subprocess

        tmp = tempfile.NamedTemporaryFile(suffix=f".{ext}", delete=False)
        try:
            tmp.write(data)
            tmp.close()

            # Try whisper CLI
            result = subprocess.run(
                ["whisper", tmp.name, "--model", "base", "--output_format", "txt",
                 "--output_dir", os.path.dirname(tmp.name)],
                capture_output=True, text=True, timeout=120)

            txt_path = tmp.name.rsplit(".", 1)[0] + ".txt"
            if os.path.exists(txt_path):
                with open(txt_path, "r", encoding="utf-8") as f:
                    transcript = f.read()
                os.unlink(txt_path)
                return f"Audio transcription of {fname} ({len(data):,} bytes):\n\n{transcript}"

            return f"Audio: {fname} ({len(data):,} bytes) — whisper transcription produced no output"

        except FileNotFoundError:
            return f"Audio: {fname} ({len(data):,} bytes) — whisper not available for transcription"
        except subprocess.TimeoutExpired:
            return f"Audio: {fname} ({len(data):,} bytes) — transcription timed out"
        except Exception as e:
            return f"Audio: {fname} ({len(data):,} bytes) — transcription failed: {e}"
        finally:
            try:
                os.unlink(tmp.name)
            except Exception:
                pass

    def _read_filestore_bytes(self, path: str) -> bytes:
        """Read raw bytes from FileStore."""
        import re
        from core.file_store import FileStore
        store = FileStore.instance()
        _fid_match = re.search(r'/?(?:files/)?([a-f0-9]{12})(?:/|$)', path)
        file_id = _fid_match.group(1) if _fid_match else path.split("/")[0]
        entry = store.get(file_id)
        if not entry:
            found = store.find_by_name(file_id)
            if found:
                entry = store.get(found)
        if not entry:
            raise FileNotFoundError(f"'{file_id}' not found in FileStore")
        return entry[1]
