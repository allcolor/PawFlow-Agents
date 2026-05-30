# Azure Blob Storage Tasks

"""Tasks PutAzureBlob / GetAzureBlob - Object storage Azure Blob Storage.

Utilise azure-storage-blob si disponible.
"""

import logging
import tempfile
import os
from pathlib import Path
from typing import Dict, Any, List

from core import FlowFile, TaskFactory, TaskError
from core.base_task import BaseTask

logger = logging.getLogger(__name__)


def _get_azure():
    try:
        from azure.storage.blob import BlobServiceClient
        return BlobServiceClient
    except ImportError:
        return None


class GetAzureBlobTask(BaseTask):
    """Download a blob from Azure Blob Storage."""

    TYPE = "getAzureBlob"
    VERSION = "1.0.0"
    NAME = "Get Azure Blob"
    DESCRIPTION = "Télécharger un blob depuis Azure Blob Storage"
    ICON = "cloud-download"

    def __init__(self, config: Dict[str, Any]):
        super().__init__(config)
        self.connection_string = self.config.get('connection_string', '')
        self.container_name = self.config.get('container_name', '')
        self.blob_name = self.config.get('blob_name', '')

    def _resolve_attribute_value(self, flowfile: FlowFile, value: str) -> str:
        if '${' not in value:
            return value
        import re
        def replace_ref(match):
            attr_name = match.group(1)
            return flowfile.get_attribute(attr_name) or match.group(0)
        return re.sub(r'\$\{([^}]+)\}', replace_ref, value)

    def _get_client(self):
        BlobServiceClient = _get_azure()
        if BlobServiceClient is None:
            raise TaskError("getAzureBlob: azure-storage-blob required. Install: pip install azure-storage-blob")

        if not self.connection_string:
            raise TaskError("getAzureBlob: connection_string is required")

        return BlobServiceClient.from_connection_string(self.connection_string)

    def execute(self, flowfile: FlowFile) -> List[FlowFile]:
        container_name = self._resolve_attribute_value(flowfile, self.container_name)
        blob_name = self._resolve_attribute_value(flowfile, self.blob_name)
        if not container_name or not blob_name:
            raise TaskError("getAzureBlob: container_name and blob_name are required")

        try:
            service_client = self._get_client()
            blob_client = service_client.get_blob_client(container=container_name, blob=blob_name)

            properties = blob_client.get_blob_properties()
            downloader = blob_client.download_blob()
            with tempfile.NamedTemporaryFile(delete=False) as tmp:
                downloader.readinto(tmp)
                tmp_path = tmp.name

            size = properties.size or Path(tmp_path).stat().st_size
            with open(tmp_path, 'rb') as fh:
                flowfile.set_content_from_stream(fh, size_hint=size)
            os.unlink(tmp_path)
            flowfile.set_attribute('filename', Path(blob_name).name)
            flowfile.set_attribute('azure.container', container_name)
            flowfile.set_attribute('azure.blob', blob_name)
            flowfile.set_attribute('fileSize', str(size))
            content_type = properties.content_settings.content_type or ''
            if content_type:
                flowfile.set_attribute('mime.type', content_type)

        except TaskError:
            raise
        except Exception as e:
            raise TaskError(f"getAzureBlob: {e}")

        logger.info(f"Azure Blob downloaded {container_name}/{blob_name}")
        return [flowfile]

    def get_parameter_schema(self) -> Dict[str, Any]:
        return {
            'connection_string': {'type': 'secret', 'required': False, 'description': 'Azure Storage connection string'},
            'container_name': {'type': 'string', 'required': False, 'description': 'Azure Blob container name'},
            'blob_name': {'type': 'string', 'required': False, 'description': 'Azure blob name (object path)'},
        }


class PutAzureBlobTask(BaseTask):
    """Upload a blob to Azure Blob Storage."""

    TYPE = "putAzureBlob"
    VERSION = "1.0.0"
    NAME = "Put Azure Blob"
    DESCRIPTION = "Uploader le contenu du FlowFile vers Azure Blob Storage"
    ICON = "cloud-upload"

    def __init__(self, config: Dict[str, Any]):
        super().__init__(config)
        self.connection_string = self.config.get('connection_string', '')
        self.container_name = self.config.get('container_name', '')
        self.blob_name = self.config.get('blob_name', '')
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
        BlobServiceClient = _get_azure()
        if BlobServiceClient is None:
            raise TaskError("putAzureBlob: azure-storage-blob required. Install: pip install azure-storage-blob")

        if not self.connection_string:
            raise TaskError("putAzureBlob: connection_string is required")

        return BlobServiceClient.from_connection_string(self.connection_string)

    def execute(self, flowfile: FlowFile) -> List[FlowFile]:
        container_name = self._resolve_attribute_value(flowfile, self.container_name)
        blob_name = self._resolve_attribute_value(flowfile, self.blob_name)

        if not container_name:
            raise TaskError("putAzureBlob: container_name is required")
        if not blob_name:
            # Use filename attribute as blob_name fallback
            blob_name = flowfile.get_attribute('filename', 'output.bin')

        try:
            service_client = self._get_client()
            blob_client = service_client.get_blob_client(container=container_name, blob=blob_name)

            ct = self.content_type or flowfile.get_attribute('mime.type', '')
            overwrite = True
            kwargs = {'overwrite': overwrite}
            if ct:
                from azure.storage.blob import ContentSettings
                kwargs['content_settings'] = ContentSettings(content_type=ct)

            blob_client.upload_blob(flowfile.get_content(), **kwargs)

            flowfile.set_attribute('azure.container', container_name)
            flowfile.set_attribute('azure.blob', blob_name)

        except TaskError:
            raise
        except Exception as e:
            raise TaskError(f"putAzureBlob: {e}")

        logger.info(f"Azure Blob uploaded to {container_name}/{blob_name}")
        return [flowfile]

    def get_parameter_schema(self) -> Dict[str, Any]:
        return {
            'connection_string': {'type': 'secret', 'required': False, 'description': 'Azure Storage connection string'},
            'container_name': {'type': 'string', 'required': False, 'description': 'Azure Blob container name'},
            'blob_name': {'type': 'string', 'required': False, 'description': 'Azure blob name (default: filename attr)'},
            'content_type': {'type': 'string', 'required': False},
        }


TaskFactory.register(GetAzureBlobTask)
TaskFactory.register(PutAzureBlobTask)
