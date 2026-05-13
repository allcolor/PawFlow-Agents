from pathlib import Path

from pawflow import pfp

payload = pfp.payload
operation = payload.get("operation", "")
arguments = payload.get("arguments", {})
if operation != "generate":
    pfp.error(f"unsupported operation: {operation}")
else:
    output_dir = Path(pfp.context["output_dir"])
    output_path = output_dir / "image.png"
    output_path.write_bytes(
        b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR"
        b"\x00\x00\x00\x01\x00\x00\x00\x01\x08\x06\x00\x00\x00\x1f\x15\xc4\x89"
        b"\x00\x00\x00\nIDATx\x9cc\x00\x01\x00\x00\x05\x00\x01\r\n-\xb4"
        b"\x00\x00\x00\x00IEND\xaeB`\x82"
    )
    pfp.result(pfp.artifact(
        "image",
        "image.png",
        "image/png",
        filename="image.png",
    ))
