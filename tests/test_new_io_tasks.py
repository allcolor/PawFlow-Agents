"""Tests for new IO and data tasks: XML, Email, Slack, SFTP."""

import json
import pytest
from unittest.mock import patch, MagicMock

from tasks import register_all_tasks
register_all_tasks()

from core import FlowFile, TaskFactory


# ============================================================================
# ParseXML Tests
# ============================================================================

class TestParseXML:

    def test_simple_xml(self):
        task = TaskFactory.get("parseXML")({})
        xml = b'<root><name>hello</name><value>42</value></root>'
        ff = FlowFile(content=xml)
        results = task.execute(ff)
        data = json.loads(results[0].get_content())
        assert data["root"]["name"] == "hello"
        assert data["root"]["value"] == "42"

    def test_xml_with_attributes(self):
        task = TaskFactory.get("parseXML")({})
        xml = b'<item id="1" type="test"><name>foo</name></item>'
        ff = FlowFile(content=xml)
        results = task.execute(ff)
        data = json.loads(results[0].get_content())
        assert data["item"]["@attributes"]["id"] == "1"
        assert data["item"]["name"] == "foo"

    def test_xml_nested(self):
        task = TaskFactory.get("parseXML")({})
        xml = b'<root><items><item>a</item><item>b</item></items></root>'
        ff = FlowFile(content=xml)
        results = task.execute(ff)
        data = json.loads(results[0].get_content())
        assert isinstance(data["root"]["items"]["item"], list)
        assert len(data["root"]["items"]["item"]) == 2

    def test_invalid_xml(self):
        task = TaskFactory.get("parseXML")({})
        ff = FlowFile(content=b'<not valid xml')
        with pytest.raises(Exception):
            task.execute(ff)

    def test_sets_attributes(self):
        task = TaskFactory.get("parseXML")({})
        ff = FlowFile(content=b'<doc></doc>')
        results = task.execute(ff)
        assert results[0].get_attribute('mime.type') == 'application/json'
        assert results[0].get_attribute('xml.root_tag') == 'doc'


# ============================================================================
# TransformXML Tests
# ============================================================================

class TestTransformXML:

    def test_json_to_xml(self):
        task = TaskFactory.get("transformXML")({'root_tag': 'data'})
        data = {"name": "test", "value": "42"}
        ff = FlowFile(content=json.dumps(data).encode())
        results = task.execute(ff)
        content = results[0].get_content().decode()
        assert '<?xml version="1.0"' in content
        assert '<name>test</name>' in content
        assert '<value>42</value>' in content

    def test_xml_declaration_off(self):
        task = TaskFactory.get("transformXML")({'xml_declaration': False})
        ff = FlowFile(content=b'{"a": "b"}')
        results = task.execute(ff)
        content = results[0].get_content().decode()
        assert '<?xml' not in content

    def test_single_key_uses_as_root(self):
        task = TaskFactory.get("transformXML")({})
        data = {"person": {"name": "Alice"}}
        ff = FlowFile(content=json.dumps(data).encode())
        results = task.execute(ff)
        content = results[0].get_content().decode()
        assert '<person>' in content

    def test_invalid_json(self):
        task = TaskFactory.get("transformXML")({})
        ff = FlowFile(content=b'not json')
        with pytest.raises(Exception):
            task.execute(ff)

    def test_roundtrip(self):
        """XML -> JSON -> XML preserves structure."""
        parse = TaskFactory.get("parseXML")({})
        transform = TaskFactory.get("transformXML")({'xml_declaration': False})

        xml = b'<root><name>test</name><count>5</count></root>'
        ff = FlowFile(content=xml)
        json_ff = parse.execute(ff)[0]
        xml_ff = transform.execute(json_ff)[0]

        content = xml_ff.get_content().decode()
        assert '<name>test</name>' in content
        assert '<count>5</count>' in content


# ============================================================================
# SendEmail Tests (mocked)
# ============================================================================

