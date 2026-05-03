# SendEmail Task

"""
Task SendEmail - Send emails through SMTP.

Supports two authentication modes:
- password: login SMTP classique (username/password)
- oauth2: XOAUTH2 pour Gmail et Microsoft 365
  Requires client_id, client_secret, and refresh_token.
  L'access token est obtenu automatiquement via le refresh token.
"""

import base64
import json
import logging
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from email.mime.base import MIMEBase
from email import encoders
from typing import Dict, Any, List, Optional
from urllib.request import Request, urlopen
from urllib.error import URLError

from core import FlowFile, TaskError
from core.base_task import BaseTask

logger = logging.getLogger(__name__)

# OAuth2 presets for common email providers
_OAUTH2_PRESETS = {
    "gmail": {
        "smtp_host": "smtp.gmail.com",
        "smtp_port": 587,
        "token_url": "https://oauth2.googleapis.com/token",
        "scope": "https://mail.google.com/",
    },
    "microsoft": {
        "smtp_host": "smtp.office365.com",
        "smtp_port": 587,
        "token_url": "https://login.microsoftonline.com/common/oauth2/v2.0/token",
        "scope": "https://outlook.office365.com/SMTP.Send",
    },
}


class SendEmailTask(BaseTask):
    """Send an email through SMTP with OAuth2 support (Gmail, Microsoft 365)."""

    TYPE = "sendEmail"
    VERSION = "2.0.0"
    NAME = "Send Email"
    DESCRIPTION = "Send an email through SMTP (password ou OAuth2 pour Gmail/Microsoft)"
    ICON = "mail"

    def __init__(self, config: Dict[str, Any]):
        super().__init__(config)
        self.auth_type = self.config.get('auth_type', 'password')
        self.oauth2_provider = self.config.get('oauth2_provider', 'gmail')
        self.smtp_host = self.config.get('smtp_host', '')
        self.smtp_port = int(self.config.get('smtp_port', 587))
        self.use_tls = self.config.get('use_tls', True)
        self.username = self.config.get('username', '')
        self.password = self.config.get('password', '')
        self.oauth2_client_id = self.config.get('oauth2_client_id', '')
        self.oauth2_client_secret = self.config.get('oauth2_client_secret', '')
        self.oauth2_refresh_token = self.config.get('oauth2_refresh_token', '')
        self.oauth2_token_url = self.config.get('oauth2_token_url', '')
        self.from_addr = self.config.get('from', '')
        self.to_addrs = self.config.get('to', '')
        self.cc_addrs = self.config.get('cc', '')
        self.bcc_addrs = self.config.get('bcc', '')
        self.subject = self.config.get('subject', '')
        self.content_type = self.config.get('content_type', 'text/plain')
        self.attach_content = self.config.get('attach_content', False)
        self.body = self.config.get('body', '')

    def execute(self, flowfile: FlowFile) -> List[FlowFile]:
        to = self._resolve(flowfile, self.to_addrs)
        subject = self._resolve(flowfile, self.subject)
        from_addr = self._resolve(flowfile, self.from_addr)

        if not to:
            raise TaskError("sendEmail: 'to' address is required")
        if not from_addr:
            raise TaskError("sendEmail: 'from' address is required")

        to_list = [a.strip() for a in to.split(',')]
        cc_list = [a.strip() for a in self.cc_addrs.split(',')
                   if a.strip()] if self.cc_addrs else []
        bcc_list = [a.strip() for a in self.bcc_addrs.split(',')
                    if a.strip()] if self.bcc_addrs else []

        msg = self._build_message(flowfile, from_addr, to_list, cc_list,
                                  subject)

        try:
            all_recipients = to_list + cc_list + bcc_list

            if self.auth_type == 'oauth2':
                self._send_oauth2(msg, from_addr, all_recipients)
            else:
                self._send_password(msg, from_addr, all_recipients)

        except TaskError:
            raise
        except Exception as e:
            raise TaskError(f"sendEmail: failed to send: {e}")

        flowfile.set_attribute('email.sent', 'true')
        flowfile.set_attribute('email.to', to)
        flowfile.set_attribute('email.subject', subject)
        logger.info(f"Email sent to {to}")
        return [flowfile]

    def _build_message(self, flowfile: FlowFile, from_addr: str,
                       to_list: List[str], cc_list: List[str],
                       subject: str) -> MIMEMultipart:
        """Build the MIME message."""
        msg = MIMEMultipart()
        msg['From'] = from_addr
        msg['To'] = ', '.join(to_list)
        if cc_list:
            msg['Cc'] = ', '.join(cc_list)
        msg['Subject'] = subject

        # Body: use config body or FlowFile content
        if self.body:
            body_text = self._resolve(flowfile, self.body)
        else:
            body_text = flowfile.get_content().decode('utf-8', errors='replace')

        subtype = 'html' if self.content_type == 'text/html' else 'plain'
        msg.attach(MIMEText(body_text, subtype, 'utf-8'))

        # Attach FlowFile content as file if requested
        if self.attach_content:
            filename = flowfile.get_attribute('filename') or 'attachment.bin'
            part = MIMEBase('application', 'octet-stream')
            part.set_payload(flowfile.get_content())
            encoders.encode_base64(part)
            part.add_header('Content-Disposition',
                            f'attachment; filename="{filename}"')
            msg.attach(part)

        return msg

    def _send_password(self, msg: MIMEMultipart, from_addr: str,
                       recipients: List[str]):
        """Send email via SMTP with password auth."""
        host = self.smtp_host or 'localhost'
        server = smtplib.SMTP(host, self.smtp_port, timeout=30)
        try:
            server.ehlo()
            if self.use_tls:
                server.starttls()
                server.ehlo()
            if self.username:
                server.login(self.username, self.password)
            server.sendmail(from_addr, recipients, msg.as_string())
        finally:
            server.quit()

    def _send_oauth2(self, msg: MIMEMultipart, from_addr: str,
                     recipients: List[str]):
        """Send email via SMTP with XOAUTH2 auth (Gmail, Microsoft 365)."""
        # Resolve OAuth2 config
        client_id = self._resolve_secret(self.oauth2_client_id)
        client_secret = self._resolve_secret(self.oauth2_client_secret)
        refresh_token = self._resolve_secret(self.oauth2_refresh_token)

        if not all([client_id, client_secret, refresh_token]):
            raise TaskError(
                "sendEmail OAuth2: client_id, client_secret, and "
                "refresh_token are all required")

        # Get SMTP host from preset or config
        preset = _OAUTH2_PRESETS.get(self.oauth2_provider, {})
        host = self.smtp_host or preset.get('smtp_host', 'smtp.gmail.com')
        port = self.smtp_port or preset.get('smtp_port', 587)
        token_url = (self.oauth2_token_url
                     or preset.get('token_url',
                                   'https://oauth2.googleapis.com/token'))

        # Exchange refresh token for access token
        access_token = self._get_access_token(
            token_url, client_id, client_secret, refresh_token)

        # Build XOAUTH2 string: "user=<email>\x01auth=Bearer <token>\x01\x01"
        auth_string = f"user={from_addr}\x01auth=Bearer {access_token}\x01\x01"

        server = smtplib.SMTP(host, port, timeout=30)
        try:
            server.ehlo()
            if self.use_tls:
                server.starttls()
                server.ehlo()

            # Authenticate with XOAUTH2
            server.auth('XOAUTH2', lambda _=None: auth_string)

            server.sendmail(from_addr, recipients, msg.as_string())
            logger.debug(f"OAuth2 email sent via {host}:{port}")
        finally:
            server.quit()

    @staticmethod
    def _get_access_token(token_url: str, client_id: str,
                          client_secret: str,
                          refresh_token: str) -> str:
        """Exchange a refresh token for an access token."""
        import urllib.parse

        data = urllib.parse.urlencode({
            "client_id": client_id,
            "client_secret": client_secret,
            "refresh_token": refresh_token,
            "grant_type": "refresh_token",
        }).encode('utf-8')

        req = Request(token_url, data=data, method='POST', headers={
            'Content-Type': 'application/x-www-form-urlencoded',
        })

        try:
            with urlopen(req, timeout=15) as resp:
                result = json.loads(resp.read().decode('utf-8'))
        except URLError as e:
            raise TaskError(
                f"sendEmail OAuth2: token refresh failed: {e}")
        except Exception as e:
            raise TaskError(
                f"sendEmail OAuth2: token refresh error: {e}")

        access_token = result.get('access_token')
        if not access_token:
            error = result.get('error_description',
                               result.get('error', 'unknown'))
            raise TaskError(
                f"sendEmail OAuth2: no access_token in response: {error}")

        return access_token

    def _resolve_secret(self, value: str) -> str:
        """Resolve a value that may be a ${key} expression."""
        if not value:
            return value
        return self.resolve_value(value)

    def _resolve(self, flowfile: FlowFile, value: str) -> str:
        """Resolve ${key} expressions via unified cascade."""
        if not value or '${' not in value:
            return value
        return self.resolve_value(value, flowfile=flowfile)

    def get_parameter_schema(self) -> Dict[str, Any]:
        return {
            'auth_type': {
                'type': 'select', 'required': False, 'default': 'password',
                'options': ['password', 'oauth2'],
                'description': "Mode d'authentification: password (SMTP classique) ou oauth2 (Gmail/Microsoft 365)",
            },
            'oauth2_provider': {
                'type': 'select', 'required': False, 'default': 'gmail',
                'options': ['gmail', 'microsoft', 'custom'],
                'description': 'Preset OAuth2 (configure auto SMTP host, port, token URL)',
            },
            'smtp_host': {
                'type': 'string', 'required': False,
                'description': 'SMTP server host (auto si oauth2_provider choisi)',
            },
            'smtp_port': {
                'type': 'integer', 'required': False, 'default': 587,
                'description': 'SMTP port',
            },
            'use_tls': {
                'type': 'boolean', 'required': False, 'default': True,
                'description': 'Utiliser STARTTLS',
            },
            'username': {
                'type': 'string', 'required': False,
                'description': 'SMTP username (mode password)',
            },
            'password': {
                'type': 'secret', 'required': False,
                'description': 'SMTP password (mode password)',
            },
            'oauth2_client_id': {
                'type': 'string', 'required': False,
                'description': 'OAuth2 Client ID (mode oauth2)',
            },
            'oauth2_client_secret': {
                'type': 'secret', 'required': False,
                'description': 'OAuth2 Client Secret (mode oauth2)',
            },
            'oauth2_refresh_token': {
                'type': 'secret', 'required': False,
                'description': 'OAuth2 Refresh Token (mode oauth2, obtenu via Google OAuth Playground ou flow OAuth)',
            },
            'oauth2_token_url': {
                'type': 'string', 'required': False,
                'description': 'OAuth2 Token URL (auto si oauth2_provider choisi)',
            },
            'from': {
                'type': 'string', 'required': False,
                'description': 'Sender address',
            },
            'to': {
                'type': 'string', 'required': False,
                'description': 'Recipient(s), comma-separated (supports ${attribute})',
            },
            'cc': {
                'type': 'string', 'required': False,
                'description': 'CC, comma-separated',
            },
            'bcc': {
                'type': 'string', 'required': False,
                'description': 'BCC, comma-separated',
            },
            'subject': {
                'type': 'string', 'required': False,
                'description': 'Sujet (supports ${attribute})',
            },
            'content_type': {
                'type': 'select', 'required': False, 'default': 'text/plain',
                'options': ['text/plain', 'text/html'],
                'description': 'Format du body',
            },
            'body': {
                'type': 'string', 'required': False,
                'description': 'Custom body (default: FlowFile content)',
            },
            'attach_content': {
                'type': 'boolean', 'required': False, 'default': False,
                'description': 'Attach FlowFile content as an attachment',
            },
        }


from core import TaskFactory
TaskFactory.register(SendEmailTask)
