"""Tests for the manageCalendar task (Google Calendar OAuth2 + generic CalDAV)."""

import json
import pytest
from unittest.mock import patch, MagicMock

from tasks import register_all_tasks
register_all_tasks()

from core import FlowFile, TaskFactory


def _resp(payload):
    """Build a fake urlopen() context-manager response."""
    mock_resp = MagicMock()
    mock_resp.read.return_value = (
        payload if isinstance(payload, bytes) else payload.encode('utf-8'))
    mock_resp.__enter__.return_value = mock_resp
    mock_resp.__exit__.return_value = False
    return mock_resp


_TOKEN_RESP = json.dumps({'access_token': 'fake-token'})


class TestManageCalendarGoogle:

    @patch('tasks.io.manage_calendar.urlopen')
    def test_list_events(self, mock_urlopen):
        events_payload = json.dumps({'items': [
            {'id': 'e1', 'summary': 'RDV client', 'start': {'dateTime': '2026-07-23T10:00:00Z'},
             'end': {'dateTime': '2026-07-23T10:30:00Z'}, 'status': 'confirmed'},
        ]})
        mock_urlopen.side_effect = [_resp(_TOKEN_RESP), _resp(events_payload)]

        task = TaskFactory.get("manageCalendar")({
            'provider': 'google', 'action': 'list',
            'oauth2_client_id': 'cid', 'oauth2_client_secret': 'csec',
            'oauth2_refresh_token': 'rtok',
        })
        ff = FlowFile(content=b'')
        results = task.execute(ff)
        data = json.loads(results[0].get_content())
        assert len(data['events']) == 1
        assert data['events'][0]['summary'] == 'RDV client'
        assert results[0].get_attribute('calendar.provider') == 'google'

    @patch('tasks.io.manage_calendar.urlopen')
    def test_create_event(self, mock_urlopen):
        created_payload = json.dumps({'id': 'e2', 'summary': 'Consultation',
                                       'start': {'dateTime': '2026-07-24T09:00:00Z'},
                                       'end': {'dateTime': '2026-07-24T09:30:00Z'}})
        mock_urlopen.side_effect = [_resp(_TOKEN_RESP), _resp(created_payload)]

        task = TaskFactory.get("manageCalendar")({
            'provider': 'google', 'action': 'create',
            'oauth2_client_id': 'cid', 'oauth2_client_secret': 'csec',
            'oauth2_refresh_token': 'rtok',
            'summary': 'Consultation',
            'start_time': '2026-07-24T09:00:00+02:00',
            'end_time': '2026-07-24T09:30:00+02:00',
        })
        ff = FlowFile(content=b'')
        results = task.execute(ff)
        data = json.loads(results[0].get_content())
        assert data['event']['id'] == 'e2'

    def test_create_missing_summary_raises(self):
        task = TaskFactory.get("manageCalendar")({
            'provider': 'google', 'action': 'create',
            'oauth2_client_id': 'cid', 'oauth2_client_secret': 'csec',
            'oauth2_refresh_token': 'rtok',
            'start_time': '2026-07-24T09:00:00Z', 'end_time': '2026-07-24T09:30:00Z',
        })
        with patch('tasks.io.manage_calendar.urlopen', return_value=_resp(_TOKEN_RESP)):
            ff = FlowFile(content=b'')
            with pytest.raises(Exception, match='summary is required'):
                task.execute(ff)

    @patch('tasks.io.manage_calendar.urlopen')
    def test_delete_event(self, mock_urlopen):
        mock_urlopen.side_effect = [_resp(_TOKEN_RESP), _resp(b'')]
        task = TaskFactory.get("manageCalendar")({
            'provider': 'google', 'action': 'delete',
            'oauth2_client_id': 'cid', 'oauth2_client_secret': 'csec',
            'oauth2_refresh_token': 'rtok',
            'event_id': 'e1',
        })
        ff = FlowFile(content=b'')
        results = task.execute(ff)
        data = json.loads(results[0].get_content())
        assert data == {'event_id': 'e1', 'deleted': True}

    def test_missing_oauth_credentials_raises(self):
        task = TaskFactory.get("manageCalendar")({'provider': 'google', 'action': 'list'})
        ff = FlowFile(content=b'')
        with pytest.raises(Exception, match='oauth2_client_id'):
            task.execute(ff)

    def test_unknown_action_raises(self):
        task = TaskFactory.get("manageCalendar")({'provider': 'google', 'action': 'bogus'})
        ff = FlowFile(content=b'')
        with pytest.raises(Exception, match='unknown action'):
            task.execute(ff)


