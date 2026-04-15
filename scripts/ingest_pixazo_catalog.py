"""One-shot Pixazo catalog ingester.

Parses docs/pixazo.md (extracted from pixazo.ai via the Docker
browser relay, Cloudflare-bypass) and emits catalog entries for
every API ID it finds — ready to merge into
data/repository/configs/pixazo_catalog.json.

What it does NOT do:
- It does NOT override existing catalog entries. Manually curated
  entries stay as-is; the ingester only fills in missing ones.
- It does NOT invent conventions. If the doc doesn't show a response
  payload with `polling_url`, the entry falls back to `legacy_poll`
  with the matching /prediction or /result endpoint when present.

Run: `python scripts/ingest_pixazo_catalog.py --write`
(omit --write to preview to stdout).
"""

import argparse
import json
import re
import sys
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DOC = ROOT / "docs" / "pixazo.md"
CATALOG = ROOT / "data" / "repository" / "configs" / "pixazo_catalog.json"


# ── Model-id inference from endpoint path ─────────────────────────────


POLL_SUFFIXES = (
    "/prediction", "/predict", "/status", "/getStatus",
    "/result", "/getResult", "/getAudioResult",
    "/getFluxDevStatus",
)


def _is_poll(path: str) -> bool:
    tail = path.split("?")[0].rstrip("/")
    for s in POLL_SUFFIXES:
        if tail.endswith(s):
            return True
    return False


def _provider_slug(path: str) -> str:
    """The first path segment under gateway is the provider slug."""
    parts = [p for p in path.split("/") if p]
    return parts[0] if parts else ""


# ── Operation name inference from endpoint path ───────────────────────


OP_HINTS = [
    # ordered: most specific first.
    (re.compile(r"train|trainer", re.I), "train"),
    (re.compile(r"text[_-]?to[_-]?video|textToVideo|t2v", re.I), "text_to_video"),
    (re.compile(r"image[_-]?to[_-]?video|imageToVideo|i2v|frame[_-]?2[_-]?video|frame2Video", re.I), "image_to_video"),
    (re.compile(r"video[_-]?edit|edit[_-]?video|lucy-edit-video", re.I), "video_edit"),
    (re.compile(r"speech[_-]?to[_-]?video|s2v", re.I), "speech_to_video"),
    (re.compile(r"describe", re.I), "describe"),
    (re.compile(r"remix", re.I), "remix"),
    (re.compile(r"upscale", re.I), "upscale"),
    (re.compile(r"rmbg|removeBackground|remove[_-]?bg", re.I), "remove_background"),
    (re.compile(r"try[_-]?on|tryon|vton", re.I), "try_on"),
    (re.compile(r"lipsync|lip-sync|omnihuman|avatar", re.I), "lipsync"),
    (re.compile(r"(^|[/-])(3d|rodin|trellis|tripo3d|hyper3d|hunyuan3d)(/|-|$)", re.I), "image_to_3d"),
    (re.compile(r"music|lyria|ace-step|suno|udio|tracks|elevenlabs-music", re.I), "music_generation"),
    (re.compile(r"tts|chatterbox|vibevoice|xtts|text-to-speech|speech", re.I), "text_to_speech"),
    (re.compile(r"text[_-]?to[_-]?image|textToImage|t2i|generateImage", re.I), "text_to_image"),
    (re.compile(r"edit[_-]?image|image[_-]?edit|image[_-]?to[_-]?image|imageToImage|i2i", re.I), "edit_image"),
]


