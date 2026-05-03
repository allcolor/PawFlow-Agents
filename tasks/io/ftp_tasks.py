# FTP Tasks

"""Tasks GetFTP / PutFTP - File transfer via FTP/FTPS."""

import ftplib
import logging
import tempfile
import os
from pathlib import Path
from typing import Dict, Any, List

from core import FlowFile, TaskFactory, TaskError
from core.base_task import BaseTask

logger = logging.getLogger(__name__)


class GetFTPTask(BaseTask):
    """Download a file from a server FTP/FTPS."""

    TYPE = "getFTP"
    VERSION = "1.0.0"
    NAME = "Get FTP"
    DESCRIPTION = "Download a file from a server FTP ou FTPS"
    ICON = "download"

    def __init__(self, config: Dict[str, Any]):
        super().__init__(config)
        self.hostname = self.config.get('hostname', '')
        self.port = int(self.config.get('port', 21))
        self.username = self.config.get('username', 'anonymous')
        self.password = self.config.get('password', '')
        self.use_tls = self.config.get('use_tls', False)
        self.remote_path = self.config.get('remote_path', '')
        self.passive_mode = self.config.get('passive_mode', True)
        self.delete_after = self.config.get('delete_after_download', False)

    def _resolve_attribute_value(self, flowfile: FlowFile, value: str) -> str:
        if '${' not in value:
            return value
        import re
        def replace_ref(match):
            attr_name = match.group(1)
            return flowfile.get_attribute(attr_name) or match.group(0)
        return re.sub(r'\$\{([^}]+)\}', replace_ref, value)

    def _connect(self) -> ftplib.FTP:
        if self.use_tls:
            ftp = ftplib.FTP_TLS()
        else:
            ftp = ftplib.FTP()
        ftp.connect(self.hostname, self.port, timeout=30)
        ftp.login(self.username, self.password)
        if self.use_tls:
            ftp.prot_p()
        if self.passive_mode:
            ftp.set_pasv(True)
        return ftp

    def execute(self, flowfile: FlowFile) -> List[FlowFile]:
        remote = self._resolve_attribute_value(flowfile, self.remote_path)
        if not remote:
            raise TaskError("getFTP: remote_path is required")

        try:
            ftp = self._connect()

            # Download to temp file
            with tempfile.NamedTemporaryFile(delete=False) as tmp:
                ftp.retrbinary(f'RETR {remote}', tmp.write)
                tmp_path = tmp.name

            content = Path(tmp_path).read_bytes()
            os.unlink(tmp_path)

            # Get file size
            try:
                size = ftp.size(remote)
            except Exception:
                size = len(content)

            flowfile.set_content(content)
            flowfile.set_attribute('filename', Path(remote).name)
            flowfile.set_attribute('path', remote)
            flowfile.set_attribute('fileSize', str(size))
            flowfile.set_attribute('ftp.host', self.hostname)

            if self.delete_after:
                ftp.delete(remote)
                flowfile.set_attribute('ftp.deleted', 'true')

            ftp.quit()
        except ftplib.all_errors as e:
            raise TaskError(f"getFTP: {e}")

        logger.info(f"FTP downloaded {remote} from {self.hostname}")
        return [flowfile]

    def get_parameter_schema(self) -> Dict[str, Any]:
        return {
            'hostname': {'type': 'string', 'required': False, 'description': 'FTP server hostname'},
            'port': {'type': 'integer', 'required': False, 'default': 21},
            'username': {'type': 'string', 'required': False, 'default': 'anonymous'},
            'password': {'type': 'secret', 'required': False},
            'use_tls': {'type': 'boolean', 'required': False, 'default': False, 'description': 'Use FTPS'},
            'remote_path': {'type': 'string', 'required': False, 'description': 'Remote file path'},
            'passive_mode': {'type': 'boolean', 'required': False, 'default': True},
            'delete_after_download': {'type': 'boolean', 'required': False, 'default': False},
        }


class PutFTPTask(BaseTask):
    """Upload a file to a server FTP/FTPS."""

    TYPE = "putFTP"
    VERSION = "1.0.0"
    NAME = "Put FTP"
    DESCRIPTION = "Upload a file to a server FTP ou FTPS"
    ICON = "upload"

    def __init__(self, config: Dict[str, Any]):
        super().__init__(config)
        self.hostname = self.config.get('hostname', '')
        self.port = int(self.config.get('port', 21))
        self.username = self.config.get('username', 'anonymous')
        self.password = self.config.get('password', '')
        self.use_tls = self.config.get('use_tls', False)
        self.remote_directory = self.config.get('remote_directory', '.')
        self.passive_mode = self.config.get('passive_mode', True)
        self.create_directory = self.config.get('create_directory', True)

    def _resolve_attribute_value(self, flowfile: FlowFile, value: str) -> str:
        if '${' not in value:
            return value
        import re
        def replace_ref(match):
            attr_name = match.group(1)
            return flowfile.get_attribute(attr_name) or match.group(0)
        return re.sub(r'\$\{([^}]+)\}', replace_ref, value)

    def _connect(self) -> ftplib.FTP:
        if self.use_tls:
            ftp = ftplib.FTP_TLS()
        else:
            ftp = ftplib.FTP()
        ftp.connect(self.hostname, self.port, timeout=30)
        ftp.login(self.username, self.password)
        if self.use_tls:
            ftp.prot_p()
        if self.passive_mode:
            ftp.set_pasv(True)
        return ftp

    def _mkdir_p(self, ftp: ftplib.FTP, remote_dir: str):
        """Create remote directory tree."""
        dirs = remote_dir.strip('/').split('/')
        for d in dirs:
            if not d:
                continue
            try:
                ftp.cwd(d)
            except ftplib.error_perm:
                ftp.mkd(d)
                ftp.cwd(d)
        # Return to root
        ftp.cwd('/')

    def execute(self, flowfile: FlowFile) -> List[FlowFile]:
        filename = flowfile.get_attribute('filename', 'output.bin')
        remote_dir = self._resolve_attribute_value(flowfile, self.remote_directory)
        remote_path = f"{remote_dir}/{filename}"

        try:
            ftp = self._connect()

            if self.create_directory:
                self._mkdir_p(ftp, remote_dir)

            # Write to temp file then upload
            with tempfile.NamedTemporaryFile(delete=False) as tmp:
                tmp.write(flowfile.get_content())
                tmp_path = tmp.name

            with open(tmp_path, 'rb') as f:
                ftp.storbinary(f'STOR {remote_path}', f)
            os.unlink(tmp_path)

            flowfile.set_attribute('ftp.remote_path', remote_path)
            flowfile.set_attribute('ftp.host', self.hostname)

            ftp.quit()
        except ftplib.all_errors as e:
            raise TaskError(f"putFTP: {e}")

        logger.info(f"FTP uploaded to {remote_path} on {self.hostname}")
        return [flowfile]

    def get_parameter_schema(self) -> Dict[str, Any]:
        return {
            'hostname': {'type': 'string', 'required': False, 'description': 'FTP server hostname'},
            'port': {'type': 'integer', 'required': False, 'default': 21},
            'username': {'type': 'string', 'required': False, 'default': 'anonymous'},
            'password': {'type': 'secret', 'required': False},
            'use_tls': {'type': 'boolean', 'required': False, 'default': False},
            'remote_directory': {'type': 'string', 'required': False, 'description': 'Remote directory'},
            'passive_mode': {'type': 'boolean', 'required': False, 'default': True},
            'create_directory': {'type': 'boolean', 'required': False, 'default': True},
        }


TaskFactory.register(GetFTPTask)
TaskFactory.register(PutFTPTask)