class TestManageCalendarCalDAV:

    @patch('tasks.io.manage_calendar.urlopen')
    def test_create_event_puts_ics(self, mock_urlopen):
        mock_urlopen.return_value = _resp(b'')
        task = TaskFactory.get("manageCalendar")({
            'provider': 'caldav', 'action': 'create',
            'caldav_url': 'https://cloud.example.com/dav/calendars/user/personal/',
            'caldav_username': 'user', 'caldav_password': 'pw',
            'summary': 'RDV client Dupont',
            'start_time': '20260723T140000Z', 'end_time': '20260723T143000Z',
            'event_id': 'evt-123',
        })
        ff = FlowFile(content=b'')
        results = task.execute(ff)
        data = json.loads(results[0].get_content())
        assert data['event_id'] == 'evt-123'
        assert data['create'] is True

        req = mock_urlopen.call_args[0][0]
        assert req.get_method() == 'PUT'
        assert req.full_url.endswith('evt-123.ics')
        assert b'SUMMARY:RDV client Dupont' in req.data

    @patch('tasks.io.manage_calendar.urlopen')
    def test_list_events_parses_multistatus(self, mock_urlopen):
        multistatus = (
            "<D:multistatus xmlns:D=\"DAV:\">"
            "<D:response><C:calendar-data>"
            "BEGIN:VCALENDAR\nBEGIN:VEVENT\nUID:abc\nSUMMARY:Audience\n"
            "DTSTART:20260801T090000Z\nDTEND:20260801T100000Z\nEND:VEVENT\nEND:VCALENDAR"
            "</C:calendar-data></D:response></D:multistatus>"
        )
        mock_urlopen.return_value = _resp(multistatus)
        task = TaskFactory.get("manageCalendar")({
            'provider': 'caldav', 'action': 'list',
            'caldav_url': 'https://cloud.example.com/dav/calendars/user/personal/',
            'caldav_username': 'user', 'caldav_password': 'pw',
        })
        ff = FlowFile(content=b'')
        results = task.execute(ff)
        data = json.loads(results[0].get_content())
        assert data['events'][0]['summary'] == 'Audience'
        assert data['events'][0]['id'] == 'abc'

    @patch('tasks.io.manage_calendar.urlopen')
    def test_delete_event(self, mock_urlopen):
        mock_urlopen.return_value = _resp(b'')
        task = TaskFactory.get("manageCalendar")({
            'provider': 'caldav', 'action': 'delete',
            'caldav_url': 'https://cloud.example.com/dav/calendars/user/personal/',
            'event_id': 'evt-123',
        })
        ff = FlowFile(content=b'')
        results = task.execute(ff)
        data = json.loads(results[0].get_content())
        assert data == {'event_id': 'evt-123', 'deleted': True}

    def test_missing_caldav_url_raises(self):
        task = TaskFactory.get("manageCalendar")({'provider': 'caldav', 'action': 'list'})
        ff = FlowFile(content=b'')
        with pytest.raises(Exception, match='caldav_url is required'):
            task.execute(ff)

    def test_delete_missing_event_id_raises(self):
        task = TaskFactory.get("manageCalendar")({
            'provider': 'caldav', 'action': 'delete',
            'caldav_url': 'https://cloud.example.com/dav/calendars/user/personal/',
        })
        ff = FlowFile(content=b'')
        with pytest.raises(Exception, match='event_id is required'):
            task.execute(ff)


class TestManageCalendarSchema:

    def test_schema(self):
        task = TaskFactory.get("manageCalendar")({})
        schema = task.get_parameter_schema()
        assert 'provider' in schema
        assert 'action' in schema
        assert 'caldav_url' in schema
        assert 'oauth2_refresh_token' in schema

    def test_unknown_provider_raises(self):
        task = TaskFactory.get("manageCalendar")({'provider': 'bogus', 'action': 'list'})
        ff = FlowFile(content=b'')
        with pytest.raises(Exception, match='unknown provider'):
            task.execute(ff)