class TestSendEmail:

    @patch('tasks.io.send_email.smtplib.SMTP')
    def test_send_basic(self, mock_smtp_class):
        mock_server = MagicMock()
        mock_smtp_class.return_value = mock_server

        task = TaskFactory.get("sendEmail")({
            'smtp_host': 'localhost',
            'smtp_port': 25,
            'use_tls': False,
            'from': 'sender@test.com',
            'to': 'recipient@test.com',
            'subject': 'Test Subject',
        })
        ff = FlowFile(content=b'Hello World')
        results = task.execute(ff)

        assert results[0].get_attribute('email.sent') == 'true'
        mock_server.sendmail.assert_called_once()
        mock_server.quit.assert_called_once()

    @patch('tasks.io.send_email.smtplib.SMTP')
    def test_send_with_attachment(self, mock_smtp_class):
        mock_server = MagicMock()
        mock_smtp_class.return_value = mock_server

        task = TaskFactory.get("sendEmail")({
            'smtp_host': 'localhost',
            'use_tls': False,
            'from': 'a@b.com',
            'to': 'c@d.com',
            'subject': 'Attached',
            'attach_content': True,
            'body': 'See attached',
        })
        ff = FlowFile(content=b'file content', attributes={'filename': 'data.txt'})
        results = task.execute(ff)
        assert results[0].get_attribute('email.sent') == 'true'

    def test_missing_to_raises(self):
        task = TaskFactory.get("sendEmail")({
            'smtp_host': 'localhost',
            'from': 'a@b.com',
            'to': '',
            'subject': 'Test',
        })
        ff = FlowFile(content=b'x')
        with pytest.raises(Exception, match="'to' address"):
            task.execute(ff)

    def test_schema(self):
        task = TaskFactory.get("sendEmail")({})
        schema = task.get_parameter_schema()
        assert 'smtp_host' in schema
        assert 'to' in schema

    def test_schema_has_oauth2_fields(self):
        task = TaskFactory.get("sendEmail")({})
        schema = task.get_parameter_schema()
        assert 'auth_type' in schema
        assert 'oauth2_provider' in schema
        assert 'oauth2_client_id' in schema
        assert 'oauth2_client_secret' in schema
        assert 'oauth2_refresh_token' in schema
        assert schema['auth_type']['options'] == ['password', 'oauth2']
        assert schema['oauth2_provider']['options'] == ['gmail', 'microsoft', 'custom']

    def test_oauth2_missing_credentials_raises(self):
        task = TaskFactory.get("sendEmail")({
            'auth_type': 'oauth2',
            'from': 'me@gmail.com',
            'to': 'you@gmail.com',
            'subject': 'Test',
        })
        ff = FlowFile(content=b'hello')
        with pytest.raises(Exception, match="client_id.*client_secret.*refresh_token"):
            task.execute(ff)

    @patch('tasks.io.send_email.smtplib.SMTP')
    @patch('tasks.io.send_email.urlopen')
    def test_oauth2_gmail_flow(self, mock_urlopen, mock_smtp_class):
        """OAuth2 flow: refresh token → access token → XOAUTH2 SMTP auth."""
        import io

        # Mock token endpoint response
        token_response = io.BytesIO(b'{"access_token": "ya29.test_token", "expires_in": 3600}')
        token_response.status = 200
        mock_urlopen.return_value.__enter__ = lambda s: token_response
        mock_urlopen.return_value.__exit__ = MagicMock(return_value=False)

        # Mock SMTP
        mock_server = MagicMock()
        mock_smtp_class.return_value = mock_server

        task = TaskFactory.get("sendEmail")({
            'auth_type': 'oauth2',
            'oauth2_provider': 'gmail',
            'oauth2_client_id': 'test-client-id',
            'oauth2_client_secret': 'test-client-secret',
            'oauth2_refresh_token': 'test-refresh-token',
            'from': 'me@gmail.com',
            'to': 'you@example.com',
            'subject': 'OAuth2 Test',
        })
        ff = FlowFile(content=b'Hello via OAuth2')
        results = task.execute(ff)

        assert results[0].get_attribute('email.sent') == 'true'
        # Verify XOAUTH2 auth was called
        mock_server.auth.assert_called_once()
        auth_args = mock_server.auth.call_args
        assert auth_args[0][0] == 'XOAUTH2'
        # The callback should produce a valid XOAUTH2 string
        callback = auth_args[0][1]
        auth_string = callback()
        assert 'user=me@gmail.com' in auth_string
        assert 'auth=Bearer ya29.test_token' in auth_string
        mock_server.sendmail.assert_called_once()

    @patch('tasks.io.send_email.smtplib.SMTP')
    @patch('tasks.io.send_email.urlopen')
    def test_oauth2_microsoft_preset(self, mock_urlopen, mock_smtp_class):
        """Microsoft preset sets correct SMTP host."""
        import io

        token_response = io.BytesIO(b'{"access_token": "eyJ0test", "expires_in": 3600}')
        mock_urlopen.return_value.__enter__ = lambda s: token_response
        mock_urlopen.return_value.__exit__ = MagicMock(return_value=False)

        mock_server = MagicMock()
        mock_smtp_class.return_value = mock_server

        task = TaskFactory.get("sendEmail")({
            'auth_type': 'oauth2',
            'oauth2_provider': 'microsoft',
            'oauth2_client_id': 'ms-client-id',
            'oauth2_client_secret': 'ms-secret',
            'oauth2_refresh_token': 'ms-refresh',
            'from': 'me@outlook.com',
            'to': 'you@test.com',
            'subject': 'MS Test',
        })
        ff = FlowFile(content=b'Hello MS')
        task.execute(ff)

        # Verify Microsoft SMTP host was used
        mock_smtp_class.assert_called_with('smtp.office365.com', 587, timeout=30)

    def test_oauth2_token_refresh_error(self):
        """Token refresh failure should raise TaskError."""
        task = TaskFactory.get("sendEmail")({
            'auth_type': 'oauth2',
            'oauth2_provider': 'gmail',
            'oauth2_client_id': 'id',
            'oauth2_client_secret': 'secret',
            'oauth2_refresh_token': 'bad-token',
            'from': 'me@gmail.com',
            'to': 'you@test.com',
        })
        ff = FlowFile(content=b'test')
        # urlopen will fail because we're not mocking it → TaskError
        with pytest.raises(Exception):
            task.execute(ff)

    def test_bcc_field(self):
        """BCC should be in schema and parsed."""
        task = TaskFactory.get("sendEmail")({
            'bcc': 'hidden@test.com, another@test.com',
        })
        schema = task.get_parameter_schema()
        assert 'bcc' in schema


