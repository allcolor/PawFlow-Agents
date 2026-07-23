# ManageCalendar Task

"""
Task ManageCalendar - list/create/update/delete calendar events.

Supports two providers:
- google: Google Calendar API v3 (REST), OAuth2 refresh-token flow identical
  to SendEmailTask's XOAUTH2 (same credential shape: client_id, client_secret,
  refresh_token).
- caldav: generic CalDAV (Nextcloud, Radicale, iCloud, most self-hosted
  servers) over HTTP Basic auth, using PUT/DELETE of iCalendar (.ics)
  resources and a calendar-query REPORT for listing.
"""

import json
import logging
import re
import uuid
from typing import Dict, Any, List, Optional
from urllib.request import Request, urlopen
from urllib.error import URLError, HTTPError
import urllib.parse

from core import FlowFile, TaskError
from core.base_task import BaseTask

logger = logging.getLogger(__name__)

_GOOGLE_TOKEN_URL = "https://oauth2.googleapis.com/token"  # nosec B105
_GOOGLE_EVENTS_BASE = "https://www.googleapis.com/calendar/v3/calendars"


class ManageCalendarTask(BaseTask):
    """List, create, update, or delete calendar events (Google Calendar or CalDAV)."""

    TYPE = "manageCalendar"
    VERSION = "1.0.0"
    NAME = "Manage Calendar"
    DESCRIPTION = "List/create/update/delete calendar events (Google Calendar OAuth2 or generic CalDAV)"
    ICON = "calendar"

    def __init__(self, config: Dict[str, Any]):
        super().__init__(config)
        self.provider = self.config.get('provider', 'google')
        self.action = self.config.get('action', 'list')

        # Google Calendar
        self.calendar_id = self.config.get('calendar_id', 'primary')
        self.oauth2_client_id = self.config.get('oauth2_client_id', '')
        self.oauth2_client_secret = self.config.get('oauth2_client_secret', '')
        self.oauth2_refresh_token = self.config.get('oauth2_refresh_token', '')

        # CalDAV
        self.caldav_url = self.config.get('caldav_url', '')
        self.caldav_username = self.config.get('caldav_username', '')
        self.caldav_password = self.config.get('caldav_password', '')

        # Event fields (create/update)
        self.event_id = self.config.get('event_id', '')
        self.summary = self.config.get('summary', '')
        self.description = self.config.get('description', '')
        self.location = self.config.get('location', '')
        self.start_time = self.config.get('start_time', '')
        self.end_time = self.config.get('end_time', '')
        self.timezone = self.config.get('timezone', 'UTC')
        self.attendees = self.config.get('attendees', '')

        # List window
        self.time_min = self.config.get('time_min', '')
        self.time_max = self.config.get('time_max', '')
        self.max_results = int(self.config.get('max_results', 50))

    def execute(self, flowfile: FlowFile) -> List[FlowFile]:
        action = self._resolve(flowfile, self.action)
        if action not in ('list', 'create', 'update', 'delete'):
            raise TaskError(
                f"manageCalendar: unknown action '{action}' "
                "(expected list/create/update/delete)")

        if self.provider == 'google':
            result = self._execute_google(flowfile, action)
        elif self.provider == 'caldav':
            result = self._execute_caldav(flowfile, action)
        else:
            raise TaskError(
                f"manageCalendar: unknown provider '{self.provider}' "
                "(expected google/caldav)")

        flowfile.set_content(json.dumps(result, ensure_ascii=False, indent=2).encode('utf-8'))
        flowfile.set_attribute('calendar.action', action)
        flowfile.set_attribute('calendar.provider', self.provider)
        return [flowfile]

    # ── Google Calendar ──────────────────────────────────────────────

    def _execute_google(self, flowfile: FlowFile, action: str) -> Dict[str, Any]:
        client_id = self._resolve_secret(self.oauth2_client_id)
        client_secret = self._resolve_secret(self.oauth2_client_secret)
        refresh_token = self._resolve_secret(self.oauth2_refresh_token)
        if not all([client_id, client_secret, refresh_token]):
            raise TaskError(
                "manageCalendar (google): oauth2_client_id, oauth2_client_secret "
                "and oauth2_refresh_token are all required")

        access_token = self._get_google_access_token(client_id, client_secret, refresh_token)
        calendar_id = urllib.parse.quote(self._resolve(flowfile, self.calendar_id) or 'primary', safe='')
        base = f"{_GOOGLE_EVENTS_BASE}/{calendar_id}/events"

        if action == 'list':
            params = {'maxResults': str(self.max_results), 'singleEvents': 'true', 'orderBy': 'startTime'}
            time_min = self._resolve(flowfile, self.time_min)
            time_max = self._resolve(flowfile, self.time_max)
            if time_min:
                params['timeMin'] = time_min
            if time_max:
                params['timeMax'] = time_max
            url = f"{base}?{urllib.parse.urlencode(params)}"
            data = self._google_request('GET', url, access_token)
            events = [self._normalize_google_event(e) for e in data.get('items', [])]
            return {'events': events}

        if action == 'create':
            body = self._build_google_event_body(flowfile)
            data = self._google_request('POST', base, access_token, body)
            return {'event': self._normalize_google_event(data)}

        event_id = self._resolve(flowfile, self.event_id)
        if not event_id:
            raise TaskError(f"manageCalendar (google): event_id is required for '{action}'")
        event_url = f"{base}/{urllib.parse.quote(event_id, safe='')}"

        if action == 'update':
            body = self._build_google_event_body(flowfile)
            data = self._google_request('PATCH', event_url, access_token, body)
            return {'event': self._normalize_google_event(data)}

        # delete
        self._google_request('DELETE', event_url, access_token)
        return {'event_id': event_id, 'deleted': True}

    def _build_google_event_body(self, flowfile: FlowFile) -> Dict[str, Any]:
        summary = self._resolve(flowfile, self.summary)
        start_time = self._resolve(flowfile, self.start_time)
        end_time = self._resolve(flowfile, self.end_time)
        if not summary:
            raise TaskError("manageCalendar (google): summary is required")
        if not start_time or not end_time:
            raise TaskError("manageCalendar (google): start_time and end_time are required (RFC3339)")

        body: Dict[str, Any] = {
            'summary': summary,
            'start': {'dateTime': start_time, 'timeZone': self.timezone},
            'end': {'dateTime': end_time, 'timeZone': self.timezone},
        }
        description = self._resolve(flowfile, self.description)
        if description:
            body['description'] = description
        location = self._resolve(flowfile, self.location)
        if location:
            body['location'] = location
        attendees = self._resolve(flowfile, self.attendees)
        if attendees:
            body['attendees'] = [{'email': a.strip()} for a in attendees.split(',') if a.strip()]
        return body

    @staticmethod
    def _normalize_google_event(event: Dict[str, Any]) -> Dict[str, Any]:
        return {
            'id': event.get('id', ''),
            'summary': event.get('summary', ''),
            'description': event.get('description', ''),
            'location': event.get('location', ''),
            'start': event.get('start', {}).get('dateTime') or event.get('start', {}).get('date', ''),
            'end': event.get('end', {}).get('dateTime') or event.get('end', {}).get('date', ''),
            'status': event.get('status', ''),
            'html_link': event.get('htmlLink', ''),
        }

    def _get_google_access_token(self, client_id: str, client_secret: str, refresh_token: str) -> str:
        data = urllib.parse.urlencode({
            'client_id': client_id,
            'client_secret': client_secret,
            'refresh_token': refresh_token,
            'grant_type': 'refresh_token',
        }).encode('utf-8')
        req = Request(_GOOGLE_TOKEN_URL, data=data, method='POST', headers={
            'Content-Type': 'application/x-www-form-urlencoded',
        })
        try:
            with urlopen(req, timeout=15) as resp:  # nosec B310 - Google OAuth token endpoint.
                result = json.loads(resp.read().decode('utf-8'))
        except URLError as e:
            raise TaskError(f"manageCalendar (google): token refresh failed: {e}")

        access_token = result.get('access_token')
        if not access_token:
            error = result.get('error_description', result.get('error', 'unknown'))
            raise TaskError(f"manageCalendar (google): no access_token in response: {error}")
        return access_token

    @staticmethod
    def _google_request(method: str, url: str, access_token: str,
                         body: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        headers = {'Authorization': f'Bearer {access_token}'}
        data = None
        if body is not None:
            data = json.dumps(body).encode('utf-8')
            headers['Content-Type'] = 'application/json'
        req = Request(url, data=data, method=method, headers=headers)
        try:
            with urlopen(req, timeout=20) as resp:  # nosec B310 - Google Calendar API endpoint.
                raw = resp.read()
                return json.loads(raw.decode('utf-8')) if raw else {}
        except HTTPError as e:
            detail = e.read().decode('utf-8', errors='replace')
            raise TaskError(f"manageCalendar (google): API error {e.code}: {detail}")
        except URLError as e:
            raise TaskError(f"manageCalendar (google): request failed: {e}")

    # ── CalDAV ────────────────────────────────────────────────────────

    def _execute_caldav(self, flowfile: FlowFile, action: str) -> Dict[str, Any]:
        base_url = self._resolve(flowfile, self.caldav_url)
        username = self._resolve_secret(self.caldav_username)
        password = self._resolve_secret(self.caldav_password)
        if not base_url:
            raise TaskError("manageCalendar (caldav): caldav_url is required")
        auth_header = self._basic_auth_header(username, password) if username else None

        if action == 'list':
            return {'events': self._caldav_list(base_url, auth_header, flowfile)}

        if action == 'delete':
            event_id = self._resolve(flowfile, self.event_id)
            if not event_id:
                raise TaskError("manageCalendar (caldav): event_id is required for 'delete'")
            self._caldav_http('DELETE', self._caldav_event_url(base_url, event_id), auth_header)
            return {'event_id': event_id, 'deleted': True}

        # create / update: both PUT the full .ics resource
        event_id = self._resolve(flowfile, self.event_id) or f"{uuid.uuid4()}"
        ics = self._build_ics(flowfile, event_id)
        self._caldav_http('PUT', self._caldav_event_url(base_url, event_id), auth_header,
                           body=ics.encode('utf-8'),
                           headers={'Content-Type': 'text/calendar; charset=utf-8'})
        return {'event_id': event_id, action: True}

    @staticmethod
    def _caldav_event_url(base_url: str, event_id: str) -> str:
        base = base_url if base_url.endswith('/') else base_url + '/'
        return f"{base}{urllib.parse.quote(event_id, safe='')}.ics"

    def _build_ics(self, flowfile: FlowFile, event_id: str) -> str:
        summary = self._resolve(flowfile, self.summary)
        start_time = self._resolve(flowfile, self.start_time)
        end_time = self._resolve(flowfile, self.end_time)
        if not summary:
            raise TaskError("manageCalendar (caldav): summary is required")
        if not start_time or not end_time:
            raise TaskError(
                "manageCalendar (caldav): start_time and end_time are required "
                "(iCalendar UTC format, e.g. 20260723T140000Z)")
        description = self._resolve(flowfile, self.description)
        location = self._resolve(flowfile, self.location)

        lines = [
            'BEGIN:VCALENDAR', 'VERSION:2.0', 'PRODID:-//PawFlow//manageCalendar//EN',
            'BEGIN:VEVENT',
            f'UID:{event_id}@pawflow',
            f'SUMMARY:{self._ics_escape(summary)}',
            f'DTSTART:{start_time}', f'DTEND:{end_time}',
        ]
        if description:
            lines.append(f'DESCRIPTION:{self._ics_escape(description)}')
        if location:
            lines.append(f'LOCATION:{self._ics_escape(location)}')
        lines += ['END:VEVENT', 'END:VCALENDAR']
        return '\r\n'.join(lines) + '\r\n'

    @staticmethod
    def _ics_escape(text: str) -> str:
        return text.replace('\\', '\\\\').replace(',', '\\,').replace(';', '\\;').replace('\n', '\\n')

    def _caldav_list(self, base_url: str, auth_header: Optional[str],
                      flowfile: FlowFile) -> List[Dict[str, Any]]:
        time_min = self._resolve(flowfile, self.time_min)
        time_max = self._resolve(flowfile, self.time_max)
        range_filter = ''
        if time_min or time_max:
            attrs = []
            if time_min:
                attrs.append(f'start="{time_min}"')
            if time_max:
                attrs.append(f'end="{time_max}"')
            range_filter = f'<C:time-range {" ".join(attrs)}/>'
        report_body = (
            '<?xml version="1.0" encoding="utf-8"?>'
            '<C:calendar-query xmlns:D="DAV:" xmlns:C="urn:ietf:params:xml:ns:caldav">'
            '<D:prop><D:getetag/><C:calendar-data/></D:prop>'
            '<C:filter><C:comp-filter name="VCALENDAR"><C:comp-filter name="VEVENT">'
            f'{range_filter}'
            '</C:comp-filter></C:comp-filter></C:filter>'
            '</C:calendar-query>'
        )
        raw = self._caldav_http(
            'REPORT', base_url, auth_header,
            body=report_body.encode('utf-8'),
            headers={'Content-Type': 'application/xml; charset=utf-8', 'Depth': '1'})
        return self._parse_caldav_multistatus(raw.decode('utf-8', errors='replace'))

    @staticmethod
    def _parse_caldav_multistatus(xml_text: str) -> List[Dict[str, Any]]:
        """Extract VEVENT summaries out of a CalDAV multistatus REPORT response
        without a full WebDAV/iCal parser dependency."""
        events = []
        for block in re.findall(r'BEGIN:VEVENT(.*?)END:VEVENT', xml_text, re.DOTALL):
            def field(name):
                m = re.search(rf'{name}[^:]*:(.+)', block)
                return m.group(1).strip() if m else ''
            events.append({
                'id': field('UID'),
                'summary': field('SUMMARY'),
                'start': field('DTSTART'),
                'end': field('DTEND'),
                'location': field('LOCATION'),
            })
        return events

    @staticmethod
    def _basic_auth_header(username: str, password: str) -> str:
        import base64
        token = base64.b64encode(f'{username}:{password}'.encode('utf-8')).decode('ascii')
        return f'Basic {token}'

    @staticmethod
    def _caldav_http(method: str, url: str, auth_header: Optional[str],
                      body: Optional[bytes] = None,
                      headers: Optional[Dict[str, str]] = None) -> bytes:
        req_headers = dict(headers or {})
        if auth_header:
            req_headers['Authorization'] = auth_header
        req = Request(url, data=body, method=method, headers=req_headers)
        try:
            with urlopen(req, timeout=20) as resp:  # nosec B310 - user-configured CalDAV server.
                return resp.read()
        except HTTPError as e:
            detail = e.read().decode('utf-8', errors='replace')
            raise TaskError(f"manageCalendar (caldav): server error {e.code}: {detail}")
        except URLError as e:
            raise TaskError(f"manageCalendar (caldav): request failed: {e}")

    # ── Helpers ──────────────────────────────────────────────────────

    def _resolve_secret(self, value: str) -> str:
        if not value:
            return value
        return self.resolve_value(value)

    def _resolve(self, flowfile: FlowFile, value: str) -> str:
        if not value or '${' not in value:
            return value
        return self.resolve_value(value, flowfile=flowfile)

    def get_parameter_schema(self) -> Dict[str, Any]:
        return {
            'provider': {
                'type': 'select', 'required': False, 'default': 'google',
                'options': ['google', 'caldav'],
                'description': 'Fournisseur de calendrier',
            },
            'action': {
                'type': 'select', 'required': False, 'default': 'list',
                'options': ['list', 'create', 'update', 'delete'],
                'description': 'Action à effectuer',
            },
            'calendar_id': {
                'type': 'string', 'required': False, 'default': 'primary',
                'description': "ID du calendrier Google (ex: 'primary' ou une adresse email de calendrier)",
            },
            'oauth2_client_id': {
                'type': 'string', 'required': False,
                'description': 'OAuth2 Client ID (provider=google)',
            },
            'oauth2_client_secret': {
                'type': 'secret', 'required': False,
                'description': 'OAuth2 Client Secret (provider=google)',
            },
            'oauth2_refresh_token': {
                'type': 'secret', 'required': False,
                'description': 'OAuth2 Refresh Token (provider=google, scope Calendar)',
            },
            'caldav_url': {
                'type': 'string', 'required': False,
                'description': "URL de la collection CalDAV (provider=caldav), ex: https://cloud.example.com/remote.php/dav/calendars/user/personal/",
            },
            'caldav_username': {
                'type': 'string', 'required': False,
                'description': 'Utilisateur CalDAV (provider=caldav)',
            },
            'caldav_password': {
                'type': 'secret', 'required': False,
                'description': "Mot de passe ou app-password CalDAV (provider=caldav)",
            },
            'event_id': {
                'type': 'string', 'required': False,
                'description': "ID de l'événement (requis pour update/delete; optionnel pour create en CalDAV)",
            },
            'summary': {
                'type': 'string', 'required': False,
                'description': "Titre de l'événement (create/update, supports ${attribute})",
            },
            'description': {
                'type': 'string', 'required': False,
                'description': 'Description (create/update)',
            },
            'location': {
                'type': 'string', 'required': False,
                'description': 'Lieu (create/update)',
            },
            'start_time': {
                'type': 'string', 'required': False,
                'description': "Début: RFC3339 pour Google (ex: 2026-07-23T14:00:00+02:00) ou iCal UTC pour CalDAV (ex: 20260723T140000Z)",
            },
            'end_time': {
                'type': 'string', 'required': False,
                'description': 'Fin, même format que start_time',
            },
            'timezone': {
                'type': 'string', 'required': False, 'default': 'UTC',
                'description': "Fuseau horaire IANA (provider=google), ex: 'Europe/Brussels'",
            },
            'attendees': {
                'type': 'string', 'required': False,
                'description': "Emails des participants, séparés par des virgules (provider=google)",
            },
            'time_min': {
                'type': 'string', 'required': False,
                'description': "Borne basse pour 'list' (RFC3339 pour Google, iCal UTC pour CalDAV)",
            },
            'time_max': {
                'type': 'string', 'required': False,
                'description': "Borne haute pour 'list'",
            },
            'max_results': {
                'type': 'integer', 'required': False, 'default': 50,
                'description': "Nombre max d'événements retournés (provider=google, action=list)",
            },
        }


from core import TaskFactory
TaskFactory.register(ManageCalendarTask)
