"""Event triggers -- automatic flow execution based on events."""

import fnmatch
import hashlib
import hmac
import json
import os
import time
import threading
from typing import Dict, List, Optional, Any, Callable
from dataclasses import dataclass, field
from enum import Enum
from http.server import HTTPServer, BaseHTTPRequestHandler


class TriggerType(Enum):
    FILE_WATCHER = "file_watcher"
    WEBHOOK = "webhook"
    EVENT = "event"
    POLLING = "polling"


class TriggerState(Enum):
    ACTIVE = "active"
    PAUSED = "paused"
    ERROR = "error"
    STOPPED = "stopped"


@dataclass
class TriggerConfig:
    """Configuration for a trigger."""
    trigger_id: str
    trigger_type: TriggerType
    flow_path: str
    name: str = ""
    enabled: bool = True
    parameters: Dict[str, Any] = field(default_factory=dict)
    # Type-specific config
    config: Dict[str, Any] = field(default_factory=dict)


@dataclass
class TriggerEvent:
    """Record of a trigger firing."""
    trigger_id: str
    timestamp: float
    event_data: Dict[str, Any]
    flow_executed: bool = False
    error: str = ""


class BaseTrigger:
    """Base class for all triggers."""

    def __init__(self, config: TriggerConfig, on_fire: Callable):
        self.config = config
        self._on_fire = on_fire  # callback(trigger_id, event_data) -> executes flow
        self._state = TriggerState.STOPPED
        self._fire_count = 0
        self._last_fired: Optional[float] = None
        self._errors: List[str] = []

    @property
    def state(self) -> TriggerState:
        return self._state

    def start(self):
        self._state = TriggerState.ACTIVE

    def stop(self):
        self._state = TriggerState.STOPPED

    def pause(self):
        self._state = TriggerState.PAUSED

    def resume(self):
        if self._state == TriggerState.PAUSED:
            self._state = TriggerState.ACTIVE

    def fire(self, event_data: Dict[str, Any]):
        """Fire the trigger -- execute the configured flow."""
        if self._state != TriggerState.ACTIVE:
            return
        self._fire_count += 1
        self._last_fired = time.time()
        try:
            self._on_fire(self.config.trigger_id, event_data)
        except Exception as e:
            self._errors.append(str(e))
            if len(self._errors) > 50:
                self._errors = self._errors[-50:]

    def get_status(self) -> Dict:
        return {
            "trigger_id": self.config.trigger_id,
            "type": self.config.trigger_type.value,
            "name": self.config.name,
            "state": self._state.value,
            "fire_count": self._fire_count,
            "last_fired": self._last_fired,
            "errors": self._errors[-5:],
            "flow_path": self.config.flow_path,
        }


