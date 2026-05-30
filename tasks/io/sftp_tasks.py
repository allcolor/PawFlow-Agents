# SFTP Tasks

"""Tasks GetSFTP / PutSFTP - File transfer via SFTP.

Utilise paramiko si disponible, sinon subprocess sftp/scp.
"""

import logging
import os
import subprocess  # nosec B404
import tempfile
from pathlib import Path
from typing import Dict, Any, List

from core import FlowFile, TaskError
from core.base_task import BaseTask

logger = logging.getLogger(__name__)


def _get_paramiko():
    """Try to import paramiko, return None if unavailable."""
    try:
        import paramiko
        return paramiko
    except ImportError:
        return None


class GetSFTPTask(BaseTask):
    """Retrieve a file from an SFTP server."""

    TYPE = "getSFTP"
    VERSION = "1.0.0"
    NAME = "Get SFTP"
    DESCRIPTION = "Download a file from a server SFTP"
    ICON = "download"

    def __init__(self, config: Dict[str, Any]):
        super().__init__(config)
        self.hostname = self.config.get('hostname', '')
        self.port = int(self.config.get('port', 22))
        self.username = self.config.get('username', '')
        self.password = self.config.get('password', '')
        self.private_key_path = self.config.get('private_key_path', '')
        self.remote_path = self.config.get('remote_path', '')
        self.delete_after = self.config.get('delete_after_download', False)

    def _resolve_attribute_value(self, flowfile: FlowFile, value: str) -> str:
        if '${' not in value:
            return value
        import re
        def replace_ref(match):
            attr_name = match.group(1)
            return flowfile.get_attribute(attr_name) or match.group(0)
        return re.sub(r'\$\{([^}]+)\}', replace_ref, value)

    def execute(self, flowfile: FlowFile) -> List[FlowFile]:
        remote = self._resolve_attribute_value(flowfile, self.remote_path)
        if not remote:
            raise TaskError("getSFTP: remote_path is required")

        paramiko = _get_paramiko()
        if paramiko is None:
            raise TaskError("getSFTP: paramiko is required. Install with: pip install paramiko")

        try:
            transport = paramiko.Transport((self.hostname, self.port))
            if self.private_key_path:
                key = paramiko.RSAKey.from_private_key_file(self.private_key_path)
                transport.connect(username=self.username, pkey=key)
            else:
                transport.connect(username=self.username, password=self.password)

            sftp = paramiko.SFTPClient.from_transport(transport)

            # Download to temp file, then read
            with tempfile.NamedTemporaryFile(delete=False) as tmp:
                tmp_path = tmp.name

            sftp.get(remote, tmp_path)
            stat = sftp.stat(remote)

            with open(tmp_path, 'rb') as fh:
                flowfile.set_content_from_stream(fh, size_hint=stat.st_size)
            os.unlink(tmp_path)

            flowfile.set_attribute('filename', Path(remote).name)
            flowfile.set_attribute('path', remote)
            flowfile.set_attribute('fileSize', str(stat.st_size))
            flowfile.set_attribute('sftp.host', self.hostname)

            if self.delete_after:
                sftp.remove(remote)
                flowfile.set_attribute('sftp.deleted', 'true')

            sftp.close()
            transport.close()

        except Exception as e:
            raise TaskError(f"getSFTP: {e}")

        logger.info(f"Downloaded {remote} from {self.hostname}")
        return [flowfile]

    def get_parameter_schema(self) -> Dict[str, Any]:
        return {
            'hostname': {'type': 'string', 'required': False},
            'port': {'type': 'integer', 'required': False, 'default': 22},
            'username': {'type': 'string', 'required': False},
            'password': {'type': 'secret', 'required': False},
            'private_key_path': {'type': 'string', 'required': False},
            'remote_path': {'type': 'string', 'required': False, 'description': 'Remote file path'},
            'delete_after_download': {'type': 'boolean', 'required': False, 'default': False},
        }


