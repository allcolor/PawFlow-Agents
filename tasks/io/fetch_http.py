# FetchHTTP Task

"""
Tâche FetchHTTP - Effectuer des requêtes HTTP avec scraping intelligent.

Pour les requêtes GET, utilise Scrapling (anti-bot, JS rendering, auto-escalation).
Pour POST/PUT/PATCH/DELETE ou quand Scrapling n'est pas disponible, fallback urllib.
"""

import json
import logging
import re
import subprocess
import sys
from typing import Dict, Any, List
from core import FlowFile, TaskFactory, TaskError
from core.base_task import BaseTask

logger = logging.getLogger(__name__)

# Minimal GDPR consent cookies to bypass European consent walls
_GDPR_COOKIES = {
    "authId": "anonymous",
    "didomi_token": (
        "eyJ1c2VyX2lkIjoiIiwiY3JlYXRlZCI6IjIwMjQtMDEtMDFUMDA6M"
        "DA6MDAuMDAwWiIsInVwZGF0ZWQiOiIyMDI0LTAxLTAxVDAwOjAwOjAw"
        "LjAwMFoiLCJ2ZW5kb3JzIjp7ImVuYWJsZWQiOltdfSwicHVycG9zZXM"
        "iOnsiZW5hYmxlZCI6W119fQ=="
    ),
    "euconsent-v2": "CQ",
    "consentUUID": "anonymous",
}