# ============================================================================
# NotifySlack Tests (mocked)
# ============================================================================

class TestNotifySlack:

    @patch('tasks.io.notify_slack.urlopen')
    def test_send_message(self, mock_urlopen):
        mock_response = MagicMock()
        mock_response.__enter__ = MagicMock(return_value=mock_response)
        mock_response.__exit__ = MagicMock(return_value=False)
        mock_response.read.return_value = b'ok'
        mock_urlopen.return_value = mock_response

        task = TaskFactory.get("notifySlack")({
            'webhook_url': 'https://hooks.slack.com/test',
            'message': 'Hello from test',
        })
        ff = FlowFile(content=b'content')
        results = task.execute(ff)

        assert results[0].get_attribute('slack.sent') == 'true'
        mock_urlopen.assert_called_once()

    def test_missing_webhook_raises(self):
        task = TaskFactory.get("notifySlack")({'webhook_url': ''})
        ff = FlowFile(content=b'x')
        with pytest.raises(Exception, match="webhook_url"):
            task.execute(ff)

    def test_schema(self):
        task = TaskFactory.get("notifySlack")({})
        schema = task.get_parameter_schema()
        assert 'webhook_url' in schema


# ============================================================================
# SFTP Tasks registration
# ============================================================================

class TestSFTPRegistration:

    def test_get_sftp_registered(self):
        cls = TaskFactory.get("getSFTP")
        assert cls.TYPE == "getSFTP"

    def test_put_sftp_registered(self):
        cls = TaskFactory.get("putSFTP")
        assert cls.TYPE == "putSFTP"

    def test_get_sftp_schema(self):
        task = TaskFactory.get("getSFTP")({'hostname': 'test', 'username': 'user', 'remote_path': '/tmp/file'})
        schema = task.get_parameter_schema()
        assert 'hostname' in schema
        assert 'remote_path' in schema

    def test_put_sftp_schema(self):
        task = TaskFactory.get("putSFTP")({'hostname': 'test', 'username': 'user', 'remote_directory': '/tmp'})
        schema = task.get_parameter_schema()
        assert 'hostname' in schema
        assert 'remote_directory' in schema


