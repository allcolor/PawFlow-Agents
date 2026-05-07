"""Private Gateway \u2014 pre-authentication access gate.

When enabled, every HTTP request must first pass a secret challenge
before reaching the login page. Secrets are stored as global secrets
with the prefix ``privategateway.`` (e.g. ${privategateway.mykey}).

IP-based rate-limiting and banning:
- Exponential cooldown on failed attempts (1s, 3s, 10s, 30s).
- After 5 consecutive failures the IP is banned for 24 h.
- All requests from banned IPs are rejected immediately.

The "passed" state is tracked via an HMAC-signed cookie
that survives logout/login cycles.

Toggle: global parameter ``private_gateway_enabled`` ("true" / "false").
"""

import hashlib
import hmac
import json
import logging
import threading
import time
from pathlib import Path
from typing import Dict

logger = logging.getLogger(__name__)

_COOKIE_NAME = "_pf_gw"
_COOKIE_MAX_AGE = 30 * 86400  # 30 days


def _signing_key() -> bytes:
    from core.secrets import get_secrets_manager
    return get_secrets_manager().derive_subkey(b"private-gateway-cookie")


def _make_cookie_value(ip: str) -> str:
    ts = str(int(time.time()))
    payload = f"{ts}:{ip}"
    sig = hmac.new(_signing_key(), payload.encode(), hashlib.sha256).hexdigest()[:32]
    return f"{ts}.{sig}"


def _verify_cookie(value: str, ip: str) -> bool:
    try:
        ts_str, sig = value.split(".", 1)
        ts = int(ts_str)
        if time.time() - ts > _COOKIE_MAX_AGE:
            return False
        payload = f"{ts_str}:{ip}"
        expected = hmac.new(_signing_key(), payload.encode(), hashlib.sha256).hexdigest()[:32]
        return hmac.compare_digest(sig, expected)
    except Exception:
        return False


_ip_state: Dict[str, dict] = {}
_lock = threading.Lock()
_COOLDOWNS = [0, 1, 3, 10, 30]
_MAX_FAILURES = 5
_BAN_DURATION = 24 * 3600
import core.paths as _paths


def _save_bans():
    """Persist banned IPs to disk. Call with _lock held."""
    now = time.time()
    bans = {ip: st for ip, st in _ip_state.items() if st.get("banned_until", 0) > now}
    try:
        _paths.GATEWAY_BANS_FILE.parent.mkdir(parents=True, exist_ok=True)
        _paths.GATEWAY_BANS_FILE.write_text(json.dumps(bans), encoding="utf-8")
    except Exception as e:
        logger.warning("Failed to save gateway bans: %s", e)


def _load_bans():
    """Load banned IPs from disk on startup.

    Skips entries for IPs that should never have been banned in the first
    place (loopback / RFC1918 / docker bridge — see _is_local_or_private).
    Pre-fix releases banned the user's LAN IP after the codex MCP bridge
    cookie-auth cascade; clean those up at boot so the rule kicks in
    even for already-persisted bans.
    """
    if not _paths.GATEWAY_BANS_FILE.exists():
        return
    try:
        data = json.loads(_paths.GATEWAY_BANS_FILE.read_text(encoding="utf-8"))
        now = time.time()
        skipped_local = 0
        with _lock:
            for ip, st in data.items():
                if st.get("banned_until", 0) <= now:
                    continue
                if _is_local_or_private(ip):
                    skipped_local += 1
                    continue
                _ip_state[ip] = st
        if skipped_local:
            logger.info(
                "Discarded %d stale local/docker-IP ban(s) at boot "
                "(local IPs are no longer ban-eligible).", skipped_local)
            # Rewrite the file so the discarded entries don't reappear.
            _save_bans()
        logger.info("Loaded %d gateway ban(s) from disk", len(_ip_state))
    except Exception as e:
        logger.warning("Failed to load gateway bans: %s", e)


# `_is_local_or_private` is defined below — forward-declare via a stub so
# the boot-time `_load_bans()` call can reach it before the real impl.
def _is_local_or_private(ip: str) -> bool:  # noqa: F811  (real impl below)
    if not ip:
        return True
    try:
        import ipaddress
        addr = ipaddress.ip_address(ip)
        return (addr.is_loopback or addr.is_private or addr.is_link_local
                or addr.is_reserved or addr.is_unspecified)
    except (ValueError, TypeError):
        return True


_load_bans()


def _is_local_or_private(ip: str) -> bool:
    """True for IPs that PawFlow's gateway must NEVER ban: loopback,
    RFC1918 private ranges (10/8, 172.16/12, 192.168/16), CGNAT
    (100.64/10), link-local (169.254/16), IPv6 loopback / link-local /
    ULA. Server-spawned components — the CC / codex / gemini Docker
    containers, the user's relay running on the LAN, anything in the
    docker bridge subnet — all live on these ranges and a failed auth
    attempt from one of them must not lock out everything else on the
    same source IP.

    Public IPs (failed attempts from the open internet) still get
    banned per the original 5-failures → 24h policy.
    """
    if not ip:
        return True  # missing addr — treat as local, don't ban anything
    try:
        import ipaddress
        addr = ipaddress.ip_address(ip)
        return (
            addr.is_loopback
            or addr.is_private
            or addr.is_link_local
            or addr.is_reserved
            or addr.is_unspecified
        )
    except (ValueError, TypeError):
        # Unparseable — be safe and DON'T ban (better to miss a ban than
        # to lock out a legit user with a weird X-Forwarded-For header).
        return True


def _get_ip_state(ip: str) -> dict:
    with _lock:
        if ip not in _ip_state:
            _ip_state[ip] = {"failures": 0, "last_attempt": 0.0, "banned_until": 0.0}
        return _ip_state[ip]


