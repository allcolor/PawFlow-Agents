# Notification System

"""Système de notifications pour PyFi2.

Envoie des notifications sur les événements de flow (succès, échec, etc.)
via webhook HTTP, et supporte des handlers custom.
"""

import json
import logging
import threading
from collections import defaultdict
from datetime import datetime, timezone
from typing import Any, Callable, Dict, List, Optional
from urllib.request import Request, urlopen
from urllib.error import URLError

logger = logging.getLogger(__name__)


class EventType:
    """Constantes pour les types d'événements."""
    FLOW_STARTED = "flow.started"
    FLOW_COMPLETED = "flow.completed"
    FLOW_FAILED = "flow.failed"
    TASK_FAILED = "task.failed"
    SCHEDULER_JOB_FIRED = "scheduler.job.fired"
    SCHEDULER_JOB_FAILED = "scheduler.job.failed"
    SYSTEM_ERROR = "system.error"
    PLUGIN_INSTALLED = "plugin.installed"
    PLUGIN_UNINSTALLED = "plugin.uninstalled"


class NotificationManager:
    """Gestionnaire de notifications centralisé (singleton).

    Supporte :
    - Webhook HTTP (POST JSON)
    - Handlers Python callable
    - Filtrage par type d'événement
    - File d'attente asynchrone (thread)
    """

    _instance = None
    _lock = threading.Lock()

    @classmethod
    def get_instance(cls) -> 'NotificationManager':
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = cls()
        return cls._instance

    @classmethod
    def reset(cls):
        """Reset singleton (for tests)."""
        cls._instance = None

    def __init__(self):
        self._webhooks: List[Dict[str, Any]] = []
        self._handlers: List[Dict[str, Any]] = []
        self._history: list = []
        self._max_history = 1000
        self._lock = threading.Lock()

    def register_webhook(self, url: str, events: Optional[List[str]] = None,
                         headers: Optional[Dict[str, str]] = None,
                         name: str = "") -> str:
        """Enregistrer un webhook HTTP.

        Args:
            url: URL du webhook (POST)
            events: Liste de types d'événements à écouter (None = tous)
            headers: Headers HTTP supplémentaires
            name: Nom descriptif

        Returns:
            ID du webhook
        """
        import hashlib
        webhook_id = hashlib.sha256(f"{url}{name}{datetime.now().isoformat()}".encode()).hexdigest()[:12]
        with self._lock:
            self._webhooks.append({
                'id': webhook_id,
                'url': url,
                'events': events,
                'headers': headers or {},
                'name': name or url,
                'created': datetime.now(timezone.utc).isoformat(),
                'call_count': 0,
                'last_error': None,
            })
        logger.info(f"Webhook registered: {name or url} ({webhook_id})")
        return webhook_id

    def unregister_webhook(self, webhook_id: str) -> bool:
        """Supprimer un webhook par ID."""
        with self._lock:
            before = len(self._webhooks)
            self._webhooks = [w for w in self._webhooks if w['id'] != webhook_id]
            return len(self._webhooks) < before

    def register_handler(self, handler: Callable, events: Optional[List[str]] = None,
                         name: str = "") -> str:
        """Enregistrer un handler Python callable.

        Args:
            handler: Callable qui reçoit (event_type, payload)
            events: Types d'événements à écouter (None = tous)
            name: Nom descriptif
        """
        import hashlib
        handler_id = hashlib.sha256(f"{id(handler)}{datetime.now().isoformat()}".encode()).hexdigest()[:12]
        with self._lock:
            self._handlers.append({
                'id': handler_id,
                'handler': handler,
                'events': events,
                'name': name or f"handler-{handler_id}",
            })
        return handler_id

    def unregister_handler(self, handler_id: str) -> bool:
        """Supprimer un handler par ID."""
        with self._lock:
            before = len(self._handlers)
            self._handlers = [h for h in self._handlers if h['id'] != handler_id]
            return len(self._handlers) < before

    def notify(self, event_type: str, payload: Optional[Dict[str, Any]] = None,
               async_send: bool = True):
        """Émettre une notification.

        Args:
            event_type: Type d'événement (ex: "flow.completed")
            payload: Données associées
            async_send: Envoyer en arrière-plan (True) ou synchrone (False)
        """
        event = {
            'event': event_type,
            'timestamp': datetime.now(timezone.utc).isoformat(),
            'payload': payload or {},
        }

        # Store in history
        with self._lock:
            self._history.append(event)
            if len(self._history) > self._max_history:
                self._history = self._history[-self._max_history:]

        if async_send:
            thread = threading.Thread(target=self._dispatch, args=(event,), daemon=True)
            thread.start()
        else:
            self._dispatch(event)

    def _dispatch(self, event: Dict[str, Any]):
        """Dispatch event to all matching webhooks and handlers."""
        event_type = event['event']

        # Webhooks
        with self._lock:
            webhooks = list(self._webhooks)
        for wh in webhooks:
            if wh['events'] is not None and not self._matches_event(event_type, wh['events']):
                continue
            self._call_webhook(wh, event)

        # Handlers
        with self._lock:
            handlers = list(self._handlers)
        for h in handlers:
            if h['events'] is not None and not self._matches_event(event_type, h['events']):
                continue
            try:
                h['handler'](event_type, event.get('payload', {}))
            except Exception as e:
                logger.warning(f"Handler {h['name']} error: {e}")

    def _matches_event(self, event_type: str, patterns: List[str]) -> bool:
        """Check if event_type matches any pattern (supports * wildcard)."""
        for pattern in patterns:
            if pattern == '*':
                return True
            if pattern.endswith('.*'):
                prefix = pattern[:-2]
                if event_type.startswith(prefix + '.'):
                    return True
            elif pattern == event_type:
                return True
        return False

    def _call_webhook(self, webhook: Dict[str, Any], event: Dict[str, Any]):
        """Send event to a webhook URL via HTTP POST."""
        try:
            data = json.dumps(event).encode('utf-8')
            headers = {'Content-Type': 'application/json'}
            headers.update(webhook.get('headers', {}))

            req = Request(webhook['url'], data=data, headers=headers, method='POST')
            with urlopen(req, timeout=10) as resp:
                resp.read()

            webhook['call_count'] += 1
            webhook['last_error'] = None
            logger.debug(f"Webhook {webhook['name']} called OK")

        except Exception as e:
            webhook['last_error'] = str(e)
            logger.warning(f"Webhook {webhook['name']} failed: {e}")

    def list_webhooks(self) -> List[Dict[str, Any]]:
        """Lister les webhooks enregistrés."""
        with self._lock:
            return [{k: v for k, v in w.items()} for w in self._webhooks]

    def list_handlers(self) -> List[Dict[str, str]]:
        """Lister les handlers enregistrés."""
        with self._lock:
            return [{'id': h['id'], 'name': h['name'], 'events': h['events']}
                    for h in self._handlers]

    def get_history(self, event_type: Optional[str] = None, limit: int = 50) -> List[Dict]:
        """Récupérer l'historique des notifications."""
        with self._lock:
            history = list(reversed(self._history))
        if event_type:
            history = [e for e in history if self._matches_event(e['event'], [event_type])]
        return history[:limit]

    def get_stats(self) -> Dict[str, Any]:
        """Statistiques du système de notifications."""
        with self._lock:
            event_counts = defaultdict(int)
            for e in self._history:
                event_counts[e['event']] += 1
            return {
                'total_events': len(self._history),
                'webhooks': len(self._webhooks),
                'handlers': len(self._handlers),
                'event_counts': dict(event_counts),
            }
