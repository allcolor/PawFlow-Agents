"""Extract WaveSpeedAI model documentation into PawFlow repository files.

The WaveSpeed docs are rendered server-side by Nextra. The docs landing
page contains the complete model-library sidebar, including every
``/docs/docs-api/...`` model page. This script reads that sidebar,
downloads each model page, writes a human reference file, and builds the
machine catalog consumed by the WaveSpeed services.

Run:
    python scripts/extract_wavespeed_catalog.py --write

Without ``--write`` the script prints the discovered model count only.
"""

import argparse
import html
from html.parser import HTMLParser
import json
import re
import sys
import time
import urllib.error
import urllib.request
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple


ROOT = Path(__file__).resolve().parent.parent
DOC_URL = "https://wavespeed.ai/docs"
DOC_OUT = ROOT / "docs" / "wavespeed.md"
CATALOG_OUT = ROOT / "data" / "repository" / "configs" / "wavespeed_catalog.json"
BASE_API = "https://api.wavespeed.ai/api/v3"

UA = "PawFlow-WaveSpeed-Docs/1.0 (+https://github.com/allcolor/PawFlow-Agents)"


class _TextExtractor(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self._skip = 0
        self.parts: List[str] = []

    def handle_starttag(self, tag, attrs):
        if tag in ("script", "style", "svg"):
            self._skip += 1
        if tag in ("p", "div", "li", "tr", "h1", "h2", "h3", "h4", "pre", "br"):
            self.parts.append("\n")

    def handle_endtag(self, tag):
        if tag in ("script", "style", "svg") and self._skip:
            self._skip -= 1
        if tag in ("p", "div", "li", "tr", "h1", "h2", "h3", "h4", "pre"):
            self.parts.append("\n")

    def handle_data(self, data):
        if not self._skip and data.strip():
            self.parts.append(data)

    def text(self) -> str:
        raw = html.unescape(" ".join(self.parts))
        raw = re.sub(r"[ \t\r\f\v]+", " ", raw)
        raw = re.sub(r"\n\s+", "\n", raw)
        raw = re.sub(r"\n{3,}", "\n\n", raw)
        return raw.strip()


def _fetch(url: str, *, retries: int = 3) -> str:
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    last = None
    for attempt in range(retries):
        try:
            with urllib.request.urlopen(req, timeout=45) as resp:
                return resp.read().decode("utf-8", errors="replace")
        except (urllib.error.URLError, TimeoutError) as e:
            last = e
            time.sleep(1 + attempt)
    raise RuntimeError(f"failed to fetch {url}: {last}")


def _html_to_text(markup: str) -> str:
    parser = _TextExtractor()
    parser.feed(markup)
    return parser.text()


def _title(markup: str, fallback: str) -> str:
    m = re.search(r"<title>(.*?)</title>", markup, re.I | re.S)
    if not m:
        return fallback
    text = html.unescape(re.sub(r"\s+", " ", m.group(1))).strip()
    return re.sub(r"\s+-\s+WaveSpeedAI$", "", text)


def _discover_model_links(index_html: str) -> List[Tuple[str, str]]:
    """Return sorted (path, label) pairs from the complete sidebar."""
    pattern = re.compile(
        r"href=\"(?P<href>/docs/docs-api/[^\"]+)\"[^>]*>(?P<label>.*?)</a>",
        re.I | re.S,
    )
    links: Dict[str, str] = {}
    for m in pattern.finditer(index_html):
        href = html.unescape(m.group("href"))
        label = _html_to_text(m.group("label"))
        if href.rstrip("/") == "/docs/docs-api":
            continue
        # Provider index pages have only two path components after docs-api.
        parts = [p for p in href.split("/") if p]
        if len(parts) < 4:
            continue
        links[href] = label or parts[-1].replace("-", " ").title()
    return sorted(links.items())


def _first_api_endpoint(text: str) -> str:
    endpoints = []
    for m in re.finditer(r"https://api\.wavespeed\.ai/api/v3/([^\s\"'<>\\]+)", text):
        raw = html.unescape(m.group(1))
        raw = re.split(r"[\"'<>\\\s]", raw, maxsplit=1)[0]
        path = "/" + raw.strip().strip("`)")
        if path.startswith("/predictions/"):
            continue
        if path not in endpoints:
            endpoints.append(path)
    return endpoints[0] if endpoints else ""


def _prediction_result_endpoint(text: str) -> str:
    if "/predictions/" in text and "/result" in text:
        return "/predictions/{id}/result"
    if "/predictions/" in text:
        return "/predictions/{id}"
    return "/predictions/{id}/result"


def _slug_label(path: str) -> str:
    return path.strip("/").split("/")[-1].replace("-", " ").title()


def _operation_for(label: str, endpoint: str) -> str:
    value = f"{label} {endpoint}".lower()
    checks = [
        (("voice-clone", "voice clone", "voice_clone"), "voice_clone"),
        (("voice-design", "voice design"), "voice_design"),
        (("text-to-speech", "tts", "speech 02", "speech 2.", "eleven v3", "turbo v2", "flash v2"), "text_to_speech"),
        (("speech-to-video", "speech to video"), "speech_to_video"),
        (("audio-to-video", "lipsync", "lip-sync", "talking avatar", "avatar", "digital human"), "lipsync"),
        (("text-to-3d", "text to 3d"), "text_to_3d"),
        (("image-to-3d", "sketch-to-3d", "3d", "rodin", "tripo3d", "hunyuan3d", "meshy"), "image_to_3d"),
        (("remove-background", "background remover", "rmbg"), "remove_background"),
        (("upscale", "upscaler", "super resolution", "seedvr", "real-esrgan"), "upscale"),
        (("tryon", "try-on", "virtual outfit", "clothes changer", "outfit"), "try_on"),
        (("audio-to-audio", "audio to audio", "inpaint", "outpaint"), "audio_edit"),
        (("music", "song", "lyria", "ace-step", "heartmula"), "music_generation"),
        (("video-extend", "extend-video", "video extend"), "video_extend"),
        (("video-edit", "edit-video", "video edit", "video-to-video", "v2v"), "video_edit"),
        (("reference-to-video", "reference to video"), "reference_to_video"),
        (("start-end-to-video", "transition", "start end to video"), "frame_to_video"),
        (("image-to-video", "image to video", "i2v"), "image_to_video"),
        (("text-to-video", "text to video", "t2v"), "text_to_video"),
        (("image-edit", "edit-image", "image edit", "image-to-image", "image to image", "i2i", "inpaint", "eraser", "watermark remover"), "edit_image"),
        (("text-to-image", "text to image", "image text to image", "t2i"), "text_to_image"),
        (("lora trainer", "trainer", "train"), "train"),
    ]
    for needles, op in checks:
        if any(n in value for n in needles):
            return op
    if "image" in value:
        return "text_to_image"
    if "video" in value:
        return "text_to_video"
    if "audio" in value or "voice" in value or "speech" in value:
        return "music_generation"
    return "text_to_image"


def _category_for(op: str, label: str, endpoint: str) -> str:
    value = f"{label} {endpoint}".lower()
    if op in ("voice_clone", "voice_design"):
        return "voice_clone"
    if op in ("text_to_speech", "music_generation", "audio_edit"):
        return "audio"
    if op in ("lipsync", "speech_to_video"):
        return "lipsync"
    if op == "try_on":
        return "try_on"
    if op == "upscale":
        return "upscale"
    if op == "remove_background":
        return "upscale"
    if op in ("image_to_3d", "text_to_3d") or "3d" in value:
        return "3d"
    if op == "train":
        return "trainer"
    if "video" in op or "video" in value:
        return "video"
    return "image"


def _extract_request_params(text: str) -> Dict[str, str]:
    params: Dict[str, str] = {}
    # Pages include the right-sidebar table of contents before the real
    # API reference. Prefer the concrete request table under "Task Submission
    # Parameters"; otherwise use the last matching heading, not the first TOC
    # item.
    anchor = text.rfind("Task Submission Parameters")
    if anchor >= 0:
        start = text.find("Request Parameters", anchor)
    else:
        start = text.rfind("Request Parameters")
    if start < 0:
        start = text.rfind("Parameters")
    if start < 0:
        return params
    end_candidates = [
        p for p in (text.find("Response Parameters", start),
                    text.find("Result Request Parameters", start),
                    text.find("API Endpoints", start + 20))
        if p > start
    ]
    block = text[start:min(end_candidates) if end_candidates else start + 4000]
    lines = [ln.strip() for ln in block.splitlines() if ln.strip()]
    row_re = re.compile(
        r"^(?P<name>[A-Za-z_][A-Za-z0-9_]*)\s+"
        r"(?P<type>string|integer|number|boolean|array|object)\b\s*"
        r"(?P<rest>.*)$"
    )
    compact: Dict[str, str] = {}
    i = 0
    while i < len(lines):
        m = row_re.match(lines[i])
        if not m:
            i += 1
            continue
        name = m.group("name")
        desc_parts = [m.group("type"), m.group("rest").strip()]
        i += 1
        while i < len(lines) and not row_re.match(lines[i]):
            if lines[i] not in ("Response Parameters", "Result Request Parameters"):
                desc_parts.append(lines[i])
            i += 1
        compact[name] = " ".join(p for p in desc_parts if p)[:240]
    if compact:
        return compact
    skip = {"Parameters", "Task Submission Parameters", "Request Parameters",
            "Parameter", "Type", "Required", "Default", "Range", "Description"}
    names = []
    for i, line in enumerate(lines):
        if line in skip or len(line) > 80:
            continue
        if not re.match(r"^[A-Za-z_][A-Za-z0-9_]*$", line):
            continue
        if line in params:
            continue
        window = " ".join(lines[i + 1:i + 8])
        if not re.search(r"\b(string|integer|number|boolean|array|object)\b", window):
            continue
        names.append((line, i))
    for idx, (name, pos) in enumerate(names):
        next_pos = names[idx + 1][1] if idx + 1 < len(names) else min(len(lines), pos + 10)
        details = [ln for ln in lines[pos + 1:next_pos] if ln not in skip]
        params[name] = " ".join(details[:8])[:240]
    return params


def _op_metadata(op: str, params: Dict[str, str], endpoint: str) -> Dict[str, Any]:
    meta: Dict[str, Any] = {
        "endpoint": endpoint,
        "convention": "prediction_poll",
        "id_field": "id",
        "output_path": "data.outputs.0",
        "params": params,
    }
    if op in ("edit_image", "image_to_video", "image_to_3d", "try_on"):
        if "image" in params:
            meta["input_field"] = "image"
        elif "image_url" in params:
            meta["input_field"] = "image_url"
    if op == "lipsync":
        if "audio" in params:
            meta["audio_field"] = "audio"
        if "audio_url" in params:
            meta["audio_field"] = "audio_url"
        if "video" in params:
            meta["video_field"] = "video"
        if "image" in params:
            meta["image_field"] = "image"
    if op == "try_on":
        if "person_image" in params:
            meta["person_field"] = "person_image"
        if "garment_image" in params:
            meta["garment_field"] = "garment_image"
    if op == "voice_clone":
        if "audio" in params:
            meta["reference_audio_field"] = "audio"
        elif "reference_audio" in params:
            meta["reference_audio_field"] = "reference_audio"
    return meta


def _build_page(path: str, sidebar_label: str) -> Optional[Dict[str, Any]]:
    url = "https://wavespeed.ai" + path
    markup = _fetch(url)
    text = _html_to_text(markup)
    title = _title(markup, sidebar_label)
    endpoint = _first_api_endpoint(markup + "\n" + text)
    if not endpoint:
        return None
    op = _operation_for(title, endpoint)
    params = _extract_request_params(text)
    category = _category_for(op, title, endpoint)
    model_id = endpoint.lstrip("/")
    return {
        "model_id": model_id,
        "label": sidebar_label or title or _slug_label(endpoint),
        "title": title,
        "doc_url": url,
        "endpoint": endpoint,
        "result_endpoint": _prediction_result_endpoint(text),
        "category": category,
        "operation": op,
        "params": params,
        "text": text,
    }


def _fetch_pages(links: List[Tuple[str, str]], max_workers: int) -> List[Dict[str, Any]]:
    pages = []
    with ThreadPoolExecutor(max_workers=max_workers) as pool:
        futures = {pool.submit(_build_page, p, label): (p, label) for p, label in links}
        done = 0
        for fut in as_completed(futures):
            done += 1
            path, label = futures[fut]
            try:
                page = fut.result()
                if page:
                    pages.append(page)
            except Exception as e:
                print(f"WARN: {path}: {e}", file=sys.stderr)
            if done % 50 == 0:
                print(f"Fetched {done}/{len(links)} pages...", file=sys.stderr)
    return sorted(pages, key=lambda p: p["model_id"])


def _catalog(pages: Iterable[Dict[str, Any]]) -> Dict[str, Any]:
    models: Dict[str, Any] = {}
    for page in pages:
        mid = page["model_id"]
        op = page["operation"]
        entry = models.setdefault(mid, {
            "label": page["label"],
            "category": page["category"],
            "doc_url": page["doc_url"],
            "operations": {},
        })
        entry["operations"][op] = _op_metadata(op, page["params"], page["endpoint"])
    return {
        "_comment": (
            "WaveSpeedAI model catalog generated from https://wavespeed.ai/docs. "
            "All operations use POST /api/v3/<endpoint>, then poll data.urls.get "
            "or /predictions/{id}/result until data.status=completed and read data.outputs[0]."
        ),
        "base_url": BASE_API,
        "models": models,
    }


def _write_reference(pages: List[Dict[str, Any]]) -> None:
    counts = Counter(p["category"] for p in pages)
    op_counts = Counter(p["operation"] for p in pages)
    lines = [
        "# WaveSpeedAI API — Model Reference",
        "",
        "> Auto-generated from [wavespeed.ai/docs](https://wavespeed.ai/docs).",
        f"> {len(pages)} API model pages extracted.",
        "",
        "## Common API Pattern",
        "",
        "- Base URL: `https://api.wavespeed.ai/api/v3`",
        "- Authentication: `Authorization: Bearer ${WAVESPEED_API_KEY}`",
        "- Submit: `POST /api/v3/<model-endpoint>` with JSON parameters",
        "- Poll: `GET data.urls.get` or `GET /api/v3/predictions/{id}/result`",
        "- Terminal statuses: `completed`, `failed`",
        "- Media outputs: `data.outputs[]`",
        "",
        "## Counts",
        "",
        "### By Category",
        "",
    ]
    for key, value in sorted(counts.items()):
        lines.append(f"- `{key}`: {value}")
    lines.extend(["", "### By Operation", ""])
    for key, value in sorted(op_counts.items()):
        lines.append(f"- `{key}`: {value}")
    grouped: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    for page in pages:
        grouped[page["category"]].append(page)
    for category in sorted(grouped):
        lines.extend(["", f"## Category: {category}", ""])
        for page in grouped[category]:
            lines.extend([
                f"### {page['label']}",
                "",
                f"- **Model ID:** `{page['model_id']}`",
                f"- **Operation:** `{page['operation']}`",
                f"- **Endpoint:** `POST {BASE_API}{page['endpoint']}`",
                f"- **Result:** `GET {BASE_API}{page['result_endpoint']}`",
                f"- **Docs:** {page['doc_url']}",
            ])
            if page["params"]:
                lines.extend(["", "**Request Parameters**", ""])
                for name, desc in page["params"].items():
                    lines.append(f"- `{name}`: {desc}" if desc else f"- `{name}`")
            lines.append("")
    DOC_OUT.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--write", action="store_true")
    parser.add_argument("--max-workers", type=int, default=8)
    args = parser.parse_args()

    index_html = _fetch(DOC_URL)
    links = _discover_model_links(index_html)
    print(f"Discovered {len(links)} model links", file=sys.stderr)
    pages = _fetch_pages(links, max_workers=max(1, args.max_workers))
    print(f"Extracted {len(pages)} API model pages", file=sys.stderr)
    counts = Counter(p["category"] for p in pages)
    print(f"By category: {dict(sorted(counts.items()))}", file=sys.stderr)

    if not args.write:
        return 0
    catalog = _catalog(pages)
    CATALOG_OUT.write_text(
        json.dumps(catalog, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    _write_reference(pages)
    print(f"Wrote {CATALOG_OUT}", file=sys.stderr)
    print(f"Wrote {DOC_OUT}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
