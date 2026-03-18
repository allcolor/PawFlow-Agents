"""BrowserService — Playwright browser session pool for agent automation.

Manages browser sessions per conversation_id. Sessions are reused within
a conversation and auto-cleaned after inactivity.

Playwright is optional — graceful import with clear error if absent.

Config env vars:
    PYFI2_BROWSER_TIMEOUT: Session inactivity timeout (default: 300s)
    PYFI2_BROWSER_ALLOWED_DOMAINS: Comma-separated allowlist (empty = all)
    PYFI2_BROWSER_BLOCKED_DOMAINS: Comma-separated blocklist
"""

import logging
import os
import queue
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, Optional
from urllib.parse import urlparse

logger = logging.getLogger(__name__)

_BLOCKED_SCHEMES = {"file", "javascript", "data", "vbscript"}


@dataclass
class BrowserSession:
    """A browser session tied to a conversation."""
    context: Any = None  # playwright BrowserContext
    page: Any = None  # playwright Page
    last_activity: float = field(default_factory=time.time)


class BrowserService:
    """Singleton service managing Playwright browser sessions.

    All Playwright operations run on a dedicated worker thread
    (Playwright requires same-thread access).
    """

    _instance: Optional["BrowserService"] = None
    _lock = threading.Lock()

    def __init__(self):
        self._sessions: Dict[str, BrowserSession] = {}
        self._session_lock = threading.Lock()
        self._browser = None
        self._playwright = None
        self._worker_thread: Optional[threading.Thread] = None
        self._cmd_queue: queue.Queue = queue.Queue()
        self._started = False
        self._stop_event = threading.Event()
        self._cleanup_thread: Optional[threading.Thread] = None
        self._timeout = int(os.environ.get("PYFI2_BROWSER_TIMEOUT", "300"))

        allowed = os.environ.get("PYFI2_BROWSER_ALLOWED_DOMAINS", "")
        self._allowed_domains = {d.strip().lower() for d in allowed.split(",") if d.strip()} if allowed else set()

        blocked = os.environ.get("PYFI2_BROWSER_BLOCKED_DOMAINS", "")
        self._blocked_domains = {d.strip().lower() for d in blocked.split(",") if d.strip()} if blocked else set()

    @classmethod
    def instance(cls) -> "BrowserService":
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = cls()
        return cls._instance

    @classmethod
    def reset(cls):
        with cls._lock:
            if cls._instance:
                cls._instance.shutdown()
            cls._instance = None

    def validate_url(self, url: str) -> None:
        """Validate URL against security rules."""
        parsed = urlparse(url)
        scheme = (parsed.scheme or "").lower()

        if scheme in _BLOCKED_SCHEMES:
            raise ValueError(f"Blocked URL scheme: {scheme}://")

        hostname = (parsed.hostname or "").lower()
        if not hostname:
            raise ValueError("URL has no hostname")

        if self._allowed_domains and hostname not in self._allowed_domains:
            raise ValueError(f"Domain '{hostname}' not in allowed list")

        if hostname in self._blocked_domains:
            raise ValueError(f"Domain '{hostname}' is blocked")

    def _ensure_started(self):
        """Start the Playwright worker thread if not running."""
        if self._started:
            return

        with self._lock:
            if self._started:
                return

            try:
                import playwright.sync_api  # noqa: F401
            except ImportError:
                raise ImportError(
                    "playwright is required for browser automation. "
                    "Install with: pip install playwright && playwright install chromium"
                )

            self._worker_thread = threading.Thread(
                target=self._worker_loop, daemon=True, name="browser-worker",
            )
            self._worker_thread.start()

            # Wait for browser to be ready
            ready_event = threading.Event()
            error_holder = [None]

            def on_ready(err=None):
                error_holder[0] = err
                ready_event.set()

            self._cmd_queue.put(("_init_browser", (), {}, on_ready))
            ready_event.wait(timeout=30)

            if error_holder[0]:
                raise RuntimeError(f"Browser init failed: {error_holder[0]}")

            self._started = True

            # Start cleanup thread
            self._cleanup_thread = threading.Thread(
                target=self._cleanup_loop, daemon=True, name="browser-cleanup",
            )
            self._cleanup_thread.start()

    def _worker_loop(self):
        """Worker thread: processes Playwright commands sequentially."""
        from playwright.sync_api import sync_playwright

        pw = sync_playwright().start()
        self._playwright = pw

        while not self._stop_event.is_set():
            try:
                item = self._cmd_queue.get(timeout=1)
            except queue.Empty:
                continue

            method_name, args, kwargs, callback = item
            try:
                method = getattr(self, method_name)
                result = method(*args, **kwargs)
                if callback:
                    callback(result)
            except Exception as e:
                if callback:
                    callback(e)
                else:
                    logger.error(f"Browser command '{method_name}' failed: {e}")

        # Cleanup
        self._close_all_sessions()
        if self._browser:
            try:
                self._browser.close()
            except Exception:
                pass
        if pw:
            try:
                pw.stop()
            except Exception:
                pass

    def _init_browser(self):
        """Initialize Chromium browser (runs on worker thread)."""
        self._browser = self._playwright.chromium.launch(headless=True)
        logger.info("Playwright Chromium browser launched")
        return None

    def _run_on_worker(self, method_name: str, *args, **kwargs) -> Any:
        """Execute a method on the worker thread and wait for result."""
        self._ensure_started()
        result_event = threading.Event()
        result_holder = [None]

        def on_complete(result):
            result_holder[0] = result
            result_event.set()

        self._cmd_queue.put((method_name, args, kwargs, on_complete))
        result_event.wait(timeout=30)

        result = result_holder[0]
        if isinstance(result, Exception):
            raise result
        return result

    def get_session(self, conversation_id: str) -> BrowserSession:
        """Get or create a browser session for a conversation."""
        with self._session_lock:
            session = self._sessions.get(conversation_id)
            if session:
                session.last_activity = time.time()
                return session

        # Create new session on worker thread
        self._run_on_worker("_create_session", conversation_id)

        with self._session_lock:
            return self._sessions[conversation_id]

    def _create_session(self, conversation_id: str):
        """Create a new browser session (runs on worker thread)."""
        context = self._browser.new_context(
            viewport={"width": 1280, "height": 720},
        )
        page = context.new_page()
        session = BrowserSession(context=context, page=page)

        with self._session_lock:
            self._sessions[conversation_id] = session

        logger.info(f"Browser session created for {conversation_id[:8]}")

    def close_session(self, conversation_id: str):
        """Close a browser session."""
        self._run_on_worker("_close_session_internal", conversation_id)

    def _close_session_internal(self, conversation_id: str):
        """Close session (runs on worker thread)."""
        with self._session_lock:
            session = self._sessions.pop(conversation_id, None)
        if session:
            try:
                session.context.close()
            except Exception:
                pass
            logger.info(f"Browser session closed for {conversation_id[:8]}")

    def _close_all_sessions(self):
        """Close all sessions (runs on worker thread)."""
        with self._session_lock:
            conv_ids = list(self._sessions.keys())
        for cid in conv_ids:
            self._close_session_internal(cid)

    def _cleanup_loop(self):
        """Background thread to clean up idle sessions."""
        while not self._stop_event.is_set():
            self._stop_event.wait(timeout=30)
            if self._stop_event.is_set():
                break

            now = time.time()
            with self._session_lock:
                expired = [
                    cid for cid, s in self._sessions.items()
                    if now - s.last_activity > self._timeout
                ]

            for cid in expired:
                try:
                    self.close_session(cid)
                except Exception as e:
                    logger.warning(f"Cleanup error for {cid[:8]}: {e}")

    def navigate(self, conversation_id: str, url: str) -> str:
        """Navigate to URL. Returns page title + URL."""
        self.validate_url(url)
        return self._run_on_worker("_navigate", conversation_id, url)

    def _navigate(self, conversation_id: str, url: str) -> str:
        session = self._get_session_internal(conversation_id)
        session.page.goto(url, wait_until="domcontentloaded", timeout=15000)
        session.last_activity = time.time()
        return f"Navigated to: {session.page.title()} ({session.page.url})"

    def click(self, conversation_id: str, selector: str) -> str:
        return self._run_on_worker("_click", conversation_id, selector)

    def _click(self, conversation_id: str, selector: str) -> str:
        session = self._get_session_internal(conversation_id)
        session.page.click(selector, timeout=5000)
        session.last_activity = time.time()
        return f"Clicked: {selector}"

    def fill(self, conversation_id: str, selector: str, value: str) -> str:
        return self._run_on_worker("_fill", conversation_id, selector, value)

    def _fill(self, conversation_id: str, selector: str, value: str) -> str:
        session = self._get_session_internal(conversation_id)
        session.page.fill(selector, value, timeout=5000)
        session.last_activity = time.time()
        return f"Filled '{selector}' with value"

    def extract(self, conversation_id: str, selector: str) -> str:
        return self._run_on_worker("_extract", conversation_id, selector)

    def _extract(self, conversation_id: str, selector: str) -> str:
        session = self._get_session_internal(conversation_id)
        el = session.page.query_selector(selector)
        if not el:
            return f"No element found: {selector}"
        text = el.text_content() or ""
        session.last_activity = time.time()
        # Truncate to 10K chars
        if len(text) > 10000:
            text = text[:10000] + "\n... (truncated)"
        return text

    def screenshot(self, conversation_id: str) -> str:
        return self._run_on_worker("_screenshot", conversation_id)

    def _screenshot(self, conversation_id: str) -> str:
        import base64
        session = self._get_session_internal(conversation_id)
        png_bytes = session.page.screenshot(type="png")
        session.last_activity = time.time()

        # Store in FileStore
        from core.file_store import FileStore
        file_id = FileStore.instance().store("screenshot.png", png_bytes,
                                              content_type="image/png")
        b64 = base64.b64encode(png_bytes).decode("ascii")
        return f"Screenshot taken (file_id: {file_id}, size: {len(png_bytes)} bytes, base64 length: {len(b64)})"

    def scroll(self, conversation_id: str, direction: str = "down") -> str:
        return self._run_on_worker("_scroll", conversation_id, direction)

    def _scroll(self, conversation_id: str, direction: str) -> str:
        session = self._get_session_internal(conversation_id)
        pixels = 500 if direction == "down" else -500
        session.page.evaluate(f"window.scrollBy(0, {pixels})")
        session.last_activity = time.time()
        return f"Scrolled {direction}"

    def wait_for(self, conversation_id: str, selector: str, timeout_ms: int = 5000) -> str:
        return self._run_on_worker("_wait_for", conversation_id, selector, timeout_ms)

    def _wait_for(self, conversation_id: str, selector: str, timeout_ms: int) -> str:
        session = self._get_session_internal(conversation_id)
        session.page.wait_for_selector(selector, timeout=timeout_ms)
        session.last_activity = time.time()
        return f"Element found: {selector}"

    def _get_session_internal(self, conversation_id: str) -> BrowserSession:
        """Get session, must be called from worker thread."""
        with self._session_lock:
            session = self._sessions.get(conversation_id)
        if not session:
            self._create_session(conversation_id)
            with self._session_lock:
                session = self._sessions[conversation_id]
        return session

    def shutdown(self):
        """Stop the browser service."""
        self._stop_event.set()
        if self._worker_thread and self._worker_thread.is_alive():
            self._worker_thread.join(timeout=10)
        self._started = False
