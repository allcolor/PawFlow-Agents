from __future__ import annotations

import re
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import urlsplit

import pytest


ROOT = Path(__file__).resolve().parents[1]
SITE = ROOT / "pawflow-website"
PRODUCT_PAGES = (
    "index.html",
    "product.html",
    "features.html",
    "flows.html",
    "relays.html",
    "integrations.html",
    "use-cases.html",
)
ALL_PAGES = PRODUCT_PAGES + (
    "quickstart.html",
    "docs.html",
    "howtos.html",
    "faq.html",
)


class PageParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.ids: set[str] = set()
        self.links: list[str] = []
        self.media: list[str] = []
        self.scripts: list[str] = []
        self.h1_count = 0

    def handle_starttag(
        self, tag: str, attrs: list[tuple[str, str | None]]
    ) -> None:
        values = dict(attrs)
        if element_id := values.get("id"):
            self.ids.add(element_id)
        if tag == "a" and (href := values.get("href")):
            self.links.append(href)
        if tag in {"img", "source", "video"} and (src := values.get("src")):
            self.media.append(src)
        if tag == "script" and (src := values.get("src")):
            self.scripts.append(src)
        if tag == "h1":
            self.h1_count += 1


def parse_page(name: str) -> PageParser:
    parser = PageParser()
    parser.feed((SITE / name).read_text(encoding="utf-8"))
    parser.close()
    return parser


def local_target(value: str) -> tuple[Path, str] | None:
    parsed = urlsplit(value)
    if parsed.scheme or parsed.netloc or value.startswith(("mailto:", "tel:", "data:")):
        return None
    path = parsed.path
    if not path:
        return None
    return SITE / path, parsed.fragment


@pytest.mark.parametrize("name", ALL_PAGES)
def test_website_pages_have_one_heading_and_valid_local_targets(name: str) -> None:
    parser = parse_page(name)

    assert parser.h1_count == 1
    assert "site.js?v=a34" in parser.scripts

    references = parser.links + parser.scripts
    if name in PRODUCT_PAGES:
        references += parser.media

    for value in references:
        target = local_target(value)
        if target is None:
            continue
        path, fragment = target
        assert path.exists(), f"{name} references missing local target {value}"
        if fragment and path.suffix == ".html":
            target_parser = parse_page(path.name)
            assert fragment in target_parser.ids, (
                f"{name} references missing fragment {value}"
            )


def test_homepage_tells_the_product_story_without_old_hero_clutter() -> None:
    html = (SITE / "index.html").read_text(encoding="utf-8")
    parser = parse_page("index.html")

    assert "AI agents that work on your real machines." in html
    assert "Work where your infrastructure already is" in html
    assert "One durable runtime" in html
    assert "Reason with agents. Run with flows." in html
    assert "Install in 5 minutes" in html
    assert {"why", "architecture", "demos", "stack", "comparison", "install"} <= parser.ids
    assert html.index('id="architecture"') < html.index('id="why"')
    assert "01 / HOW IT WORKS" in html
    assert "02 / WHY PAWFLOW" in html

    hero = html.split('<section class="landing-hero"', 1)[1].split("</section>", 1)[0]
    for removed_element in (
        "hero-logo-video",
        "hero-logo-sound",
        "hero-help-note",
        "hero-install",
        "signal-row",
        "product-strip",
    ):
        assert removed_element not in hero


def test_homepage_has_no_continuous_desktop_redraw_effects() -> None:
    css = (SITE / "style.css").read_text(encoding="utf-8")

    assert "zoom-glow-drift" not in css
    assert "zoom-stage-scan" not in css
    assert "zoom-node-pulse" not in css


def test_homepage_uses_three_real_demo_assets() -> None:
    html = (SITE / "index.html").read_text(encoding="utf-8")

    assert "chatgpt-mcp-live-workspace.mp4" in html
    assert "desktop-relay-session.webp" in html
    assert "agent-to-flow-pattern.webp" in html


