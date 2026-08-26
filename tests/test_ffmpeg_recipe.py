import subprocess
import sys
from pathlib import Path

import pytest

sys.path.append(str(Path(__file__).resolve().parent.parent / "tools"))

from core.ffmpeg_recipe import FFmpegRecipe, OPERATIONS, compile_ffmpeg_argv
from tools.fs_exec import action_exec


FID1 = "a1b2c3d4e5f6"
FID2 = "0123456789ab"


def recipe(operation="trim", inputs=(FID1,), output="result.mp4", **parameters):
    return FFmpegRecipe(
        operation=operation,
        inputs=inputs,
        output_filename=output,
        parameters=parameters,
    )


def test_closed_catalog_contains_every_v1_operation():
    assert OPERATIONS == {
        "probe", "trim", "concat", "resize", "crop", "pad", "transcode",
        "change_fps", "extract_frame", "extract_audio", "replace_audio",
        "mix_audio", "duck_audio", "normalize_loudness", "fade", "crossfade",
        "overlay_image", "overlay_text", "burn_subtitles",
        "loop_image_with_audio",
    }


@pytest.mark.parametrize("field", ["command", "shell", "args", "filter_complex"])
def test_recipe_rejects_unrestricted_execution_fields(field):
    with pytest.raises(ValueError, match="unsupported recipe fields"):
        FFmpegRecipe.from_dict({
            "operation": "trim",
            "inputs": [FID1],
            "output_filename": "out.mp4",
            field: "touch /tmp/owned",
        })


@pytest.mark.parametrize("value", [
    "../video.mp4",
    "/etc/passwd",
    "https://example.test/a.mp4",
    "file:///etc/passwd",
    "a1b2c3d4e5f6;touch-owned",
])
def test_recipe_accepts_only_filestore_ids(value):
    with pytest.raises(ValueError, match="FileStore IDs"):
        recipe(inputs=(value,))


@pytest.mark.parametrize("name", ["../out.mp4", "/out.mp4", "out.sh", "x;touch.mp4"])
def test_recipe_rejects_unsafe_output_names(name):
    with pytest.raises(ValueError, match="output_filename|format"):
        recipe(output=name)


def test_recipe_rejects_unknown_operation_parameter():
    with pytest.raises(ValueError, match="unsupported parameters"):
        recipe(command="rm -rf /")


def test_compiler_returns_distinct_argv_tokens():
    value = recipe(start=1.25, duration=2.5)
    argv = compile_ffmpeg_argv(value, ["input one.mp4"], "output one.mp4")
    assert argv[0] == "ffmpeg"
    assert "input one.mp4" in argv
    assert "output one.mp4" == argv[-1]
    assert all(not token.startswith("sh -c") for token in argv)


def test_overlay_text_is_filter_escaped_but_never_becomes_a_command():
    value = recipe(
        operation="overlay_text",
        text="title: 'safe', [one]",
        x=10,
        y=20,
        font_size=32,
        font_color="white",
    )
    argv = compile_ffmpeg_argv(value, ["in.mp4"], "out.mp4")
    filter_value = argv[argv.index("-vf") + 1]
    assert r"\:" in filter_value
    assert r"\'" in filter_value
    assert r"\," in filter_value
    assert argv[-1] == "out.mp4"


def test_concat_and_mix_have_bounded_input_counts():
    with pytest.raises(ValueError, match="requires between"):
        recipe(operation="concat")
    with pytest.raises(ValueError, match="requires between"):
        recipe(operation="mix_audio", inputs=(FID1,) * 17, output="out.wav")


def test_dimensions_duration_codec_and_color_are_bounded():
    with pytest.raises(ValueError, match="width"):
        recipe(operation="resize", width=99999, height=1080)
    with pytest.raises(ValueError, match="duration"):
        recipe(start=0, duration=0)
    with pytest.raises(ValueError, match="codec"):
        recipe(operation="transcode", video_codec="shell", audio_codec="aac")
    with pytest.raises(ValueError, match="color"):
        recipe(operation="pad", width=100, height=100, color="red;touch")


def test_probe_uses_ffprobe_and_no_output_argument():
    value = recipe(operation="probe", output="probe.json")
    argv = compile_ffmpeg_argv(value, ["input.mp4"], "unused.json")
    assert argv[0] == "ffprobe"
    assert argv[-1] == "input.mp4"
    assert "unused.json" not in argv


def test_relay_exec_argv_uses_shell_false(monkeypatch, tmp_path):
    captured = {}

    def fake_run(request_id, argv, **kwargs):
        captured["request_id"] = request_id
        captured["argv"] = argv
        captured.update(kwargs)
        return subprocess.CompletedProcess(argv, 0, "ok", "")

    monkeypatch.setattr("tools.fs_exec._run_cancellable", fake_run)
    result = action_exec(
        str(tmp_path),
        str(tmp_path),
        {"request_id": "r1", "argv": ["printf", "%s", "hello;touch owned"]},
        allow_exec=True,
    )
    assert result["returncode"] == 0
    assert captured["argv"] == ["printf", "%s", "hello;touch owned"]
    assert captured["shell"] is False


def test_relay_exec_rejects_command_and_argv_together(tmp_path):
    with pytest.raises(ValueError, match="exactly one"):
        action_exec(
            str(tmp_path),
            str(tmp_path),
            {"command": "true", "argv": ["true"]},
            allow_exec=True,
        )