def _infer_op(path: str, category_hint: str = "") -> str:
    p = path.lower()
    # 1) Explicit operation tokens in the path — wins over heuristics.
    if "trainer" in p:
        return "train"
    if any(s in p for s in ("frame2video", "image-to-video",
                              "imagetovideo", "i2v", "img2vid",
                              "image2video")):
        return "image_to_video"
    if any(s in p for s in ("text-to-video", "texttovideo", "t2v",
                              "videogeneration", "videotask",
                              "generatevideo")):
        return "text_to_video"
    if any(s in p for s in ("video-edit", "edit-video", "lucy-edit-video",
                              "video-to-video")):
        return "video_edit"
    if any(s in p for s in ("speech-to-video", "s2v", "speechtovideo")):
        return "speech_to_video"
    if any(s in p for s in ("describe",)):
        return "describe"
    if any(s in p for s in ("remix",)):
        return "remix"
    if "rmbg" in p or "remove-background" in p or "removebackground" in p:
        return "remove_background"
    if "upscale" in p:
        return "upscale"
    if any(s in p for s in ("vton", "tryon", "try-on", "virtual-try-on")):
        return "try_on"
    if "omnihuman" in p or "lipsync" in p:
        return "lipsync"
    if any(s in p for s in ("hunyuan3d", "rodin", "trellis", "tripo3d",
                              "image-to-3d")):
        return "image_to_3d"
    if any(s in p for s in ("xtts", "chatterbox", "vibevoice",
                              "voice-clone", "text-to-speech")):
        return "text_to_speech"
    if any(s in p for s in ("music", "lyria", "ace-step", "tracks",
                              "elevenlabs-music", "eleven-v3-alpha",
                              "minimax-hailuo-ai-music")):
        return "music_generation"
    if any(s in p for s in ("text-to-image", "texttoimage", "t2i",
                              "generateimage", "image-generation")):
        return "text_to_image"
    if any(s in p for s in ("image-to-image", "imagetoimage", "i2i",
                              "edit-image", "imageedit", "image-edit",
                              "editimage")):
        return "edit_image"

    # 2) Defaults by category when path is opaque.
    return {
        "video": "text_to_video", "audio": "music_generation",
        "3d": "image_to_3d", "upscale": "upscale",
        "try_on": "try_on", "lipsync": "lipsync",
        "trainer": "train",
    }.get(category_hint, "text_to_image")


# ── Doc parsing ───────────────────────────────────────────────────────


POST_RE = re.compile(r"^POST https://gateway\.pixazo\.ai(\S+)", re.M)
CAT_HEADER_RE = re.compile(
    r"^## (Video Generation|Image Generation|Audio & Music|"
    r"Virtual Try-On|Additional Models)\b.*$|"
    r"^### Category: (Image Generation|Video Generation|"
    r"Audio & Music|3D Generation|Image Processing|"
    r"Virtual Try-On|Lipsync & Avatar|Coming Soon)\b.*$", re.M)