# ============================================================================
# FTP Tasks registration
# ============================================================================

class TestFTPRegistration:

    def test_get_ftp_registered(self):
        cls = TaskFactory.get("getFTP")
        assert cls.TYPE == "getFTP"

    def test_put_ftp_registered(self):
        cls = TaskFactory.get("putFTP")
        assert cls.TYPE == "putFTP"

    def test_get_ftp_schema(self):
        task = TaskFactory.get("getFTP")({})
        schema = task.get_parameter_schema()
        assert 'hostname' in schema
        assert 'remote_path' in schema
        assert 'use_tls' in schema

    def test_put_ftp_schema(self):
        task = TaskFactory.get("putFTP")({})
        schema = task.get_parameter_schema()
        assert 'hostname' in schema
        assert 'remote_directory' in schema


# ============================================================================
# Kafka Tasks registration
# ============================================================================

class TestKafkaRegistration:

    def test_publish_kafka_registered(self):
        cls = TaskFactory.get("publishKafka")
        assert cls.TYPE == "publishKafka"

    def test_consume_kafka_registered(self):
        cls = TaskFactory.get("consumeKafka")
        assert cls.TYPE == "consumeKafka"

    def test_publish_schema(self):
        task = TaskFactory.get("publishKafka")({})
        schema = task.get_parameter_schema()
        assert 'bootstrap_servers' in schema
        assert 'topic' in schema
        assert 'compression' in schema

    def test_consume_schema(self):
        task = TaskFactory.get("consumeKafka")({})
        schema = task.get_parameter_schema()
        assert 'bootstrap_servers' in schema
        assert 'group_id' in schema

    def test_publish_requires_kafka_lib(self):
        task = TaskFactory.get("publishKafka")({'topic': 'test'})
        ff = FlowFile(content=b'msg')
        # Should raise because kafka-python not installed (or if it is, needs a broker)
        with pytest.raises(Exception):
            task.execute(ff)


# ============================================================================
# S3 Tasks registration
# ============================================================================

class TestS3Registration:

    def test_get_s3_registered(self):
        cls = TaskFactory.get("getS3")
        assert cls.TYPE == "getS3"

    def test_put_s3_registered(self):
        cls = TaskFactory.get("putS3")
        assert cls.TYPE == "putS3"

    def test_get_s3_schema(self):
        task = TaskFactory.get("getS3")({})
        schema = task.get_parameter_schema()
        assert 'bucket' in schema
        assert 'key' in schema
        assert 'endpoint_url' in schema

    def test_put_s3_schema(self):
        task = TaskFactory.get("putS3")({})
        schema = task.get_parameter_schema()
        assert 'bucket' in schema
        assert 'storage_class' in schema

    def test_get_s3_requires_boto3(self):
        task = TaskFactory.get("getS3")({'bucket': 'test', 'key': 'test.txt'})
        ff = FlowFile(content=b'x')
        with pytest.raises(Exception):
            task.execute(ff)


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