def is_banned(ip: str) -> bool:
    # Local / docker-bridge / RFC1918 IPs are never banned — see
    # `_is_local_or_private` for the policy rationale.
    if _is_local_or_private(ip):
        return False
    with _lock:
        st = _ip_state.get(ip)
        if not st:
            return False
        if st["banned_until"] > time.time():
            return True
        if st["banned_until"] > 0:
            st["failures"] = 0
            st["banned_until"] = 0.0
        return False


def get_cooldown_remaining(ip: str) -> float:
    if _is_local_or_private(ip):
        return 0.0
    st = _get_ip_state(ip)
    if st["failures"] <= 0:
        return 0.0
    idx = min(st["failures"], len(_COOLDOWNS)) - 1
    cooldown = _COOLDOWNS[idx]
    remaining = (st["last_attempt"] + cooldown) - time.time()
    return max(0.0, remaining)


def record_failure(ip: str):
    # Local / docker-bridge / RFC1918 IPs are never recorded as failures.
    # A failed auth from a server-spawned MCP bridge or the user's
    # LAN relay must not pollute the ban counter — doing so would
    # eventually lock out every legitimate component sharing that IP.
    if _is_local_or_private(ip):
        return
    with _lock:
        st = _ip_state.setdefault(ip, {"failures": 0, "last_attempt": 0.0, "banned_until": 0.0})
        st["failures"] += 1
        st["last_attempt"] = time.time()
        if st["failures"] >= _MAX_FAILURES:
            st["banned_until"] = time.time() + _BAN_DURATION
            logger.warning("Private gateway: banned IP %s for 24h after %d failures",
                           ip, st["failures"])
            _save_bans()


def record_success(ip: str):
    with _lock:
        was_banned = _ip_state.pop(ip, {}).get("banned_until", 0) > time.time()
        if was_banned:
            _save_bans()


def list_bans() -> list:
    now = time.time()
    with _lock:
        return [
            {"ip": ip, "banned_until": st["banned_until"],
             "failures": st["failures"]}
            for ip, st in _ip_state.items()
            if st["banned_until"] > now
        ]


def unban_ip(ip: str) -> bool:
    with _lock:
        st = _ip_state.pop(ip, None)
        was_banned = st is not None and st.get("banned_until", 0) > time.time()
        if was_banned:
            _save_bans()
        return was_banned


def _load_gateway_secrets() -> Dict[str, str]:
    from core.config_store import ConfigStore
    from core.paths import GLOBAL_SECRETS_FILE
    secrets_file = GLOBAL_SECRETS_FILE
    all_secrets = ConfigStore.load_secrets(secrets_file)
    return {k: str(v) for k, v in all_secrets.items() if k.startswith("privategateway.")}


def verify_secret(submitted: str) -> bool:
    gw_secrets = _load_gateway_secrets()
    if not gw_secrets:
        logger.warning("Private gateway enabled but no privategateway.* secrets found")
        return False
    for _name, value in gw_secrets.items():
        if hmac.compare_digest(submitted.strip().encode('utf-8'), value.strip().encode('utf-8')):
            return True
    return False


def is_enabled() -> bool:
    try:
        from core.expression import _load_global_parameters
        params = _load_global_parameters()
        val = str(params.get("private_gateway_enabled", "false")).strip().lower()
        return val in ("true", "1", "yes")
    except Exception:
        return False