def _category_for(section_title: str, endpoint: str) -> str:
    t = section_title.lower()
    ep = endpoint.lower()

    # Strongest signals: endpoint slug or path suffix.
    # "<provider>-image" is image (kling-image, qwen-image, …)
    # "<provider>-video" is video (kling-video, wan-video, …)
    slug = _provider_slug(ep)
    if re.search(r"(^|-)image(-|$)", slug):
        # But "image-to-video" / "image-to-3d" override → those keep their
        # specific category later.
        if "to-video" not in slug and "to-3d" not in slug:
            return "image"
    if re.search(r"(^|-)video(-|$)", slug):
        return "video"
    if "rmbg" in ep or "upscale" in ep or "upscaler" in ep \
            or "removeBackground" in ep:
        return "upscale"
    if "omnihuman" in ep or "lipsync" in ep:
        return "lipsync"
    if "vton" in ep or "virtual-try-on" in ep or "tryon" in ep:
        return "try_on"
    if "hunyuan3d" in ep or "rodin" in ep or "trellis" in ep \
            or "tripo3d" in ep:
        return "3d"
    if any(s in ep for s in (
            "elevenlabs", "eleven-v3", "lyria", "ace-step", "chatterbox",
            "vibevoice", "xtts", "tracks", "voice-clone", "suno",
            "minimax-hailuo-ai-music", "music")):
        return "audio"
    if "trainer" in ep:
        return "trainer"
    # Then path-suffix patterns (often more reliable than headers).
    if any(p in ep for p in (
            "videotask", "to-video", "tovideo", "videogeneration",
            "i2v", "t2v", "frame2video", "video-edit", "edit-video",
            "lucy-edit-video", "veo", "runway", "kling", "wan-video",
            "wan-2-", "sora-video", "p-video", "pika-video",
            "luma-dream-machine", "hailuo", "kandinsky", "vidu",
            "ltx", "mochi", "pixverse", "veed", "topaz", "ai-model-api",
            "byteplus", "seedance", "genflare")):
        # But fall through to image if the slug has clear image markers
        # ("text-to-image" / "edit-image" inside the slug).
        if any(p in ep for p in ("text-to-image", "edit-image",
                                  "image-edit", "/image/",
                                  "image-to-image")):
            return "image"
        return "video"
    if any(p in ep for p in (
            "image", "t2i", "i2i", "imagine", "imageEdit",
            "edit-image", "text-to-image", "imageToImage",
            "edit_image", "auraflow", "sdxl", "stable-diffusion",
            "ideogram", "recraft", "z-image", "nano-banana",
            "qwen-image", "gpt-image", "longcat-image",
            "pixelforge", "studio-ghibli", "p-image", "reve",
            "firered", "flux", "soul", "pixelyatra")):
        return "image"

    # Last resort: trust the section header.
    if "3d" in t and "image" not in t and "video" not in t:
        return "3d"
    if ("video" in t and "gen" in t) or "video api" in t:
        return "video"
    if "audio" in t or "music" in t or "tts" in t or "voice" in t:
        return "audio"
    if "try" in t or "vton" in t:
        return "try_on"
    if "lipsync" in t or "avatar" in t:
        return "lipsync"
    if "upscal" in t:
        return "upscale"
    return "image"


def _iter_sections(text: str):
    """Yield (category, section_body) for every meaningful subsection."""
    # Walk headers in document order, tracking which category we're under.
    pos = 0
    current_cat = "image"
    header_re = re.compile(r"^(#{2,3}) (.+)$", re.M)
    matches = list(header_re.finditer(text))
    for i, m in enumerate(matches):
        level = len(m.group(1))
        title = m.group(2).strip()
        end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
        body = text[m.start():end]
        if level == 2 or (level == 3 and title.startswith("Category:")):
            # Category switch
            current_cat = _category_for(title, "")
        # Only H3 sections produce models (H2 are category headers)
        if level == 3 and not title.startswith("Category:"):
            yield current_cat, title, body
        pos = end


# ── Build catalog patch ────────────────────────────────────────────────


VALID_SLUG = re.compile(r"^[a-z0-9][a-z0-9-]*[a-z0-9]$")


def _friendly_label(slug: str) -> str:
    """Human-readable label from a provider slug."""
    return slug.replace("-", " ").replace("_", " ").title()