@pytest.mark.parametrize(
    ("name", "expected"),
    (
        ("product.html", "The model is a component. The runtime is the product."),
        ("features.html", "Capabilities built around one shared runtime."),
        ("flows.html", "Turn repeatable agent work into durable visual flows."),
        ("relays.html", "Your real machine. Your tools. Your boundary."),
        ("integrations.html", "Bring your model. Expose one runtime."),
        ("use-cases.html", "Agentic work where your infrastructure already is."),
    ),
)
def test_secondary_pages_have_a_distinct_job(name: str, expected: str) -> None:
    assert expected in (SITE / name).read_text(encoding="utf-8")


def test_homepage_uses_a_fluid_infinite_zoom_canvas() -> None:
    css = (SITE / "style.css").read_text(encoding="utf-8")
    script = (SITE / "site.js").read_text(encoding="utf-8")

    assert "@media (prefers-reduced-motion: reduce)" in css
    assert ".stage-link i" in css
    assert "animation: none !important" in css
    assert ".zoom-story-active" in css
    assert "initZoomStory" in script
    assert "loopClone" in script
    assert "portalFrames" in script
    assert "frame.width / width" in script
    assert "frame.height / height" in script
    assert "easeInOut" in script
    assert "const threshold = raf ? 60 : 1" in script
    assert "queuedWheelDirection" in script
    assert "queueWheelStep" in script
    assert "window.addEventListener('wheel', onWheel, { passive: false })" in script
    assert "--scene-content-fit" in script
    assert "--scene-blur" in script
    assert "wheelLocked" not in script
    assert "wheelUnlockTimer" not in script
    assert "lastWheelEvent < 1000" not in script


def test_mobile_story_uses_boundary_aware_zoom_scenes() -> None:
    css = (SITE / "style.css").read_text(encoding="utf-8")
    script = (SITE / "site.js").read_text(encoding="utf-8")

    assert "initMobileStoryCanvas" in script
    assert "Array(scenes.length).fill('zoom')" in script
    assert "atBottom: scene.scrollTop + scene.clientHeight >= scene.scrollHeight - 2" in script
    assert "if (delta < 0 && start.atBottom) move(1)" in script
    assert "if (delta > 0 && start.atTop) move(-1)" in script
    assert "(current + direction + scenes.length) % scenes.length" in script
    assert ".mobile-story-active .mobile-story-scene" in css
    assert "overflow-y: auto" in css
    assert "mobile-zoom-in" in css


def test_story_navigation_yields_to_help_chat_input_and_scrolling() -> None:
    script = (SITE / "site.js").read_text(encoding="utf-8")

    assert "function isStoryKeyboardTarget(target)" in script
    assert script.count("if (isStoryKeyboardTarget(event.target)) return;") == 2
    assert "if (!enabled || isHelpWidgetTarget(event.target)) return;" in script
    assert "panel.addEventListener('wheel', (event) =>" in script
    assert "event.stopPropagation();" in script
    assert "target.closest('.pf-help-log')" in script
    assert "textarea.scrollHeight > textarea.clientHeight" in script
    assert "log.scrollTop += delta;" in script


def test_homepage_chapters_form_an_ordered_portal_chain() -> None:
    html = (SITE / "index.html").read_text(encoding="utf-8")
    script = (SITE / "site.js").read_text(encoding="utf-8")

    assert html.count("data-zoom-portal") == 7
    assert '<figure class="runtime-stage" data-zoom-portal' in html
    assert "'product', 'architecture', 'why', 'demos', 'stack', 'comparison', 'install'" in script
    architecture_link = 'href="#architecture" data-zoom-target="1"'
    about_link = 'href="#why" data-zoom-target="2"'
    assert architecture_link in html
    assert about_link in html
    assert html.index(architecture_link) < html.index(about_link)
    assert '<div class="zoom-story-hint" aria-hidden="true"><span></span></div>' in html
    assert "Scroll to zoom" not in html


