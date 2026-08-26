"""Closed FFmpeg recipe contract and shell-free argument compiler."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import PurePath
from typing import Any, Mapping, Sequence

from core.media_studio import new_contract_id, utc_now

SCHEMA_VERSION = "1.0"
OPERATIONS = frozenset({
    "probe", "trim", "concat", "resize", "crop", "pad", "transcode",
    "change_fps", "extract_frame", "extract_audio", "replace_audio",
    "mix_audio", "duck_audio", "normalize_loudness", "fade", "crossfade",
    "overlay_image", "overlay_text", "burn_subtitles",
    "loop_image_with_audio",
})
_FILE_ID = re.compile(r"^[a-f0-9]{12}$")
_SAFE_FILENAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
_SAFE_COLOR = re.compile(r"^(?:#[0-9A-Fa-f]{6}|[A-Za-z]{1,24})$")
_VIDEO_CODECS = frozenset({"copy", "libx264", "libx265", "libvpx-vp9", "prores_ks"})
_AUDIO_CODECS = frozenset({"copy", "aac", "libopus", "libmp3lame", "pcm_s16le", "flac"})
_FORMATS = frozenset({
    "mp4", "mov", "mkv", "webm", "mp3", "wav", "flac", "ogg", "png",
    "jpg", "webp", "json",
})
_PARAM_KEYS = {
    "probe": frozenset(),
    "trim": frozenset({"start", "duration"}),
    "concat": frozenset({"has_audio"}),
    "resize": frozenset({"width", "height"}),
    "crop": frozenset({"width", "height", "x", "y"}),
    "pad": frozenset({"width", "height", "x", "y", "color"}),
    "transcode": frozenset({"video_codec", "audio_codec", "format", "crf"}),
    "change_fps": frozenset({"fps"}),
    "extract_frame": frozenset({"time"}),
    "extract_audio": frozenset({"audio_codec"}),
    "replace_audio": frozenset({"audio_codec"}),
    "mix_audio": frozenset({"duration"}),
    "duck_audio": frozenset({"threshold", "ratio", "attack", "release"}),
    "normalize_loudness": frozenset({"integrated", "true_peak", "lra"}),
    "fade": frozenset({"media", "direction", "start", "duration"}),
    "crossfade": frozenset({"duration", "offset"}),
    "overlay_image": frozenset({"x", "y"}),
    "overlay_text": frozenset({"text", "x", "y", "font_size", "font_color"}),
    "burn_subtitles": frozenset(),
    "loop_image_with_audio": frozenset({"width", "height", "fps"}),
}
_INPUT_COUNTS = {
    "probe": (1, 1), "trim": (1, 1), "concat": (2, 32),
    "resize": (1, 1), "crop": (1, 1), "pad": (1, 1),
    "transcode": (1, 1), "change_fps": (1, 1),
    "extract_frame": (1, 1), "extract_audio": (1, 1),
    "replace_audio": (2, 2), "mix_audio": (2, 16),
    "duck_audio": (2, 2), "normalize_loudness": (1, 1),
    "fade": (1, 1), "crossfade": (2, 2),
    "overlay_image": (2, 2), "overlay_text": (1, 1),
    "burn_subtitles": (2, 2), "loop_image_with_audio": (2, 2),
}


def _number(value: Any, name: str, minimum: float, maximum: float) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{name} must be numeric")
    result = float(value)
    if not minimum <= result <= maximum:
        raise ValueError(f"{name} must be between {minimum:g} and {maximum:g}")
    return result


def _integer(value: Any, name: str, minimum: int, maximum: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"{name} must be an integer")
    if not minimum <= value <= maximum:
        raise ValueError(f"{name} must be between {minimum} and {maximum}")
    return value


def _file_id(value: str) -> str:
    raw = str(value or "").strip()
    if raw.startswith("fs://filestore/"):
        raw = raw[len("fs://filestore/"):].split("/", 1)[0]
    if not _FILE_ID.fullmatch(raw):
        raise ValueError("inputs must be owner-authorized FileStore IDs")
    return raw


def _filename(value: str) -> str:
    raw = str(value or "").strip()
    if (not _SAFE_FILENAME.fullmatch(raw) or PurePath(raw).name != raw
            or raw in {".", ".."}):
        raise ValueError("output_filename must be a safe basename")
    suffix = raw.rsplit(".", 1)[-1].lower() if "." in raw else ""
    if suffix not in _FORMATS:
        raise ValueError(f"unsupported output format: {suffix or '<missing>'}")
    return raw


def _escape_filter(value: str) -> str:
    return (value.replace("\\", "\\\\").replace(":", "\\:")
            .replace("'", "\\'").replace(",", "\\,")
            .replace("[", "\\[").replace("]", "\\]"))


@dataclass(frozen=True)
class FFmpegRecipe:
    """Validated recipe whose fields map only to a closed operation catalog."""

    operation: str
    inputs: tuple[str, ...]
    output_filename: str
    parameters: Mapping[str, Any] = field(default_factory=dict)
    recipe_id: str = field(default_factory=lambda: new_contract_id("ffmpeg_recipe"))
    created_at: str = field(default_factory=utc_now)
    schema_version: str = SCHEMA_VERSION

    def __post_init__(self) -> None:
        if self.schema_version != SCHEMA_VERSION:
            raise ValueError(f"schema_version must be {SCHEMA_VERSION}")
        if self.operation not in OPERATIONS:
            raise ValueError(f"unsupported FFmpeg operation: {self.operation}")
        normalized_inputs = tuple(_file_id(value) for value in self.inputs)
        minimum, maximum = _INPUT_COUNTS[self.operation]
        if not minimum <= len(normalized_inputs) <= maximum:
            raise ValueError(
                f"{self.operation} requires between {minimum} and {maximum} inputs")
        params = dict(self.parameters)
        unknown = sorted(set(params) - _PARAM_KEYS[self.operation])
        if unknown:
            raise ValueError(
                f"unsupported parameters for {self.operation}: {', '.join(unknown)}")
        object.__setattr__(self, "inputs", normalized_inputs)
        object.__setattr__(self, "output_filename", _filename(self.output_filename))
        object.__setattr__(self, "parameters", _validate_parameters(self.operation, params))

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "FFmpegRecipe":
        if not isinstance(value, Mapping):
            raise ValueError("recipe must be an object")
        allowed = {
            "operation", "inputs", "output_filename", "parameters",
            "recipe_id", "created_at", "schema_version",
        }
        unknown = sorted(set(value) - allowed)
        if unknown:
            raise ValueError(f"unsupported recipe fields: {', '.join(unknown)}")
        return cls(
            operation=str(value.get("operation") or ""),
            inputs=tuple(value.get("inputs") or ()),
            output_filename=str(value.get("output_filename") or ""),
            parameters=dict(value.get("parameters") or {}),
            recipe_id=str(value.get("recipe_id") or new_contract_id("ffmpeg_recipe")),
            created_at=str(value.get("created_at") or utc_now()),
            schema_version=str(value.get("schema_version") or SCHEMA_VERSION),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "recipe_id": self.recipe_id,
            "created_at": self.created_at,
            "operation": self.operation,
            "inputs": list(self.inputs),
            "output_filename": self.output_filename,
            "parameters": dict(self.parameters),
        }


def _validate_parameters(operation: str, params: dict[str, Any]) -> dict[str, Any]:
    result = dict(params)
    if operation in {"trim", "fade"}:
        result["start"] = _number(result.get("start", 0), "start", 0, 86400)
        result["duration"] = _number(result.get("duration", 1), "duration", 0.01, 86400)
    if operation in {"resize", "crop", "pad", "loop_image_with_audio"}:
        result["width"] = _integer(result.get("width", 1920), "width", 2, 16384)
        result["height"] = _integer(result.get("height", 1080), "height", 2, 16384)
    if operation in {"crop", "pad", "overlay_image"}:
        result["x"] = _integer(result.get("x", 0), "x", 0, 16384)
        result["y"] = _integer(result.get("y", 0), "y", 0, 16384)
    if operation == "pad":
        color = str(result.get("color", "black"))
        if not _SAFE_COLOR.fullmatch(color):
            raise ValueError("color must be a named or six-digit hex color")
        result["color"] = color
    if operation == "transcode":
        video = str(result.get("video_codec", "libx264"))
        audio = str(result.get("audio_codec", "aac"))
        fmt = str(result.get("format", "")).lower()
        if video not in _VIDEO_CODECS or audio not in _AUDIO_CODECS:
            raise ValueError("unsupported transcode codec")
        if fmt and fmt not in _FORMATS:
            raise ValueError("unsupported transcode format")
        result.update(video_codec=video, audio_codec=audio, format=fmt)
        result["crf"] = _integer(result.get("crf", 23), "crf", 0, 51)
    if operation in {"change_fps", "loop_image_with_audio"}:
        result["fps"] = _number(result.get("fps", 30), "fps", 1, 240)
    if operation == "extract_frame":
        result["time"] = _number(result.get("time", 0), "time", 0, 86400)
    if operation in {"extract_audio", "replace_audio"}:
        codec = str(result.get("audio_codec", "aac"))
        if codec not in _AUDIO_CODECS:
            raise ValueError("unsupported audio codec")
        result["audio_codec"] = codec
    if operation == "concat":
        result["has_audio"] = bool(result.get("has_audio", True))
    if operation == "mix_audio":
        duration = str(result.get("duration", "longest"))
        if duration not in {"first", "longest", "shortest"}:
            raise ValueError("duration must be first, longest, or shortest")
        result["duration"] = duration
    if operation == "duck_audio":
        result["threshold"] = _number(result.get("threshold", 0.1), "threshold", 0.0001, 1)
        result["ratio"] = _number(result.get("ratio", 8), "ratio", 1, 20)
        result["attack"] = _number(result.get("attack", 20), "attack", 0.01, 2000)
        result["release"] = _number(result.get("release", 250), "release", 0.01, 9000)
    if operation == "normalize_loudness":
        result["integrated"] = _number(result.get("integrated", -16), "integrated", -70, -5)
        result["true_peak"] = _number(result.get("true_peak", -1.5), "true_peak", -9, 0)
        result["lra"] = _number(result.get("lra", 11), "lra", 1, 50)
    if operation == "fade":
        if result.get("media", "video") not in {"audio", "video"}:
            raise ValueError("media must be audio or video")
        if result.get("direction", "in") not in {"in", "out"}:
            raise ValueError("direction must be in or out")
        result["media"] = result.get("media", "video")
        result["direction"] = result.get("direction", "in")
    if operation == "crossfade":
        result["duration"] = _number(result.get("duration", 1), "duration", 0.01, 60)
        result["offset"] = _number(result.get("offset", 0), "offset", 0, 86400)
    if operation == "overlay_text":
        text = str(result.get("text", ""))
        if not text or len(text) > 500 or any(ord(char) < 32 for char in text):
            raise ValueError("text must contain 1 to 500 printable characters")
        result["text"] = text
        result["x"] = _integer(result.get("x", 0), "x", 0, 16384)
        result["y"] = _integer(result.get("y", 0), "y", 0, 16384)
        result["font_size"] = _integer(result.get("font_size", 48), "font_size", 6, 512)
        color = str(result.get("font_color", "white"))
        if not _SAFE_COLOR.fullmatch(color):
            raise ValueError("font_color must be a named or six-digit hex color")
        result["font_color"] = color
    return result


def compile_ffmpeg_argv(
    recipe: FFmpegRecipe,
    input_paths: Sequence[str],
    output_path: str,
    *,
    ffmpeg_binary: str = "ffmpeg",
    ffprobe_binary: str = "ffprobe",
) -> list[str]:
    """Compile a validated recipe into argv; no shell text is accepted."""

    if len(input_paths) != len(recipe.inputs):
        raise ValueError("input_paths must match recipe inputs")
    paths = [str(PurePath(path)) for path in input_paths]
    if any(not path for path in paths) or not output_path:
        raise ValueError("resolved input and output paths are required")
    if recipe.operation == "probe":
        return [
            ffprobe_binary, "-v", "error", "-show_format", "-show_streams",
            "-of", "json", paths[0],
        ]

    argv = [ffmpeg_binary, "-hide_banner", "-loglevel", "error", "-nostdin", "-y"]
    p = recipe.parameters
    op = recipe.operation

    if op == "trim":
        argv += ["-ss", str(p["start"]), "-i", paths[0], "-t", str(p["duration"]), "-c", "copy"]
    else:
        for path in paths:
            argv += ["-i", path]

    if op == "concat":
        streams = "".join(f"[{index}:v][{index}:a]" for index in range(len(paths)))
        if p["has_audio"]:
            graph = f"{streams}concat=n={len(paths)}:v=1:a=1[v][a]"
            argv += ["-filter_complex", graph, "-map", "[v]", "-map", "[a]"]
        else:
            streams = "".join(f"[{index}:v]" for index in range(len(paths)))
            argv += ["-filter_complex", f"{streams}concat=n={len(paths)}:v=1:a=0[v]", "-map", "[v]"]
    elif op == "resize":
        argv += ["-vf", f"scale={p['width']}:{p['height']}"]
    elif op == "crop":
        argv += ["-vf", f"crop={p['width']}:{p['height']}:{p['x']}:{p['y']}"]
    elif op == "pad":
        argv += ["-vf", f"pad={p['width']}:{p['height']}:{p['x']}:{p['y']}:{p['color']}"]
    elif op == "transcode":
        argv += ["-c:v", p["video_codec"], "-c:a", p["audio_codec"], "-crf", str(p["crf"])]
        if p["format"]:
            argv += ["-f", p["format"]]
    elif op == "change_fps":
        argv += ["-vf", f"fps={p['fps']}"]
    elif op == "extract_frame":
        argv[6:6] = ["-ss", str(p["time"])]
        argv += ["-frames:v", "1"]
    elif op == "extract_audio":
        argv += ["-vn", "-c:a", p["audio_codec"]]
    elif op == "replace_audio":
        argv += ["-map", "0:v:0", "-map", "1:a:0", "-c:v", "copy", "-c:a", p["audio_codec"], "-shortest"]
    elif op == "mix_audio":
        graph = f"amix=inputs={len(paths)}:duration={p['duration']}:normalize=0"
        argv += ["-filter_complex", graph]
    elif op == "duck_audio":
        graph = (
            f"[0:a][1:a]sidechaincompress=threshold={p['threshold']}:"
            f"ratio={p['ratio']}:attack={p['attack']}:release={p['release']}[ducked];"
            "[ducked][1:a]amix=inputs=2:duration=first[outa]")
        argv += ["-filter_complex", graph, "-map", "[outa]"]
    elif op == "normalize_loudness":
        argv += ["-af", f"loudnorm=I={p['integrated']}:TP={p['true_peak']}:LRA={p['lra']}"]
    elif op == "fade":
        prefix = "a" if p["media"] == "audio" else ""
        option = "-af" if p["media"] == "audio" else "-vf"
        argv += [option, f"{prefix}fade=t={p['direction']}:st={p['start']}:d={p['duration']}"]
    elif op == "crossfade":
        argv += ["-filter_complex", f"xfade=transition=fade:duration={p['duration']}:offset={p['offset']}"]
    elif op == "overlay_image":
        argv += ["-filter_complex", f"[0:v][1:v]overlay={p['x']}:{p['y']}"]
    elif op == "overlay_text":
        text = _escape_filter(p["text"])
        argv += ["-vf", (
            f"drawtext=text='{text}':x={p['x']}:y={p['y']}:"
            f"fontsize={p['font_size']}:fontcolor={p['font_color']}")]
    elif op == "burn_subtitles":
        argv += ["-vf", f"subtitles='{_escape_filter(paths[1])}'"]
    elif op == "loop_image_with_audio":
        argv = [
            ffmpeg_binary, "-hide_banner", "-loglevel", "error", "-nostdin", "-y",
            "-loop", "1", "-framerate", str(p["fps"]), "-i", paths[0],
            "-i", paths[1], "-vf", f"scale={p['width']}:{p['height']}",
            "-shortest", "-c:v", "libx264", "-c:a", "aac",
        ]
    argv.append(output_path)
    if len(argv) > 256 or any(len(value) > 8192 for value in argv):
        raise ValueError("compiled FFmpeg argv exceeds limits")
    return argv
