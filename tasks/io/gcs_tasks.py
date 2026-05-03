# GCS Tasks

"""Tasks PutGCS / GetGCS - Object storage Google Cloud Storage.

Utilise google-cloud-storage si disponible.
"""

import logging
import tempfile
import os
from pathlib import Path
from typing import Dict, Any, List

from core import FlowFile, TaskFactory, TaskError
from core.base_task import BaseTask

logger = logging.getLogger(__name__)


def _get_gcs():
    try:
        from google.cloud import storage
        return storage
    except ImportError:
        return None


class GetGCSTask(BaseTask):
    """Download an object from Google Cloud Storage."""

    TYPE = "getGCS"
    VERSION = "1.0.0"
    NAME = "Get GCS"
    DESCRIPTION = "Télécharger un objet depuis Google Cloud Storage"
    ICON = "cloud-download"

    def __init__(self, config: Dict[str, Any]):
        super().__init__(config)
        self.bucket = self.config.get('bucket', '')
        self.blob_name = self.config.get('blob_name', '')
        self.project_id = self.config.get('project_id', '')
        self.credentials_json = self.config.get('credentials_json', '')

    def _resolve_attribute_value(self, flowfile: FlowFile, value: str) -> str:
        if '${' not in value:
            return value
        import re
        def replace_ref(match):
            attr_name = match.group(1)
            return flowfile.get_attribute(attr_name) or match.group(0)
        return re.sub(r'\$\{([^}]+)\}', replace_ref, value)

    def _get_client(self):
        storage = _get_gcs()
        if storage is None:
            raise TaskError("getGCS: google-cloud-storage required. Install: pip install google-cloud-storage")

        kwargs = {}
        if self.project_id:
            kwargs['project'] = self.project_id
        if self.credentials_json:
            import json
            from google.oauth2 import service_account
            creds_info = json.loads(self.credentials_json)
            credentials = service_account.Credentials.from_service_account_info(creds_info)
            kwargs['credentials'] = credentials

        return storage.Client(**kwargs)

    def execute(self, flowfile: FlowFile) -> List[FlowFile]:
        bucket_name = self._resolve_attribute_value(flowfile, self.bucket)
        blob_name = self._resolve_attribute_value(flowfile, self.blob_name)
        if not bucket_name or not blob_name:
            raise TaskError("getGCS: bucket and blob_name are required")

        try:
            client = self._get_client()
            bucket = client.bucket(bucket_name)
            blob = bucket.blob(blob_name)

            with tempfile.NamedTemporaryFile(delete=False) as tmp:
                blob.download_to_filename(tmp.name)
                tmp_path = tmp.name

            content = Path(tmp_path).read_bytes()
            os.unlink(tmp_path)

            # Reload blob metadata
            blob.reload()

            flowfile.set_content(content)
            flowfile.set_attribute('filename', Path(blob_name).name)
            flowfile.set_attribute('gcs.bucket', bucket_name)
            flowfile.set_attribute('gcs.blob', blob_name)
            flowfile.set_attribute('fileSize', str(blob.size or len(content)))
            content_type = blob.content_type or ''
            if content_type:
                flowfile.set_attribute('mime.type', content_type)

        except TaskError:
            raise
        except Exception as e:
            raise TaskError(f"getGCS: {e}")

        logger.info(f"GCS downloaded gs://{bucket_name}/{blob_name}")
        return [flowfile]

    def get_parameter_schema(self) -> Dict[str, Any]:
        return {
            'bucket': {'type': 'string', 'required': False, 'description': 'GCS bucket name'},
            'blob_name': {'type': 'string', 'required': False, 'description': 'GCS blob name (object path)'},
            'project_id': {'type': 'string', 'required': False, 'description': 'Google Cloud project ID'},
            'credentials_json': {'type': 'secret', 'required': False, 'description': 'Service account JSON credentials'},
        }


class PutGCSTask(BaseTask):
    """Upload an object to Google Cloud Storage."""

    TYPE = "putGCS"
    VERSION = "1.0.0"
    NAME = "Put GCS"
    DESCRIPTION = "Uploader le contenu du FlowFile vers Google Cloud Storage"
    ICON = "cloud-upload"

    def __init__(self, config: Dict[str, Any]):
        super().__init__(config)
        self.bucket = self.config.get('bucket', '')
        self.blob_name = self.config.get('blob_name', '')
        self.project_id = self.config.get('project_id', '')
        self.credentials_json = self.config.get('credentials_json', '')
        self.content_type = self.config.get('content_type', '')

    def _resolve_attribute_value(self, flowfile: FlowFile, value: str) -> str:
        if '${' not in value:
            return value
        import re
        def replace_ref(match):
            attr_name = match.group(1)
            return flowfile.get_attribute(attr_name) or match.group(0)
        return re.sub(r'\$\{([^}]+)\}', replace_ref, value)

    def _get_client(self):
        storage = _get_gcs()
        if storage is None:
            raise TaskError("putGCS: google-cloud-storage required. Install: pip install google-cloud-storage")

        kwargs = {}
        if self.project_id:
            kwargs['project'] = self.project_id
        if self.credentials_json:
            import json
            from google.oauth2 import service_account
            creds_info = json.loads(self.credentials_json)
            credentials = service_account.Credentials.from_service_account_info(creds_info)
            kwargs['credentials'] = credentials

        return storage.Client(**kwargs)

    def execute(self, flowfile: FlowFile) -> List[FlowFile]:
        bucket_name = self._resolve_attribute_value(flowfile, self.bucket)
        blob_name = self._resolve_attribute_value(flowfile, self.blob_name)

        if not bucket_name:
            raise TaskError("putGCS: bucket is required")
        if not blob_name:
            # Use filename attribute as blob_name fallback
            blob_name = flowfile.get_attribute('filename', 'output.bin')

        try:
            client = self._get_client()
            bucket = client.bucket(bucket_name)
            blob = bucket.blob(blob_name)

            ct = self.content_type or flowfile.get_attribute('mime.type', '')
            if ct:
                blob.content_type = ct

            blob.upload_from_string(flowfile.get_content())

            flowfile.set_attribute('gcs.bucket', bucket_name)
            flowfile.set_attribute('gcs.blob', blob_name)

        except TaskError:
            raise
        except Exception as e:
            raise TaskError(f"putGCS: {e}")

        logger.info(f"GCS uploaded to gs://{bucket_name}/{blob_name}")
        return [flowfile]

    def get_parameter_schema(self) -> Dict[str, Any]:
        return {
            'bucket': {'type': 'string', 'required': False, 'description': 'GCS bucket name'},
            'blob_name': {'type': 'string', 'required': False, 'description': 'GCS blob name (default: filename attr)'},
            'project_id': {'type': 'string', 'required': False, 'description': 'Google Cloud project ID'},
            'credentials_json': {'type': 'secret', 'required': False, 'description': 'Service account JSON credentials'},
            'content_type': {'type': 'string', 'required': False},
        }


TaskFactory.register(GetGCSTask)
TaskFactory.register(PutGCSTask)