def build_patch(existing: dict) -> dict:
    text = DOC.read_text(encoding="utf-8", errors="replace")
    # provider_slug → {"endpoints": set, "poll_endpoints": set, "category": str,
    #                   "has_polling_url": bool}
    providers = defaultdict(lambda: {
        "endpoints": set(), "poll_endpoints": set(),
        "category": "image", "has_polling_url": False,
    })

    for cat, title, body in _iter_sections(text):
        endpoints = POST_RE.findall(body)
        if not endpoints:
            continue
        for ep in endpoints:
            slug = _provider_slug(ep)
            if not slug or not VALID_SLUG.match(slug):
                continue
            info = providers[slug]
            # Category: prefer a specific one over generic "image" from context.
            derived = _category_for(title, ep)
            if info["category"] == "image" and derived != "image":
                info["category"] = derived
            elif info["category"] == "image":
                info["category"] = cat if cat else "image"
            if _is_poll(ep):
                info["poll_endpoints"].add(ep)
            else:
                info["endpoints"].add(ep)
            if "polling_url" in body and slug in body:
                info["has_polling_url"] = True

    patch = {}
    existing_slugs = set()
    for mid in existing.keys():
        existing_slugs.add(mid)

    for slug, info in providers.items():
        if slug in existing_slugs:
            continue
        if not info["endpoints"]:
            continue
        ops = {}
        for ep in sorted(info["endpoints"]):
            op_name = _infer_op(ep, info["category"])
            # Skip if we already have this op on this provider.
            if op_name in ops:
                # Keep the shorter endpoint path (typically the main one)
                if len(ep) < len(ops[op_name]["endpoint"]):
                    ops[op_name]["endpoint"] = ep
                continue
            entry = {"endpoint": ep,
                      "convention": "polling_url" if info["has_polling_url"]
                                    else "legacy_poll",
                      "id_field": "request_id"}
            if not info["has_polling_url"] and info["poll_endpoints"]:
                # Pick the most plausible poll endpoint for this op.
                # Prefer /prediction, then /status, then /result.
                prefs = ["/prediction", "/status", "/getStatus",
                         "/result", "/getResult"]
                sorted_polls = sorted(
                    info["poll_endpoints"],
                    key=lambda p: min(
                        (i for i, s in enumerate(prefs) if p.endswith(s)),
                        default=99))
                entry["poll_endpoint"] = sorted_polls[0]
            ops[op_name] = entry
        if not ops:
            continue
        patch[slug] = {
            "label": _friendly_label(slug),
            "category": info["category"],
            "operations": ops,
        }

    # Manual overrides for slugs the parser misclassifies. Doc headers
    # are sometimes wrong (Auraflow tagged "3D" in the doc but it's an
    # image generator) or genuinely ambiguous (sd3 listed under "Image
    # & Video Generation"). Keep this list minimal.
    overrides = {
        # Image gens the parser sent to the wrong bucket
        "auraflow-v0-3-512":              {"category": "image", "op": "text_to_image"},
        "sd3-5":                          {"category": "image", "op": "text_to_image"},
        "sd3":                            {"category": "image", "op": "text_to_image"},
        "inpainting":                     {"category": "image", "op": "edit_image"},
        "grok-imagine-video":             {"category": "video", "op": "text_to_video"},
        "kandinsky-5-0-pro-953":          {"category": "video", "op": "text_to_video"},
        # Audio: junk slugs from misparsed URLs — drop
        "image-generation":               None,
    }
    for slug, ovr in overrides.items():
        if ovr is None:
            patch.pop(slug, None)
            continue
        if slug not in patch:
            continue
        if ovr.get("category"):
            patch[slug]["category"] = ovr["category"]
        if ovr.get("op"):
            # Re-key the (single) op under the corrected name.
            ops = patch[slug]["operations"]
            if len(ops) == 1:
                old_op = next(iter(ops))
                patch[slug]["operations"] = {ovr["op"]: ops[old_op]}

    return patch


# ── Entry point ───────────────────────────────────────────────────────


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--write", action="store_true",
                        help="Write merged catalog to disk")
    args = parser.parse_args()

    catalog = json.loads(CATALOG.read_text(encoding="utf-8"))
    existing = catalog.get("models", {})
    patch = build_patch(existing)

    if not patch:
        print("No new models to add.", file=sys.stderr)
        return 0

    print(f"Adding {len(patch)} new models:", file=sys.stderr)
    cat_counts = defaultdict(int)
    for mid, m in patch.items():
        cat_counts[m["category"]] += 1
        ops = list(m["operations"].keys())
        print(f"  + {mid:40s}  [{m['category']:8s}]  ops={ops}", file=sys.stderr)
    print("", file=sys.stderr)
    print(f"By category: {dict(cat_counts)}", file=sys.stderr)

    if not args.write:
        print("\n(dry run — rerun with --write to apply)", file=sys.stderr)
        print(json.dumps(patch, indent=2, ensure_ascii=False))
        return 0

    merged = {**existing, **patch}
    catalog["models"] = merged
    CATALOG.write_text(
        json.dumps(catalog, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8")
    print(f"Catalog written: {len(merged)} total models.", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