_CHALLENGE_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Access</title>
<style>
  * { margin: 0; padding: 0; box-sizing: border-box; }
  body {
    font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;
    background: #0a0a0a; color: #ccc;
    display: flex; align-items: center; justify-content: center;
    min-height: 100vh;
  }
  .box {
    background: #141414; border: 1px solid #222; border-radius: 8px;
    padding: 2rem; width: 360px;
  }
  input[type=password] {
    width: 100%%; padding: .6rem .8rem; font-size: .95rem;
    background: #1a1a1a; border: 1px solid #333; border-radius: 4px;
    color: #eee; margin-bottom: 1rem; outline: none;
  }
  input[type=password]:focus { border-color: #555; }
  button {
    width: 100%%; padding: .6rem; font-size: .95rem;
    background: #2563eb; color: #fff; border: none; border-radius: 4px;
    cursor: pointer;
  }
  button:hover { background: #1d4ed8; }
  button:disabled { background: #333; cursor: not-allowed; color: #666; }
  .err { color: #ef4444; font-size: .85rem; margin-bottom: .8rem; min-height: 1.2em; }
  .cd { color: #888; font-size: .85rem; text-align: center; margin-top: .8rem; }
</style>
</head>
<body>
<div class="box">
  <form method="POST" action="/_gateway" id="f">
    <input type="hidden" name="next" value="%(next_url)s">
    <input type="password" name="secret" id="s" placeholder="Access key" autocomplete="off" autofocus>
    <div class="err" id="e">%(error)s</div>
    <button type="submit" id="b">Enter</button>
  </form>
  <div class="cd" id="cd"></div>
</div>
<script>
(function(){
  var cd = %(cooldown)d, b = document.getElementById('b'),
      cdEl = document.getElementById('cd');
  function tick() {
    if (cd <= 0) { b.disabled = false; cdEl.textContent = ''; return; }
    b.disabled = true;
    cdEl.textContent = 'Wait ' + Math.ceil(cd) + 's';
    cd--;
    setTimeout(tick, 1000);
  }
  tick();
})();
</script>
</body>
</html>"""


_SKIN_GOOGLE = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Google</title>
<style>
  * { margin: 0; padding: 0; box-sizing: border-box; }
  body { font-family: arial, sans-serif; background: #fff; display: flex;
         flex-direction: column; align-items: center; justify-content: center;
         min-height: 100vh; }
  .logo { font-size: 92px; font-weight: 400; margin-bottom: 20px; }
  .logo span:nth-child(1) { color: #4285f4; }
  .logo span:nth-child(2) { color: #ea4335; }
  .logo span:nth-child(3) { color: #fbbc05; }
  .logo span:nth-child(4) { color: #4285f4; }
  .logo span:nth-child(5) { color: #34a853; }
  .logo span:nth-child(6) { color: #ea4335; }
  .search-box { display: flex; align-items: center; width: 580px; max-width: 90vw;
                 border: 1px solid #dfe1e5; border-radius: 24px; padding: 5px 14px;
                 box-shadow: 0 1px 6px rgba(32,33,36,.28); }
  .search-box:hover { box-shadow: 0 1px 6px rgba(32,33,36,.4); }
  .search-box svg { flex-shrink: 0; fill: #9aa0a6; }
  .search-box input { flex: 1; border: none; outline: none; font-size: 16px;
                       padding: 10px 8px; background: transparent; color: #202124; }
  .btns { margin-top: 18px; display: flex; gap: 12px; }
  .btns button { background: #f8f9fa; border: 1px solid #f8f9fa; border-radius: 4px;
                  font-size: 14px; color: #3c4043; padding: 8px 16px; cursor: pointer; }
  .btns button:hover { border-color: #dadce0; box-shadow: 0 1px 1px rgba(0,0,0,.1); }
  .err { color: #ea4335; font-size: 13px; margin-top: 8px; min-height: 1.2em; }
  .cd { color: #70757a; font-size: 13px; margin-top: 6px; }
</style>
</head>
<body>
<div class="logo">
  <span>G</span><span>o</span><span>o</span><span>g</span><span>l</span><span>e</span>
</div>
<form method="POST" action="/_gateway" id="f">
  <input type="hidden" name="next" value="%(next_url)s">
  <div class="search-box">
    <svg xmlns="http://www.w3.org/2000/svg" width="20" height="20" viewBox="0 0 24 24">
      <path d="M15.5 14h-.79l-.28-.27A6.47 6.47 0 0016 9.5 6.5 6.5 0 109.5 16c1.61 0 3.09-.59 4.23-1.57l.27.28v.79l5 4.99L20.49 19l-4.99-5zm-6 0C7.01 14 5 11.99 5 9.5S7.01 5 9.5 5 14 7.01 14 9.5 11.99 14 9.5 14z"/>
    </svg>
    <input type="text" name="secret" id="s" autocomplete="off" autofocus>
  </div>
  <div class="err" id="e">%(error)s</div>
  <div class="btns">
    <button type="submit" id="b">Google Search</button>
    <button type="button" onclick="document.getElementById('s').value='';document.getElementById('s').focus()">I'm Feeling Lucky</button>
  </div>
</form>
<div class="cd" id="cd"></div>
<script>
(function(){
  var cd = %(cooldown)d, b = document.getElementById('b'),
      cdEl = document.getElementById('cd');
  function tick() {
    if (cd <= 0) { b.disabled = false; cdEl.textContent = ''; return; }
    b.disabled = true;
    cdEl.textContent = 'Please wait ' + Math.ceil(cd) + 's';
    cd--;
    setTimeout(tick, 1000);
  }
  tick();
})();
</script>
</body>
</html>"""

_SKIN_WIFI = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Wi-Fi Login</title>
<style>
  * { margin: 0; padding: 0; box-sizing: border-box; }
  body { font-family: 'Segoe UI', Tahoma, sans-serif; background: linear-gradient(135deg, #667eea 0%%, #764ba2 100%%);
         display: flex; align-items: center; justify-content: center; min-height: 100vh; }
  .card { background: #fff; border-radius: 12px; padding: 40px 32px; width: 380px;
          box-shadow: 0 20px 60px rgba(0,0,0,.3); text-align: center; }
  .wifi-icon { font-size: 48px; margin-bottom: 12px; }
  h1 { font-size: 20px; color: #333; margin-bottom: 4px; }
  .sub { color: #888; font-size: 13px; margin-bottom: 24px; }
  input[type=text] { width: 100%%; padding: 12px 16px; font-size: 15px; border: 2px solid #e0e0e0;
                     border-radius: 8px; outline: none; margin-bottom: 16px; }
  input:focus { border-color: #667eea; }
  button { width: 100%%; padding: 12px; font-size: 16px; font-weight: 600;
           background: linear-gradient(135deg, #667eea, #764ba2); color: #fff;
           border: none; border-radius: 8px; cursor: pointer; }
  button:hover { opacity: .9; }
  button:disabled { opacity: .4; cursor: not-allowed; }
  .err { color: #e74c3c; font-size: 13px; margin-bottom: 12px; min-height: 1.2em; }
  .cd { color: #999; font-size: 12px; margin-top: 12px; }
  .footer { color: #bbb; font-size: 11px; margin-top: 20px; }
</style>
</head>
<body>
<div class="card">
  <div class="wifi-icon">\U0001F4F6</div>
  <h1>Welcome to Free Wi-Fi</h1>
  <div class="sub">Enter access code to connect</div>
  <form method="POST" action="/_gateway" id="f">
    <input type="hidden" name="next" value="%(next_url)s">
    <input type="text" name="secret" id="s" placeholder="Access code" autocomplete="off" autofocus>
    <div class="err" id="e">%(error)s</div>
    <button type="submit" id="b">\U0001F310 Connect to Internet</button>
  </form>
  <div class="cd" id="cd"></div>
  <div class="footer">Powered by NetConnect\u2122 Hospitality Solutions</div>
</div>
<script>
(function(){
  var cd = %(cooldown)d, b = document.getElementById('b'),
      cdEl = document.getElementById('cd');
  function tick() {
    if (cd <= 0) { b.disabled = false; cdEl.textContent = ''; return; }
    b.disabled = true;
    cdEl.textContent = 'Connection cooldown: ' + Math.ceil(cd) + 's';
    cd--;
    setTimeout(tick, 1000);
  }
  tick();
})();
</script>
</body>
</html>"""

_SKIN_TERMINAL = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>login</title>
<style>
  * { margin: 0; padding: 0; box-sizing: border-box; }
  body { font-family: 'Courier New', monospace; background: #000; color: #33ff33;
         display: flex; align-items: center; justify-content: center; min-height: 100vh; }
  .term { width: 600px; max-width: 95vw; padding: 20px; }
  .line { white-space: pre; line-height: 1.6; font-size: 14px; }
  .prompt { display: flex; align-items: center; }
  .prompt span { white-space: pre; }
  .prompt input { flex: 1; background: transparent; border: none; outline: none;
                  color: #33ff33; font-family: inherit; font-size: 14px;
                  caret-color: #33ff33; }
  .err { color: #ff3333; }
  .cd { color: #888; font-size: 12px; margin-top: 8px; }
  @keyframes blink { 50%% { opacity: 0; } }
  .cursor { animation: blink 1s step-end infinite; }
</style>
</head>
<body>
<div class="term">
  <div class="line">Linux server 5.15.0-generic #1 SMP x86_64 GNU/Linux</div>
  <div class="line">&nbsp;</div>
  <div class="line">Last login: Thu Jan  1 00:00:00 1970 from 127.0.0.1</div>
  <div class="line">&nbsp;</div>
  <div class="line">$ sudo su -</div>
  <div class="line err" id="e">%(error)s</div>
  <form method="POST" action="/_gateway" id="f">
    <input type="hidden" name="next" value="%(next_url)s">
    <div class="prompt">
      <span>[sudo] password: </span>
      <input type="password" name="secret" id="s" autocomplete="off" autofocus>
    </div>
  </form>
  <div class="cd" id="cd"></div>
</div>
<script>
(function(){
  var cd = %(cooldown)d, cdEl = document.getElementById('cd'),
      inp = document.getElementById('s');
  function tick() {
    if (cd <= 0) { inp.disabled = false; cdEl.textContent = ''; return; }
    inp.disabled = true;
    cdEl.textContent = 'Account locked. Retry in ' + Math.ceil(cd) + 's';
    cd--;
    setTimeout(tick, 1000);
  }
  tick();
  document.getElementById('f').addEventListener('submit', function(e) {
    if (inp.disabled) e.preventDefault();
  });
})();
</script>
</body>
</html>"""

_SKIN_NETFLIX = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Netflix</title>
<style>
  * { margin: 0; padding: 0; box-sizing: border-box; }
  body { font-family: 'Helvetica Neue', Helvetica, Arial, sans-serif;
         background: #000 url('data:image/svg+xml,<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 1 1"><rect fill="%%23141414"/></svg>');
         color: #fff; display: flex; align-items: center; justify-content: center; min-height: 100vh; }
  .card { background: rgba(0,0,0,.75); border-radius: 4px; padding: 60px 68px 40px;
          width: 450px; max-width: 95vw; }
  .logo { color: #e50914; font-size: 36px; font-weight: 700; margin-bottom: 28px; letter-spacing: -1px; }
  h1 { font-size: 32px; font-weight: 700; margin-bottom: 28px; }
  input[type=password] { width: 100%%; padding: 16px 20px; font-size: 16px;
                         background: #333; border: none; border-radius: 4px; color: #fff;
                         margin-bottom: 16px; outline: none; }
  input::placeholder { color: #8c8c8c; }
  input:focus { background: #454545; }
  button { width: 100%%; padding: 16px; font-size: 16px; font-weight: 700;
           background: #e50914; color: #fff; border: none; border-radius: 4px; cursor: pointer;
           margin-bottom: 12px; }
  button:hover { background: #f40612; }
  button:disabled { background: #555; cursor: not-allowed; }
  .err { color: #e87c03; font-size: 14px; margin-bottom: 16px; min-height: 1.2em; }
  .cd { color: #737373; font-size: 13px; margin-top: 8px; }
  .footer { color: #737373; font-size: 13px; margin-top: 16px; }
  a { color: #fff; text-decoration: none; }
</style>
</head>
<body>
<div class="card">
  <div class="logo">NETFLIX</div>
  <h1>Sign In</h1>
  <form method="POST" action="/_gateway" id="f">
    <input type="hidden" name="next" value="%(next_url)s">
    <input type="password" name="secret" id="s" placeholder="Password" autocomplete="off" autofocus>
    <div class="err" id="e">%(error)s</div>
    <button type="submit" id="b">Sign In</button>
  </form>
  <div class="cd" id="cd"></div>
  <div class="footer">New to Netflix? <a href="#">Sign up now</a>.</div>
</div>
<script>
(function(){
  var cd = %(cooldown)d, b = document.getElementById('b'),
      cdEl = document.getElementById('cd');
  function tick() {
    if (cd <= 0) { b.disabled = false; cdEl.textContent = ''; return; }
    b.disabled = true;
    cdEl.textContent = 'Too many attempts. Try again in ' + Math.ceil(cd) + 's';
    cd--;
    setTimeout(tick, 1000);
  }
  tick();
})();
</script>
</body>
</html>"""

_SKIN_CAPTCHA = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Security Check</title>
<style>
  * { margin: 0; padding: 0; box-sizing: border-box; }
  body { font-family: Arial, sans-serif; background: #f0f0f0;
         display: flex; align-items: center; justify-content: center; min-height: 100vh; }
  .card { background: #fff; border: 1px solid #d3d3d3; border-radius: 3px;
          padding: 24px; width: 340px; box-shadow: 0 2px 4px rgba(0,0,0,.1); }
  h2 { font-size: 16px; color: #4a4a4a; margin-bottom: 16px; }
  .captcha-box { background: #f9f9f9; border: 2px solid #d3d3d3; border-radius: 4px;
                  padding: 12px; margin-bottom: 16px; display: flex; align-items: center; gap: 12px; }
  .captcha-box .check { width: 28px; height: 28px; border: 2px solid #c1c1c1; border-radius: 3px;
                        background: #fff; cursor: pointer; display: flex; align-items: center;
                        justify-content: center; font-size: 18px; color: #4285f4; }
  .captcha-box .check.checked { background: #4285f4; color: #fff; border-color: #4285f4; }
  .captcha-label { font-size: 14px; color: #555; }
  .recaptcha { font-size: 10px; color: #555; text-align: right; line-height: 1.3; }
  .recaptcha b { color: #888; }
  .field { margin-bottom: 12px; }
  .field label { font-size: 12px; color: #777; display: block; margin-bottom: 4px; }
  .field input { width: 100%%; padding: 8px 10px; font-size: 14px; border: 1px solid #ccc;
                 border-radius: 3px; outline: none; }
  .field input:focus { border-color: #4285f4; }
  button { width: 100%%; padding: 10px; font-size: 14px; background: #4285f4; color: #fff;
           border: none; border-radius: 3px; cursor: pointer; }
  button:hover { background: #3367d6; }
  button:disabled { background: #ccc; cursor: not-allowed; }
  .err { color: #d93025; font-size: 12px; margin-bottom: 8px; min-height: 1em; }
  .cd { color: #999; font-size: 11px; margin-top: 8px; text-align: center; }
</style>
</head>
<body>
<div class="card">
  <h2>Please verify you are human</h2>
  <div class="captcha-box" onclick="var c=this.querySelector('.check');c.classList.toggle('checked');c.textContent=c.classList.contains('checked')?'\\u2713':'';">
    <div class="check"></div>
    <span class="captcha-label">I'm not a robot</span>
    <div class="recaptcha" style="margin-left:auto;">
      <b>reCAPTCHA</b><br>Privacy - Terms
    </div>
  </div>
  <form method="POST" action="/_gateway" id="f">
    <input type="hidden" name="next" value="%(next_url)s">
    <div class="field">
      <label>Verification code</label>
      <input type="text" name="secret" id="s" autocomplete="off" autofocus>
    </div>
    <div class="err" id="e">%(error)s</div>
    <button type="submit" id="b">Verify</button>
  </form>
  <div class="cd" id="cd"></div>
</div>
<script>
(function(){
  var cd = %(cooldown)d, b = document.getElementById('b'),
      cdEl = document.getElementById('cd');
  function tick() {
    if (cd <= 0) { b.disabled = false; cdEl.textContent = ''; return; }
    b.disabled = true;
    cdEl.textContent = 'Verification cooldown: ' + Math.ceil(cd) + 's';
    cd--;
    setTimeout(tick, 1000);
  }
  tick();
})();
</script>
</body>
</html>"""

_SKIN_MATRIX = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>The Matrix</title>
<style>
  * { margin: 0; padding: 0; box-sizing: border-box; }
  body { background: #000; overflow: hidden; font-family: 'Courier New', monospace; }
  canvas { position: fixed; top: 0; left: 0; z-index: 0; }
  .overlay {
    position: fixed; top: 0; left: 0; width: 100%%; height: 100%%;
    display: flex; flex-direction: column; align-items: center; justify-content: center;
    z-index: 1;
  }
  .wake { color: #00ff41; font-size: 24px; margin-bottom: 30px; min-height: 32px;
          text-shadow: 0 0 10px rgba(0,255,65,0.5); }
  .input-row {
    display: flex; align-items: center;
    background: rgba(0,0,0,0.85); border: 1px solid #00ff41;
    border-radius: 4px; padding: 4px; margin-bottom: 20px;
    box-shadow: 0 0 15px rgba(0,255,65,0.15);
  }
  input[type=password] {
    background: transparent; border: none; outline: none;
    color: #00ff41; font-family: 'Courier New', monospace;
    font-size: 16px; padding: 12px 16px; width: 300px;
    caret-color: #00ff41;
  }
  input::placeholder { color: #004d14; }
  .pills { display: flex; gap: 24px; }
  .pill {
    padding: 14px 32px; font-size: 16px; font-weight: bold;
    border: none; border-radius: 24px; cursor: pointer;
    font-family: 'Courier New', monospace;
    text-transform: uppercase; letter-spacing: 2px;
    transition: all 0.3s;
  }
  .red-pill {
    background: linear-gradient(135deg, #cc0000, #ff0000);
    color: #fff; box-shadow: 0 0 20px rgba(255,0,0,0.4);
  }
  .red-pill:hover { box-shadow: 0 0 40px rgba(255,0,0,0.7); transform: scale(1.05); }
  .red-pill:disabled { background: #333; box-shadow: none; cursor: not-allowed; transform: none; }
  .blue-pill {
    background: linear-gradient(135deg, #0044cc, #0066ff);
    color: #fff; box-shadow: 0 0 20px rgba(0,100,255,0.4);
  }
  .blue-pill:hover { box-shadow: 0 0 40px rgba(0,100,255,0.7); transform: scale(1.05); }
  .err { color: #ff0000; font-size: 14px; margin-bottom: 16px; min-height: 1.2em;
         text-shadow: 0 0 10px rgba(255,0,0,0.5); }
  .cd { color: #00ff41; font-size: 13px; margin-top: 16px;
        text-shadow: 0 0 5px rgba(0,255,65,0.5); }
  .sim-msg {
    position: fixed; top: 0; left: 0; width: 100%%; height: 100%%;
    background: #000; color: #0066ff; display: flex; flex-direction: column;
    align-items: center; justify-content: center;
    font-family: 'Courier New', monospace; font-size: 24px;
    z-index: 999; text-shadow: 0 0 20px rgba(0,100,255,0.6);
  }
  .sim-msg .sub { font-size: 14px; color: #004499; margin-top: 16px; }
</style>
</head>
<body>
<canvas id="c"></canvas>
<div class="overlay">
  <div class="wake" id="w"></div>
  <form method="POST" action="/_gateway" id="f">
    <input type="hidden" name="next" value="%(next_url)s">
    <div class="input-row">
      <input type="password" name="secret" id="s" placeholder="Enter the access code..." autocomplete="off">
    </div>
    <div class="err" id="e">%(error)s</div>
    <div class="pills">
      <button type="submit" class="pill red-pill" id="b">Red Pill</button>
      <button type="button" class="pill blue-pill" id="bp">Blue Pill</button>
    </div>
  </form>
  <div class="cd" id="cd"></div>
</div>
<script>
(function(){
  var c=document.getElementById('c'),ctx=c.getContext('2d');
  function resize(){c.width=window.innerWidth;c.height=window.innerHeight;}
  resize();window.addEventListener('resize',resize);
  var chars='\u30a2\u30a4\u30a6\u30a8\u30aa\u30ab\u30ad\u30af\u30b1\u30b3\u30b5\u30b7\u30b9\u30bb\u30bd\u30bf\u30c1\u30c4\u30c6\u30c8\u30ca\u30cb\u30cc\u30cd\u30ce\u30cf\u30d2\u30d5\u30d8\u30db\u30de\u30df\u30e0\u30e1\u30e2\u30e4\u30e6\u30e8\u30e9\u30ea\u30eb\u30ec\u30ed\u30ef\u30f2\u30f30123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ';
  var fs=14,cols=Math.floor(c.width/fs);
  var drops=[];for(var i=0;i<cols;i++)drops[i]=Math.random()*-100;
  function draw(){
    ctx.fillStyle='rgba(0,0,0,0.05)';ctx.fillRect(0,0,c.width,c.height);
    ctx.font=fs+'px monospace';
    cols=Math.floor(c.width/fs);
    while(drops.length<cols)drops.push(Math.random()*-100);
    for(var i=0;i<cols;i++){
      var ch=chars[Math.floor(Math.random()*chars.length)];
      var x=i*fs,y=drops[i]*fs;
      ctx.fillStyle=Math.random()>0.975?'#fff':'#00ff41';
      ctx.fillText(ch,x,y);
      if(y>c.height&&Math.random()>0.975)drops[i]=0;
      drops[i]++;
    }
  }
  setInterval(draw,33);
})();
(function(){
  var msgs=['Wake up, Neo...','The Matrix has you...','Follow the white rabbit.'];
  var w=document.getElementById('w'),mi=0,ci=0;
  if('%(error)s'){w.textContent='';document.getElementById('s').focus();return;}
  function typeNext(){
    if(mi>=msgs.length){w.textContent='';document.getElementById('s').focus();return;}
    if(ci<=msgs[mi].length){w.textContent=msgs[mi].substring(0,ci)+'_';ci++;setTimeout(typeNext,70);}
    else{setTimeout(function(){ci=0;mi++;typeNext();},1500);}
  }
  setTimeout(typeNext,800);
})();
(function(){
  var cd=%(cooldown)d,b=document.getElementById('b'),cdEl=document.getElementById('cd');
  function tick(){
    if(cd<=0){b.disabled=false;cdEl.textContent='';return;}
    b.disabled=true;cdEl.textContent='System locked. Retry in '+Math.ceil(cd)+'s';
    cd--;setTimeout(tick,1000);
  }
  tick();
})();
document.getElementById('bp').addEventListener('click',function(){
  var d=document.createElement('div');d.className='sim-msg';
  d.innerHTML='You chose the blue pill.<br>Returning to the simulation...<div class="sub">Ignorance is bliss.</div>';
  document.body.appendChild(d);
  setTimeout(function(){window.location.href='https://www.youtube.com/watch?v=dQw4w9WgXcQ';},2500);
});
</script>
</body>
</html>"""


_SKIN_BLADERUNNER = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Blade Runner Gateway</title>
<style>
  * { margin: 0; padding: 0; box-sizing: border-box; }
  body {
    min-height: 100vh; overflow: hidden; color: #f7e8d2;
    font-family: 'Courier New', monospace;
    background:
      radial-gradient(circle at 20%% 15%%, rgba(0,229,255,0.18), transparent 28%%),
      radial-gradient(circle at 80%% 20%%, rgba(255,106,0,0.22), transparent 30%%),
      linear-gradient(180deg, #05020a 0%%, #16071d 48%%, #08030d 100%%);
  }
  body:before {
    content: ''; position: fixed; inset: 0; pointer-events: none;
    background: repeating-linear-gradient(
      180deg, rgba(255,255,255,0.035) 0, rgba(255,255,255,0.035) 1px,
      transparent 1px, transparent 5px);
    mix-blend-mode: overlay; opacity: 0.42;
  }
  body:after {
    content: ''; position: fixed; inset: auto -10%% 0 -10%%; height: 38vh;
    background: linear-gradient(180deg, transparent, rgba(255,106,0,0.14));
    filter: blur(16px); pointer-events: none;
  }
  .rain { position: fixed; inset: 0; pointer-events: none; opacity: 0.22;
          background-image: linear-gradient(115deg, transparent 0 88%%, rgba(0,229,255,0.45) 89%% 91%%, transparent 92%%);
          background-size: 34px 72px; animation: rain 0.7s linear infinite; }
  @keyframes rain { to { background-position: -34px 72px; } }
  .wrap { min-height: 100vh; display: flex; align-items: center; justify-content: center;
          padding: 32px; position: relative; z-index: 1; }
  .card {
    width: min(460px, 100%%); padding: 28px;
    background: rgba(8,3,13,0.86); border: 1px solid #ff6a00;
    box-shadow: 0 0 34px rgba(255,106,0,0.22), inset 0 0 28px rgba(0,229,255,0.06);
  }
  .eyebrow { color: #00e5ff; font-size: 12px; letter-spacing: 3px; text-transform: uppercase; }
  h1 { margin: 12px 0 6px; font-size: 28px; letter-spacing: 2px; color: #ffb000;
       text-shadow: 0 0 18px rgba(255,176,0,0.55); }
  .copy { color: #b08aa7; font-size: 13px; line-height: 1.5; margin-bottom: 22px; }
  input[type=password] {
    width: 100%%; padding: 14px 15px; background: #040107;
    border: 1px solid #3a1432; color: #f7e8d2; outline: none;
    font-family: inherit; font-size: 15px; caret-color: #00e5ff;
  }
  input[type=password]:focus { border-color: #00e5ff; box-shadow: 0 0 18px rgba(0,229,255,0.24); }
  input::placeholder { color: #735269; }
  button {
    width: 100%%; margin-top: 14px; padding: 13px 16px; border: 0; cursor: pointer;
    background: linear-gradient(90deg, #ff6a00, #ffb000); color: #08030d;
    font-weight: 800; font-family: inherit; letter-spacing: 2px; text-transform: uppercase;
    box-shadow: 0 0 22px rgba(255,106,0,0.32);
  }
  button:disabled { background: #322039; color: #735269; cursor: not-allowed; box-shadow: none; }
  .err { min-height: 20px; margin-top: 12px; color: #ff2d55; font-size: 13px;
         text-shadow: 0 0 10px rgba(255,45,85,0.5); }
  .cd { min-height: 18px; margin-top: 10px; color: #00e5ff; font-size: 12px; }
</style>
</head>
<body>
<div class="rain"></div>
<div class="wrap">
  <form class="card" method="POST" action="/_gateway">
    <input type="hidden" name="next" value="%(next_url)s">
    <div class="eyebrow">Private Gateway</div>
    <h1>Blade Runner</h1>
    <p class="copy">Access is restricted. Submit the key before the signal fades.</p>
    <input type="password" name="secret" placeholder="Voight-Kampff code" autocomplete="off" autofocus>
    <button type="submit" id="b">Enter</button>
    <div class="err">%(error)s</div>
    <div class="cd" id="cd"></div>
  </form>
</div>
<script>
(function(){
  var cd=%(cooldown)d,b=document.getElementById('b'),cdEl=document.getElementById('cd');
  function tick(){
    if(cd<=0){b.disabled=false;cdEl.textContent='';return;}
    b.disabled=true;cdEl.textContent='Signal locked. Retry in '+Math.ceil(cd)+'s';
    cd--;setTimeout(tick,1000);
  }
  tick();
})();
</script>
</body>
</html>"""


_SKINS = {
    "default": _CHALLENGE_HTML,
    "google": _SKIN_GOOGLE,
    "wifi": _SKIN_WIFI,
    "terminal": _SKIN_TERMINAL,
    "netflix": _SKIN_NETFLIX,
    "captcha": _SKIN_CAPTCHA,
    "matrix": _SKIN_MATRIX,
    "bladerunner": _SKIN_BLADERUNNER,
}


def _get_skin() -> str:
    """Get the gateway skin name from global parameters."""
    try:
        from core.expression import _load_global_parameters
        return str(_load_global_parameters().get("gateway_skin", "matrix")).strip().lower()
    except Exception:
        return "matrix"


def render_challenge(error="", cooldown=0, next_url="/"):
    import html as _html
    skin = _get_skin()
    template = _SKINS.get(skin, _CHALLENGE_HTML)
    ctx = {"error": error, "cooldown": max(0, int(cooldown)),
           "next_url": _html.escape(next_url, quote=True)}
    result = template % ctx
    return result.encode("utf-8")


def render_failure_redirect(submitted: str) -> str:
    """Return a redirect URL for invalid key (skin-dependent).

    Returns empty string if no redirect (show error on same page).
    """
    skin = _get_skin()
    if skin == "google":
        from urllib.parse import quote
        return f"https://www.google.com/search?q={quote(submitted)}"
    if skin == "netflix":
        return "https://www.netflix.com/login"
    if skin == "bing":
        from urllib.parse import quote
        return f"https://www.bing.com/search?q={quote(submitted)}"
    return ""


_EXEMPT_PATHS = frozenset(["/health", "/favicon.ico"])


def check_request(handler) -> bool:
    """Check an incoming HTTP request against the private gateway.

    Called from _RequestHandler._handle() BEFORE route matching.
    Returns True if the request was handled (blocked/challenged).
    Returns False if the request should proceed normally.
    """
    try:
        return _check_request_inner(handler)
    except Exception as e:
        logger.error("Private gateway error: %s", e, exc_info=True)
        try:
            handler.send_response(500)
            handler.send_header("Content-Type", "text/plain")
            handler.end_headers()
            handler.wfile.write(b"Internal Server Error")
            handler.wfile.flush()
        except Exception:
            pass
        return True


def _check_request_inner(handler) -> bool:
    if not is_enabled():
        return False

    ip = handler.client_address[0] if handler.client_address else "0.0.0.0"
    path = handler.path.split('?', 1)[0]

    if path in _EXEMPT_PATHS:
        return False

    # Routes flagged `public=True AND private_only=True` carry their own
    # credential (usually a URL-embedded ephemeral token) AND restrict
    # themselves to RFC1918 source IPs. They must bypass this human-
    # oriented challenge page — otherwise automated LAN-only clients
    # (CC container hitting /relay-proxy/, service-to-service callbacks,
    # …) get the HTML challenge instead of their actual response and
    # can't parse it. Repro: CC surfaced
    #   "API returned an empty or malformed response (HTTP 200) —
    #    check for a proxy or gateway intercepting the request"
    # while the Matrix-themed challenge page was what actually flew
    # back (container has no _gw cookie). The private_only flag is
    # the guarantee that this bypass can't be abused from the public
    # internet.
    try:
        _server = getattr(handler, "server", None)
        _registry = getattr(_server, "_route_registry", None)
        if _registry is not None:
            _match = _registry.match(handler.command, path)
            _entry = _match[0] if _match else None
            if (_entry is not None
                    and getattr(_entry, "public", False)
                    and getattr(_entry, "private_only", False)):
                return False
    except Exception:
        logger.debug(
            "gateway public+private_only exempt check failed",
            exc_info=True)

    # /files/{file_id} — check if public or gateway_key access
    if path.startswith("/files/"):
        file_id = path.split("/")[2] if len(path.split("/")) >= 3 else ""
        if file_id:
            try:
                from core.file_store import FileStore, ACCESS_PUBLIC, ACCESS_GATEWAY_KEY
                level = FileStore.instance().get_access_level(file_id)
                if level == ACCESS_PUBLIC:
                    return False  # bypass gateway
                if level == ACCESS_GATEWAY_KEY:
                    # Check ?k= param
                    from urllib.parse import parse_qs, urlparse
                    qs = parse_qs(urlparse(handler.path).query)
                    key = qs.get("k", [""])[0]
                    if key and FileStore.instance().check_access(
                            file_id, gateway_key=key):
                        return False  # bypass gateway
            except Exception:
                pass

    if is_banned(ip):
        _send_page(handler, 403, b"Forbidden", "text/plain")
        return True

    cookie_header = handler.headers.get("Cookie", "")
    for part in cookie_header.split(";"):
        part = part.strip()
        if part.startswith(_COOKIE_NAME + "="):
            cookie_val = part[len(_COOKIE_NAME) + 1:]
            if _verify_cookie(cookie_val, ip):
                return False

    if handler.command == "POST" and path == "/_gateway":
        content_length = int(handler.headers.get('Content-Length', 0))
        body = handler.rfile.read(content_length) if content_length > 0 else b""
        return _handle_submit(handler, ip, body)

    # Show challenge page, preserving original URL for post-auth redirect
    original_url = handler.path  # includes query string
    cooldown = get_cooldown_remaining(ip)
    page = render_challenge(cooldown=cooldown, next_url=original_url)
    _send_page(handler, 200, page, "text/html; charset=utf-8")
    return True


def _send_page(handler, status, body, content_type):
    handler.send_response(status)
    handler.send_header("Content-Type", content_type)
    handler.send_header("Content-Length", str(len(body)))
    handler.send_header("Cache-Control", "no-store")
    handler.end_headers()
    handler.wfile.write(body)
    handler.wfile.flush()


def _handle_submit(handler, ip, body):
    from urllib.parse import parse_qs
    params = parse_qs(body.decode("utf-8", errors="replace"))
    submitted = params.get("secret", [""])[0]
    next_url = params.get("next", ["/"])[0] or "/"
    # Ensure redirect is relative (prevent open redirect)
    if not next_url.startswith("/"):
        next_url = "/"

    cooldown = get_cooldown_remaining(ip)
    if cooldown > 0:
        record_failure(ip)
        page = render_challenge(error="Too many attempts.", cooldown=get_cooldown_remaining(ip), next_url=next_url)
        _send_page(handler, 429, page, "text/html; charset=utf-8")
        return True

    if not submitted or not verify_secret(submitted):
        record_failure(ip)
        if is_banned(ip):
            _send_page(handler, 403, b"Forbidden", "text/plain")
            return True
        # Skin-dependent failure redirect (e.g. Google → real google search)
        redirect_url = render_failure_redirect(submitted)
        if redirect_url:
            handler.send_response(302)
            handler.send_header("Location", redirect_url)
            handler.send_header("Cache-Control", "no-store")
            handler.send_header("Content-Length", "0")
            handler.end_headers()
            handler.wfile.flush()
            return True
        cooldown = get_cooldown_remaining(ip)
        page = render_challenge(error="Invalid key.", cooldown=cooldown, next_url=next_url)
        _send_page(handler, 200, page, "text/html; charset=utf-8")
        return True

    # Success — set cookie and redirect to original URL
    record_success(ip)
    cookie_val = _make_cookie_value(ip)
    cookie = f"{_COOKIE_NAME}={cookie_val}; Path=/; Max-Age={_COOKIE_MAX_AGE}; HttpOnly; SameSite=Lax"
    handler.send_response(302)
    handler.send_header("Location", next_url)
    handler.send_header("Set-Cookie", cookie)
    handler.send_header("Cache-Control", "no-store")
    handler.send_header("Content-Length", "0")
    handler.end_headers()
    handler.wfile.flush()
    return True