class FetchHTTPTask(BaseTask):
    """Effectuer une requête HTTP avec scraping intelligent.

    GET requests use Scrapling for anti-bot handling, JS rendering,
    and auto-escalation (fast → stealth when JS wall detected).
    POST/PUT/PATCH/DELETE use urllib (body transmission needed).
    """

    TYPE = "fetchHTTP"
    VERSION = "2.0.0"
    NAME = "FetchHTTP"
    DESCRIPTION = "Requête HTTP avec scraping intelligent (anti-bot, JS rendering)"
    ICON = "globe"

    def __init__(self, config: Dict[str, Any]):
        super().__init__(config)
        self.url = self.config.get('url', '')
        self.method = self.config.get('method', 'GET').upper()
        self.headers = self.config.get('headers', {})
        self.timeout = int(self.config.get('timeout', 30))
        self.body_source = self.config.get('body_source', 'none')
        self.mode = self.config.get('mode', 'auto')
        self.selector = self.config.get('selector', '')
        self.output_format = self.config.get('output_format', 'raw')

    def execute(self, flowfile: FlowFile) -> List[FlowFile]:
        url = self._resolve_value(flowfile, self.url)
        if not url:
            raise TaskError("Le paramètre 'url' est requis.")

        headers = {k: self._resolve_value(flowfile, str(v))
                   for k, v in self.headers.items()}

        # POST/PUT/PATCH/DELETE → urllib (need to send body)
        if self.method != 'GET':
            return self._fetch_urllib(flowfile, url, headers)

        # GET → Scrapling with smart escalation
        return self._fetch_scrapling(flowfile, url, headers)

    # -- Scrapling path (GET) --

    def _fetch_scrapling(self, flowfile: FlowFile, url: str,
                         headers: Dict[str, str]) -> List[FlowFile]:
        """Fetch via Scrapling with auto-escalation."""
        mode = self.mode
        selector = self._resolve_value(flowfile, self.selector)

        try:
            page = None
            fast_err = None

            # Step 1: fast mode
            if mode in ('auto', 'fast'):
                try:
                    from scrapling import Fetcher
                    kwargs = {"timeout": self.timeout, "verify": False,
                              "cookies": _GDPR_COOKIES}
                    if headers:
                        kwargs["headers"] = headers
                    page = Fetcher.get(url, **kwargs)
                except Exception as e:
                    fast_err = e
                    logger.debug(f"scrapling fast failed for {url}: {e}")

            # Step 2: PDF detection
            if page is not None and self._is_pdf(page, url):
                content = self._extract_pdf_bytes(page, url)
                flowfile.set_content(content)
                flowfile.set_attribute('http.status.code',
                                      str(getattr(page, 'status', 200)))
                flowfile.set_attribute('mime.type', 'application/pdf')
                self._set_common_attrs(flowfile, url, len(content))
                return [flowfile]

            # Step 3: Check for JS wall → escalate
            needs_stealth = (page is None) or (mode == 'stealth')
            if page is not None and not needs_stealth:
                text = self._extract_text(page, selector)
                if self._looks_like_js_wall(text, page):
                    logger.info(f"fetchHTTP: JS wall detected for {url}, "
                                f"escalating to stealth")
                    needs_stealth = True
                else:
                    return self._build_result(flowfile, url, page, text,
                                              selector)

            # Step 4: stealth via subprocess
            if needs_stealth:
                stealth_text = self._stealth_subprocess(url, selector)
                if stealth_text is not None:
                    flowfile.set_content(stealth_text.encode('utf-8'))
                    flowfile.set_attribute('http.status.code', '200')
                    flowfile.set_attribute('scraping.mode', 'stealth')
                    self._set_common_attrs(flowfile, url,
                                           len(stealth_text))
                    return [flowfile]
                # Stealth failed — use fast result if available
                if page is not None:
                    text = self._extract_text(page, selector)
                    return self._build_result(flowfile, url, page, text,
                                              selector)

            if fast_err:
                raise fast_err
            raise TaskError(f"Impossible de récupérer {url}")

        except ImportError:
            # Scrapling not installed → fallback to urllib
            logger.info("scrapling not installed, falling back to urllib")
            return self._fetch_urllib(flowfile, url, headers)
        except TaskError:
            raise
        except Exception as e:
            logger.warning(f"scrapling failed for {url}: {e}")
            # Fallback to urllib on any scrapling error
            try:
                return self._fetch_urllib(flowfile, url, headers)
            except TaskError:
                raise
            except Exception:
                raise TaskError(f"Erreur HTTP: {e}")

    def _build_result(self, flowfile: FlowFile, url: str, page,
                      text: str, selector: str) -> List[FlowFile]:
        """Build FlowFile result from scrapling page."""
        if self.output_format == 'raw':
            # Return raw HTML/body
            raw = (page.html_content if hasattr(page, 'html_content')
                   else str(page))
            content = raw.encode('utf-8') if isinstance(raw, str) else raw
        else:
            content = text.encode('utf-8')

        flowfile.set_content(content)
        status = str(getattr(page, 'status', 200))
        flowfile.set_attribute('http.status.code', status)
        flowfile.set_attribute('scraping.mode', 'fast')
        if selector:
            flowfile.set_attribute('scraping.selector', selector)
        self._set_common_attrs(flowfile, url, len(content))
        return [flowfile]

    # -- urllib path (POST/PUT/PATCH/DELETE) --

    def _fetch_urllib(self, flowfile: FlowFile, url: str,
                      headers: Dict[str, str]) -> List[FlowFile]:
        """Standard HTTP fetch via urllib (for methods with body)."""
        from urllib.request import Request, urlopen
        from urllib.error import URLError, HTTPError

        body = None
        if self.method in ('POST', 'PUT', 'PATCH'):
            if self.body_source == 'flowfile':
                body = flowfile.get_content()
            elif self.body_source == 'config':
                body = self.config.get('body', '').encode('utf-8')

        try:
            req = Request(url, data=body, headers=headers, method=self.method)
            with urlopen(req, timeout=self.timeout) as response:
                content = response.read()
                status_code = response.status
                response_headers = dict(response.getheaders())

            flowfile.set_content(content)
            flowfile.set_attribute('http.status.code', str(status_code))
            content_type = response_headers.get('Content-Type', '')
            if content_type:
                flowfile.set_attribute('mime.type', content_type)
            self._set_common_attrs(flowfile, url, len(content))
            return [flowfile]

        except HTTPError as e:
            error_body = e.read() if hasattr(e, 'read') else b''
            flowfile.set_content(error_body)
            flowfile.set_attribute('http.status.code', str(e.code))
            flowfile.set_attribute('http.error', str(e.reason))
            raise TaskError(f"HTTP {e.code}: {e.reason}")
        except URLError as e:
            raise TaskError(f"Erreur de connexion: {e.reason}")
        except Exception as e:
            raise TaskError(f"Erreur HTTP: {e}")

    # -- Helpers --

    def _set_common_attrs(self, flowfile: FlowFile, url: str, size: int):
        flowfile.set_attribute('http.url', url)
        flowfile.set_attribute('http.method', self.method)
        flowfile.set_attribute('fileSize', str(size))

    @staticmethod
    def _extract_text(page, selector: str) -> str:
        if selector:
            elements = page.css(selector)
            if not elements:
                return f"No elements found for selector '{selector}'"
            texts = [el.get_all_text() if hasattr(el, 'get_all_text')
                     else str(el.text) for el in elements]
            return "\n---\n".join(t for t in texts if t.strip())
        return page.get_all_text(separator="\n", strip=True)

    @staticmethod
    def _is_pdf(page, url: str) -> bool:
        content_type = ""
        if hasattr(page, 'headers') and page.headers:
            ct = (page.headers.get('content-type', '')
                  if isinstance(page.headers, dict) else '')
            content_type = ct.lower().split(';')[0].strip() if ct else ''
        return (content_type == 'application/pdf'
                or url.rstrip('/').lower().endswith('.pdf'))

    @staticmethod
    def _extract_pdf_bytes(page, url: str) -> bytes:
        """Get raw PDF bytes from a scrapling page."""
        if hasattr(page, 'body') and page.body:
            raw = page.body
            return raw if isinstance(raw, bytes) else raw.encode(
                'latin-1', errors='replace')
        # Download directly if scrapling didn't give us bytes
        from urllib.request import urlopen as _urlopen
        with _urlopen(url, timeout=30) as resp:
            return resp.read()

    @staticmethod
    def _looks_like_js_wall(text: str, page) -> bool:
        stripped = text.strip()
        if not stripped:
            return True
        if len(stripped) < 200:
            html = ""
            if hasattr(page, 'html'):
                html = str(page.html) if page.html else ""
            elif hasattr(page, 'body'):
                html = page.body if isinstance(page.body, str) else ""
            if html and html.lower().count('<script') > 3:
                return True
        lower = stripped.lower()
        challenge_signs = [
            "checking your browser", "just a moment", "cloudflare",
            "enable javascript", "please turn javascript on",
            "ray id", "attention required",
        ]
        return any(sign in lower for sign in challenge_signs)

    def _stealth_subprocess(self, url: str, selector: str):
        """Run stealth fetch in a subprocess to avoid Playwright asyncio issues."""
        script = (
            'import sys, json\n'
            'try:\n'
            '    from scrapling import StealthyFetcher\n'
            '    url = sys.argv[1]\n'
            '    selector = sys.argv[2] if len(sys.argv) > 2 '
            'and sys.argv[2] else ""\n'
            '    page = StealthyFetcher.fetch(url, headless=True, '
            'timeout=30000)\n'
            '    if selector:\n'
            '        elements = page.css(selector)\n'
            '        if not elements:\n'
            '            text = ""\n'
            '        else:\n'
            '            texts = [el.get_all_text() if hasattr(el, '
            '"get_all_text")\n'
            '                     else str(el.text) for el in elements]\n'
            '            text = "\\n---\\n".join(t for t in texts '
            'if t.strip())\n'
            '    else:\n'
            '        text = page.get_all_text(separator="\\n", '
            'strip=True)\n'
            '    print(json.dumps({"ok": True, "text": text}))\n'
            'except Exception as e:\n'
            '    print(json.dumps({"ok": False, '
            '"error": f"{type(e).__name__}: {e}"}))\n'
        )
        try:
            args = [sys.executable, "-c", script, url]
            if selector:
                args.append(selector)
            proc = subprocess.run(
                args, capture_output=True, text=True, timeout=60,
            )
            if proc.returncode != 0:
                logger.warning(f"stealth subprocess failed for {url}: "
                               f"{proc.stderr[:500]}")
                return None
            output = proc.stdout.strip()
            if not output:
                return None
            result = json.loads(output.split('\n')[-1])
            if result.get("ok"):
                text = result.get("text", "")
                return text if text else None
            logger.warning(f"stealth subprocess error for {url}: "
                           f"{result.get('error')}")
            return None
        except Exception as e:
            logger.warning(f"stealth subprocess exception for {url}: {e}")
            return None

    def _resolve_value(self, flowfile: FlowFile, value: str) -> str:
        """Resolve ${attribute} and ${X} expressions."""
        if not value or '${' not in value:
            return value
        return self.resolve_value(value, flowfile=flowfile)

    def get_parameter_schema(self) -> Dict[str, Any]:
        return {
            'url': {
                'type': 'string', 'required': True,
                'description': 'URL de la requête (supporte ${attribut})',
            },
            'method': {
                'type': 'select', 'required': False, 'default': 'GET',
                'options': ['GET', 'POST', 'PUT', 'DELETE', 'PATCH'],
                'description': 'Méthode HTTP',
            },
            'mode': {
                'type': 'select', 'required': False, 'default': 'auto',
                'options': ['auto', 'fast', 'stealth'],
                'description': 'Mode de fetch GET: auto (fast + escalation), fast (HTTP only), stealth (anti-bot)',
            },
            'selector': {
                'type': 'string', 'required': False, 'default': '',
                'description': 'CSS selector pour extraire des éléments spécifiques',
            },
            'output_format': {
                'type': 'select', 'required': False, 'default': 'raw',
                'options': ['raw', 'text'],
                'description': "Format de sortie: raw (HTML/body brut) ou text (texte extrait)",
            },
            'headers': {
                'type': 'map', 'required': False,
                'description': 'En-têtes HTTP (clé → valeur)',
            },
            'timeout': {
                'type': 'integer', 'required': False, 'default': 30,
                'description': 'Timeout en secondes',
            },
            'body_source': {
                'type': 'select', 'required': False, 'default': 'none',
                'options': ['none', 'flowfile', 'config'],
                'description': 'Source du body pour POST/PUT/PATCH',
            },
            'body': {
                'type': 'string', 'required': False,
                'description': 'Body de la requête (si body_source=config)',
            },
        }


TaskFactory.register(FetchHTTPTask)