class FileWatcherTrigger(BaseTrigger):
    """Watches a directory for new/modified files.

    Config:
        watch_path: str -- directory to watch
        patterns: List[str] -- file patterns (e.g. ["*.json", "*.csv"]), default ["*"]
        poll_interval: float -- seconds between checks (default 5.0)
        on_create: bool -- trigger on new files (default True)
        on_modify: bool -- trigger on modified files (default False)
        move_after: str -- move processed files to this dir (optional)
    """

    def __init__(self, config: TriggerConfig, on_fire: Callable):
        super().__init__(config, on_fire)
        self._thread: Optional[threading.Thread] = None
        self._known_files: Dict[str, float] = {}  # path -> mtime
        self._watch_path = self.config.config.get("watch_path", ".")
        self._patterns = self.config.config.get("patterns", ["*"])
        self._poll_interval = self.config.config.get("poll_interval", 5.0)
        self._on_create = self.config.config.get("on_create", True)
        self._on_modify = self.config.config.get("on_modify", False)
        self._move_after = self.config.config.get("move_after", "")

    def start(self):
        super().start()
        # Snapshot current files
        self._known_files = self._scan_directory()
        self._thread = threading.Thread(
            target=self._watch_loop, daemon=True,
            name=f"trigger-fw-{self.config.trigger_id}"
        )
        self._thread.start()

    def stop(self):
        super().stop()
        if self._thread:
            self._thread.join(timeout=self._poll_interval + 1)

    def _matches_pattern(self, filename: str) -> bool:
        """Check if filename matches any configured pattern."""
        if self._patterns == ["*"]:
            return True
        return any(fnmatch.fnmatch(filename, p) for p in self._patterns)

    def _scan_directory(self) -> Dict[str, float]:
        """Scan directory and return {filepath: mtime}."""
        result = {}
        if not os.path.isdir(self._watch_path):
            return result
        for fname in os.listdir(self._watch_path):
            if not self._matches_pattern(fname):
                continue
            fpath = os.path.join(self._watch_path, fname)
            if os.path.isfile(fpath):
                try:
                    result[fpath] = os.path.getmtime(fpath)
                except OSError:
                    pass
        return result

    def _watch_loop(self):
        while self._state == TriggerState.ACTIVE:
            try:
                current = self._scan_directory()

                # Detect new files
                if self._on_create:
                    for fpath in current:
                        if fpath not in self._known_files:
                            self.fire({
                                "event": "file_created",
                                "file_path": fpath,
                                "file_name": os.path.basename(fpath),
                                "file_size": os.path.getsize(fpath),
                            })
                            if self._move_after:
                                self._move_file(fpath)

                # Detect modified files
                if self._on_modify:
                    for fpath, mtime in current.items():
                        if fpath in self._known_files and mtime > self._known_files[fpath]:
                            self.fire({
                                "event": "file_modified",
                                "file_path": fpath,
                                "file_name": os.path.basename(fpath),
                                "file_size": os.path.getsize(fpath),
                            })

                self._known_files = current
            except Exception as e:
                self._errors.append(f"Watch error: {e}")

            # Sleep in small increments
            for _ in range(int(self._poll_interval * 10)):
                if self._state != TriggerState.ACTIVE:
                    return
                time.sleep(0.1)

    def _move_file(self, fpath: str):
        """Move processed file to archive directory."""
        if not self._move_after:
            return
        os.makedirs(self._move_after, exist_ok=True)
        dest = os.path.join(self._move_after, os.path.basename(fpath))
        try:
            os.rename(fpath, dest)
        except OSError:
            pass


class WebhookTrigger(BaseTrigger):
    """HTTP endpoint that triggers a flow on incoming POST requests.

    Config:
        port: int -- HTTP port to listen on (default 9090)
        path: str -- URL path to listen on (default "/webhook")
        secret: str -- optional shared secret for HMAC validation
        methods: List[str] -- allowed HTTP methods (default ["POST"])
    """

    def __init__(self, config: TriggerConfig, on_fire: Callable):
        super().__init__(config, on_fire)
        self._port = self.config.config.get("port", 9090)
        self._path = self.config.config.get("path", "/webhook")
        self._secret = self.config.config.get("secret", "")
        self._server: Optional[HTTPServer] = None
        self._thread: Optional[threading.Thread] = None

    def _make_handler(self):
        """Create an HTTP request handler class bound to this trigger."""
        trigger = self

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):
                if self.path != trigger._path:
                    self.send_response(404)
                    self.end_headers()
                    return

                # Read body
                content_length = int(self.headers.get('Content-Length', 0))
                body = self.rfile.read(content_length) if content_length else b""

                # Validate HMAC if secret configured
                if trigger._secret:
                    sig = self.headers.get('X-Webhook-Signature', '')
                    expected = hmac.new(
                        trigger._secret.encode(), body, hashlib.sha256
                    ).hexdigest()
                    if not hmac.compare_digest(sig, f"sha256={expected}"):
                        self.send_response(401)
                        self.end_headers()
                        self.wfile.write(b'{"error": "Invalid signature"}')
                        return

                # Parse body
                event_data = {
                    "event": "webhook_received",
                    "method": "POST",
                    "path": self.path,
                    "content_type": self.headers.get('Content-Type', ''),
                    "body_size": len(body),
                    "headers": dict(self.headers),
                }

                try:
                    event_data["body"] = json.loads(body.decode('utf-8'))
                except (json.JSONDecodeError, UnicodeDecodeError):
                    event_data["body_raw"] = body.decode('utf-8', errors='replace')

                trigger.fire(event_data)

                self.send_response(200)
                self.send_header('Content-Type', 'application/json')
                self.end_headers()
                self.wfile.write(json.dumps({"status": "accepted"}).encode())

            def log_message(self, format, *args):
                pass  # Suppress default logging

        return Handler

    def start(self):
        super().start()
        try:
            handler_class = self._make_handler()
            self._server = HTTPServer(("0.0.0.0", self._port), handler_class)
            self._thread = threading.Thread(
                target=self._server.serve_forever, daemon=True,
                name=f"trigger-wh-{self.config.trigger_id}"
            )
            self._thread.start()
        except Exception as e:
            self._state = TriggerState.ERROR
            self._errors.append(f"Failed to start webhook server: {e}")

    def stop(self):
        if self._server:
            self._server.shutdown()
        super().stop()
        if self._thread:
            self._thread.join(timeout=5)