def test_homepage_keeps_direct_navigation_around_the_zoom_story() -> None:
    html = (SITE / "index.html").read_text(encoding="utf-8")

    for target in ("product", "why", "architecture", "demos", "stack", "comparison", "install"):
        assert f'href="#{target}"' in html
    assert "How-tos" in html
    assert "Videos" in html


def test_howto_canvas_indexes_every_recipe_and_keeps_full_reader() -> None:
    html = (SITE / "howtos.html").read_text(encoding="utf-8")
    script = (SITE / "site.js").read_text(encoding="utf-8")
    recipe_ids = re.findall(r'<article[^>]*class="[^"]*\brecipe\b[^"]*"[^>]*\bid="([^"]+)"', html)

    assert len(recipe_ids) == 56
    assert len(recipe_ids) == len(set(recipe_ids))
    assert "workflow-agents" in recipe_ids
    assert "workflow-proposals" in recipe_ids
    assert "acp-agent" in recipe_ids
    assert "managed-mcp-providers" in recipe_ids
    assert "oauth-refresh-policy" in recipe_ids
    assert "WORKFLOW_AGENTS_ENABLED" not in html
    assert "AGENT_GROUPS_ENABLED" not in html
    assert 'data-howto-canvas' in html
    assert 'class="howto-reader"' in html
    assert html.count('data-zoom-target') == 9
    assert "buildHowtoCanvas" in script
    assert "howtos.html?read=" in script
    for recipe_id in recipe_ids:
        assert f"'{recipe_id}'" in script


def test_site_soundtrack_autoplays_with_a_user_control() -> None:
    script = (SITE / "site.js").read_text(encoding="utf-8")
    css = (SITE / "style.css").read_text(encoding="utf-8")
    soundtrack = SITE / "assets/media/audio/music_suno_brand.mp3"

    assert soundtrack.stat().st_size > 1_000_000
    assert "initAmbientSound" in script
    assert "audio.autoplay = true" in script
    assert "audio.loop = true" in script
    assert "pawflow-site-sound" in script
    assert "pawflow-site-sound-playback" in script
    assert "sessionStorage.setItem(playbackKey" in script
    assert "audio.currentTime = (savedPlayback.position + transit) % audio.duration" in script
    assert "window.addEventListener('pagehide'" in script
    assert "resumeOnGesture" in script
    assert "event.target.closest('.site-sound-toggle')" in script
    assert "toggle.classList.contains('is-blocked')" in script
    assert "audio.addEventListener('playing'" in script
    assert ".site-sound-toggle" in css


def test_release_fallback_matches_current_release() -> None:
    script = (SITE / "site.js").read_text(encoding="utf-8")

    assert "version: '1.0.0-beta.264'" in script


def test_beta_264_provider_and_history_story_is_public() -> None:
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    providers = (ROOT / "docs" / "llm_providers.md").read_text(encoding="utf-8")
    homepage = (SITE / "index.html").read_text(encoding="utf-8")
    features = (SITE / "features.html").read_text(encoding="utf-8")
    integrations = (SITE / "integrations.html").read_text(encoding="utf-8")
    faq = (SITE / "faq.html").read_text(encoding="utf-8")

    for provider in (
        "openai-responses",
        "omniroute",
        "acp",
        "cc_mcp",
        "codex_mcp",
        "agy_mcp",
    ):
        assert provider in readme
        assert provider in providers
        assert provider in faq

    assert "beta.264:" in homepage
    assert "exact display-row indices" in features
    assert "MCP, ACP, A2A, and AG-UI" in integrations
    assert "registered but unavailable" not in faq
    assert "StopHookArgs.finalModelOutput" in faq
    assert "| `agy_mcp` | Available" in providers
    assert "external_agui" in readme
    assert "external_agui" in providers
    assert "This is not an `llmConnection` provider" in providers
    assert "agent-level protocol/runtime" in providers
    assert "never starts or falls back to a local LLM" in providers
    assert "`aguiConnection`" in readme