class PutSFTPTask(BaseTask):
    """Send a file to an SFTP server."""

    TYPE = "putSFTP"
    VERSION = "1.0.0"
    NAME = "Put SFTP"
    DESCRIPTION = "Upload a file to a server SFTP"
    ICON = "upload"

    def __init__(self, config: Dict[str, Any]):
        super().__init__(config)
        self.hostname = self.config.get('hostname', '')
        self.port = int(self.config.get('port', 22))
        self.username = self.config.get('username', '')
        self.password = self.config.get('password', '')
        self.private_key_path = self.config.get('private_key_path', '')
        self.remote_directory = self.config.get('remote_directory', '.')
        self.create_directory = self.config.get('create_directory', True)
        self.conflict_resolution = self.config.get('conflict_resolution', 'replace')

    def _resolve_attribute_value(self, flowfile: FlowFile, value: str) -> str:
        if '${' not in value:
            return value
        import re
        def replace_ref(match):
            attr_name = match.group(1)
            return flowfile.get_attribute(attr_name) or match.group(0)
        return re.sub(r'\$\{([^}]+)\}', replace_ref, value)

    def execute(self, flowfile: FlowFile) -> List[FlowFile]:
        filename = flowfile.get_attribute('filename', 'output.bin')
        remote_dir = self._resolve_attribute_value(flowfile, self.remote_directory)
        remote_path = f"{remote_dir}/{filename}"

        paramiko = _get_paramiko()
        if paramiko is None:
            raise TaskError("putSFTP: paramiko is required. Install with: pip install paramiko")

        try:
            transport = paramiko.Transport((self.hostname, self.port))
            if self.private_key_path:
                key = paramiko.RSAKey.from_private_key_file(self.private_key_path)
                transport.connect(username=self.username, pkey=key)
            else:
                transport.connect(username=self.username, password=self.password)

            sftp = paramiko.SFTPClient.from_transport(transport)

            # Create remote directory if needed
            if self.create_directory:
                self._mkdir_p(sftp, remote_dir)

            # Check conflict
            if self.conflict_resolution == 'fail':
                try:
                    sftp.stat(remote_path)
                    raise TaskError(f"putSFTP: file already exists: {remote_path}")
                except FileNotFoundError:
                    pass

            # Write to temp file, then upload
            with tempfile.NamedTemporaryFile(delete=False) as tmp:
                tmp.write(flowfile.get_content())
                tmp_path = tmp.name

            sftp.put(tmp_path, remote_path)
            os.unlink(tmp_path)

            flowfile.set_attribute('sftp.remote_path', remote_path)
            flowfile.set_attribute('sftp.host', self.hostname)

            sftp.close()
            transport.close()

        except TaskError:
            raise
        except Exception as e:
            raise TaskError(f"putSFTP: {e}")

        logger.info(f"Uploaded to {remote_path} on {self.hostname}")
        return [flowfile]

    def _mkdir_p(self, sftp, remote_dir: str):
        """Create remote directory tree."""
        dirs = remote_dir.split('/')
        current = ''
        for d in dirs:
            if not d:
                current = '/'
                continue
            current = f"{current}/{d}" if current else d
            try:
                sftp.stat(current)
            except FileNotFoundError:
                sftp.mkdir(current)

    def get_parameter_schema(self) -> Dict[str, Any]:
        return {
            'hostname': {'type': 'string', 'required': False},
            'port': {'type': 'integer', 'required': False, 'default': 22},
            'username': {'type': 'string', 'required': False},
            'password': {'type': 'secret', 'required': False},
            'private_key_path': {'type': 'string', 'required': False},
            'remote_directory': {'type': 'string', 'required': False, 'description': 'Remote directory'},
            'create_directory': {'type': 'boolean', 'required': False, 'default': True},
            'conflict_resolution': {'type': 'string', 'required': False, 'default': 'replace',
                                    'enum': ['replace', 'fail', 'ignore']},
        }


from core import TaskFactory
TaskFactory.register(GetSFTPTask)
TaskFactory.register(PutSFTPTask)