class EventTrigger(BaseTrigger):
    """Reacts to internal PyFi2 events (via NotificationManager).

    Config:
        events: List[str] -- event patterns to listen for
            e.g. ["flow.completed", "flow.failed", "task.failed", "scheduler.*"]
        filter: Dict -- optional filter on event payload
            e.g. {"flow_id": "my-flow"} -- only trigger for this flow
    """

    def __init__(self, config: TriggerConfig, on_fire: Callable):
        super().__init__(config, on_fire)
        self._events = self.config.config.get("events", ["flow.completed"])
        self._filter = self.config.config.get("filter", {})
        self._handler_id: Optional[str] = None

    def start(self):
        super().start()
        from core.notifications import NotificationManager
        nm = NotificationManager.get_instance()
        self._handler_id = nm.register_handler(
            handler=self._handle_event,
            events=self._events,
            name=f"trigger-{self.config.trigger_id}",
        )

    def stop(self):
        super().stop()
        if self._handler_id:
            from core.notifications import NotificationManager
            try:
                nm = NotificationManager.get_instance()
                nm.unregister_handler(self._handler_id)
            except Exception:
                pass

    def _handle_event(self, event_type: str, payload: Dict = None):
        """Called by NotificationManager when a matching event fires."""
        if self._state != TriggerState.ACTIVE:
            return

        payload = payload or {}

        # Apply filter
        if self._filter:
            for key, expected in self._filter.items():
                if payload.get(key) != expected:
                    return

        self.fire({
            "event": "internal_event",
            "event_type": event_type,
            "payload": payload,
        })


class PollingTrigger(BaseTrigger):
    """Periodically checks a condition and triggers when met.

    Config:
        url: str -- URL to poll (GET request)
        interval: float -- seconds between polls (default 60)
        condition: str -- "status_ok" (HTTP 200), "content_changed", "json_match"
        json_path: str -- for json_match: JSONPath-like key to check (e.g. "data.status")
        expected_value: Any -- for json_match: expected value
        timeout: int -- HTTP timeout in seconds (default 10)
    """

    def __init__(self, config: TriggerConfig, on_fire: Callable):
        super().__init__(config, on_fire)
        self._url = self.config.config.get("url", "")
        self._interval = self.config.config.get("interval", 60.0)
        self._condition = self.config.config.get("condition", "status_ok")
        self._json_path = self.config.config.get("json_path", "")
        self._expected_value = self.config.config.get("expected_value", None)
        self._timeout = self.config.config.get("timeout", 10)
        self._last_content_hash: Optional[str] = None
        self._thread: Optional[threading.Thread] = None

    def start(self):
        super().start()
        self._thread = threading.Thread(
            target=self._poll_loop, daemon=True,
            name=f"trigger-poll-{self.config.trigger_id}"
        )
        self._thread.start()

    def stop(self):
        super().stop()
        if self._thread:
            self._thread.join(timeout=self._interval + 1)

    def _poll_loop(self):
        while self._state == TriggerState.ACTIVE:
            try:
                self._check()
            except Exception as e:
                self._errors.append(f"Poll error: {e}")

            for _ in range(int(self._interval * 10)):
                if self._state != TriggerState.ACTIVE:
                    return
                time.sleep(0.1)

    def _check(self):
        """Perform one poll check."""
        from urllib.request import urlopen, Request
        from urllib.error import URLError

        try:
            req = Request(self._url)
            with urlopen(req, timeout=self._timeout) as resp:
                body = resp.read()
                status = resp.status
        except URLError:
            # Connection failed -- not a trigger condition
            return
        except Exception:
            return

        triggered = False
        event_data = {
            "event": "poll_check",
            "url": self._url,
            "status": status,
            "body_size": len(body),
        }

        if self._condition == "status_ok" and status == 200:
            triggered = True

        elif self._condition == "content_changed":
            content_hash = hashlib.sha256(body).hexdigest()
            if self._last_content_hash is not None and content_hash != self._last_content_hash:
                triggered = True
                event_data["previous_hash"] = self._last_content_hash
                event_data["current_hash"] = content_hash
            self._last_content_hash = content_hash

        elif self._condition == "json_match":
            try:
                data = json.loads(body)
                value = data
                for key in self._json_path.split("."):
                    if isinstance(value, dict):
                        value = value.get(key)
                    else:
                        value = None
                        break
                if value == self._expected_value:
                    triggered = True
                    event_data["matched_value"] = value
            except (json.JSONDecodeError, KeyError):
                pass

        if triggered:
            try:
                event_data["body"] = json.loads(body.decode('utf-8'))
            except (json.JSONDecodeError, UnicodeDecodeError):
                event_data["body_preview"] = body[:500].decode('utf-8', errors='replace')
            self.fire(event_data)


# Trigger type registry
TRIGGER_TYPES = {
    TriggerType.FILE_WATCHER: FileWatcherTrigger,
    TriggerType.WEBHOOK: WebhookTrigger,
    TriggerType.EVENT: EventTrigger,
    TriggerType.POLLING: PollingTrigger,
}


class TriggerManager:
    """Manages all triggers. Creates, starts, stops, and monitors triggers.

    Usage:
        tm = TriggerManager()
        tm.create_trigger("my-watcher", TriggerType.FILE_WATCHER, "flows/pipeline.json",
                          config={"watch_path": "/data/incoming", "patterns": ["*.csv"]})
        tm.start_trigger("my-watcher")
        # ... trigger fires automatically ...
        tm.stop_trigger("my-watcher")
    """

    def __init__(self):
        self._triggers: Dict[str, BaseTrigger] = {}
        self._history: List[TriggerEvent] = []
        self._max_history = 500
        self._lock = threading.Lock()

    def create_trigger(self, trigger_id: str, trigger_type: TriggerType,
                       flow_path: str, name: str = "",
                       config: Dict = None, parameters: Dict = None,
                       enabled: bool = True) -> Dict:
        """Create a new trigger."""
        if trigger_id in self._triggers:
            raise ValueError(f"Trigger '{trigger_id}' already exists")

        tc = TriggerConfig(
            trigger_id=trigger_id,
            trigger_type=trigger_type,
            flow_path=flow_path,
            name=name or trigger_id,
            enabled=enabled,
            parameters=parameters or {},
            config=config or {},
        )

        trigger_class = TRIGGER_TYPES.get(trigger_type)
        if not trigger_class:
            raise ValueError(f"Unknown trigger type: {trigger_type}")

        trigger = trigger_class(tc, self._on_trigger_fire)
        self._triggers[trigger_id] = trigger

        if enabled:
            trigger.start()

        return trigger.get_status()

    def _on_trigger_fire(self, trigger_id: str, event_data: Dict):
        """Called when a trigger fires -- execute the configured flow."""
        trigger = self._triggers.get(trigger_id)
        if not trigger:
            return

        event = TriggerEvent(
            trigger_id=trigger_id,
            timestamp=time.time(),
            event_data=event_data,
        )

        try:
            from core import FlowFile
            from engine.continuous_executor import ContinuousFlowExecutor
            from engine.parser import FlowParser

            # Load and execute flow
            flow = FlowParser.parse_from_file(trigger.config.flow_path)

            # Create input FlowFile from event data
            content = json.dumps(event_data, default=str).encode('utf-8')
            ff = FlowFile(content=content)
            ff.set_attribute("trigger.id", trigger_id)
            ff.set_attribute("trigger.type", trigger.config.trigger_type.value)
            ff.set_attribute("trigger.timestamp", str(event.timestamp))

            # Add event-specific attributes
            for key, val in event_data.items():
                if isinstance(val, (str, int, float, bool)):
                    ff.set_attribute(f"trigger.{key}", str(val))

            result = ContinuousFlowExecutor.run_batch(
                flow,
                input_flowfiles=[ff],
                parameters=trigger.config.parameters,
            )

            event.flow_executed = True

            # Notify
            try:
                from core.notifications import NotificationManager
                NotificationManager.get_instance().notify(
                    "trigger.fired",
                    {"trigger_id": trigger_id, "flow_path": trigger.config.flow_path},
                )
            except Exception:
                pass

        except Exception as e:
            event.error = str(e)
            event.flow_executed = False

        with self._lock:
            self._history.append(event)
            if len(self._history) > self._max_history:
                self._history = self._history[-self._max_history:]

    def start_trigger(self, trigger_id: str) -> Dict:
        trigger = self._triggers.get(trigger_id)
        if not trigger:
            raise ValueError(f"Trigger '{trigger_id}' not found")
        trigger.start()
        return trigger.get_status()

    def stop_trigger(self, trigger_id: str) -> Dict:
        trigger = self._triggers.get(trigger_id)
        if not trigger:
            raise ValueError(f"Trigger '{trigger_id}' not found")
        trigger.stop()
        return trigger.get_status()

    def pause_trigger(self, trigger_id: str) -> Dict:
        trigger = self._triggers.get(trigger_id)
        if not trigger:
            raise ValueError(f"Trigger '{trigger_id}' not found")
        trigger.pause()
        return trigger.get_status()

    def resume_trigger(self, trigger_id: str) -> Dict:
        trigger = self._triggers.get(trigger_id)
        if not trigger:
            raise ValueError(f"Trigger '{trigger_id}' not found")
        trigger.resume()
        return trigger.get_status()

    def delete_trigger(self, trigger_id: str) -> bool:
        trigger = self._triggers.pop(trigger_id, None)
        if not trigger:
            return False
        if trigger.state == TriggerState.ACTIVE:
            trigger.stop()
        return True

    def get_trigger(self, trigger_id: str) -> Optional[Dict]:
        trigger = self._triggers.get(trigger_id)
        return trigger.get_status() if trigger else None

    def list_triggers(self) -> List[Dict]:
        return [t.get_status() for t in self._triggers.values()]

    def get_history(self, trigger_id: str = None, limit: int = 50) -> List[Dict]:
        with self._lock:
            events = self._history
            if trigger_id:
                events = [e for e in events if e.trigger_id == trigger_id]
            return [
                {
                    "trigger_id": e.trigger_id,
                    "timestamp": e.timestamp,
                    "event_data": e.event_data,
                    "flow_executed": e.flow_executed,
                    "error": e.error,
                }
                for e in events[-limit:]
            ]

    def stop_all(self):
        """Stop all active triggers."""
        for trigger in self._triggers.values():
            if trigger.state == TriggerState.ACTIVE:
                trigger.stop()

    def save_triggers(self, filepath: str):
        """Save trigger configurations to JSON."""
        data = []
        for trigger in self._triggers.values():
            data.append({
                "trigger_id": trigger.config.trigger_id,
                "trigger_type": trigger.config.trigger_type.value,
                "flow_path": trigger.config.flow_path,
                "name": trigger.config.name,
                "enabled": trigger.config.enabled,
                "parameters": trigger.config.parameters,
                "config": trigger.config.config,
            })
        os.makedirs(os.path.dirname(filepath) or ".", exist_ok=True)
        with open(filepath, "w") as f:
            json.dump(data, f, indent=2)

    def load_triggers(self, filepath: str):
        """Load trigger configurations from JSON and start enabled ones."""
        if not os.path.exists(filepath):
            return
        with open(filepath) as f:
            data = json.load(f)
        for item in data:
            try:
                self.create_trigger(
                    trigger_id=item["trigger_id"],
                    trigger_type=TriggerType(item["trigger_type"]),
                    flow_path=item["flow_path"],
                    name=item.get("name", ""),
                    config=item.get("config", {}),
                    parameters=item.get("parameters", {}),
                    enabled=item.get("enabled", True),
                )
            except Exception:
                pass
