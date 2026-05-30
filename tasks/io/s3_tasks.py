# S3 Tasks

"""Tasks PutS3 / GetS3 - Object storage AWS S3 / compatible (MinIO, etc).

Uses boto3 if available.
"""

import logging
import tempfile
import os
from pathlib import Path
from typing import Dict, Any, List

from core import FlowFile, TaskFactory, TaskError
from core.base_task import BaseTask

logger = logging.getLogger(__name__)


def _get_boto3():
    try:
        import boto3
        return boto3
    except ImportError:
        return None


class GetS3Task(BaseTask):
    """Download an object from S3."""

    TYPE = "getS3"
    VERSION = "1.0.0"
    NAME = "Get S3"
    DESCRIPTION = "Télécharger un objet depuis AWS S3 ou compatible"
    ICON = "cloud-download"

    def __init__(self, config: Dict[str, Any]):
        super().__init__(config)
        self.bucket = self.config.get('bucket', '')
        self.key = self.config.get('key', '')
        self.region = self.config.get('region', 'us-east-1')
        self.access_key = self.config.get('access_key_id', '')
        self.secret_key = self.config.get('secret_access_key', '')
        self.endpoint_url = self.config.get('endpoint_url', '')
        self.delete_after = self.config.get('delete_after_download', False)

    def _resolve_attribute_value(self, flowfile: FlowFile, value: str) -> str:
        if '${' not in value:
            return value
        import re
        def replace_ref(match):
            attr_name = match.group(1)
            return flowfile.get_attribute(attr_name) or match.group(0)
        return re.sub(r'\$\{([^}]+)\}', replace_ref, value)

    def _get_client(self):
        boto3 = _get_boto3()
        if boto3 is None:
            raise TaskError("getS3: boto3 required. Install: pip install boto3")

        kwargs = {'region_name': self.region}
        if self.access_key:
            kwargs['aws_access_key_id'] = self.access_key
            kwargs['aws_secret_access_key'] = self.secret_key
        if self.endpoint_url:
            kwargs['endpoint_url'] = self.endpoint_url

        return boto3.client('s3', **kwargs)

    def execute(self, flowfile: FlowFile) -> List[FlowFile]:
        bucket = self._resolve_attribute_value(flowfile, self.bucket)
        key = self._resolve_attribute_value(flowfile, self.key)
        if not bucket or not key:
            raise TaskError("getS3: bucket and key are required")

        try:
            s3 = self._get_client()

            with tempfile.NamedTemporaryFile(delete=False) as tmp:
                s3.download_fileobj(bucket, key, tmp)
                tmp_path = tmp.name

            # Get metadata
            head = s3.head_object(Bucket=bucket, Key=key)
            size = head.get('ContentLength', 0)

            with open(tmp_path, 'rb') as fh:
                flowfile.set_content_from_stream(fh, size_hint=size)
            os.unlink(tmp_path)
            flowfile.set_attribute('filename', Path(key).name)
            flowfile.set_attribute('s3.bucket', bucket)
            flowfile.set_attribute('s3.key', key)
            flowfile.set_attribute('fileSize', str(size or flowfile.size()))
            content_type = head.get('ContentType', '')
            if content_type:
                flowfile.set_attribute('mime.type', content_type)

            if self.delete_after:
                s3.delete_object(Bucket=bucket, Key=key)
                flowfile.set_attribute('s3.deleted', 'true')

        except TaskError:
            raise
        except Exception as e:
            raise TaskError(f"getS3: {e}")

        logger.info(f"S3 downloaded s3://{bucket}/{key}")
        return [flowfile]

    def get_parameter_schema(self) -> Dict[str, Any]:
        return {
            'bucket': {'type': 'string', 'required': False, 'description': 'S3 bucket name'},
            'key': {'type': 'string', 'required': False, 'description': 'S3 object key'},
            'region': {'type': 'string', 'required': False, 'default': 'us-east-1'},
            'access_key_id': {'type': 'secret', 'required': False},
            'secret_access_key': {'type': 'secret', 'required': False},
            'endpoint_url': {'type': 'string', 'required': False, 'description': 'Custom endpoint (MinIO, etc)'},
            'delete_after_download': {'type': 'boolean', 'required': False, 'default': False},
        }


class PutS3Task(BaseTask):
    """Upload an object to S3."""

    TYPE = "putS3"
    VERSION = "1.0.0"
    NAME = "Put S3"
    DESCRIPTION = "Uploader le contenu du FlowFile vers AWS S3 ou compatible"
    ICON = "cloud-upload"

    def __init__(self, config: Dict[str, Any]):
        super().__init__(config)
        self.bucket = self.config.get('bucket', '')
        self.key = self.config.get('key', '')
        self.region = self.config.get('region', 'us-east-1')
        self.access_key = self.config.get('access_key_id', '')
        self.secret_key = self.config.get('secret_access_key', '')
        self.endpoint_url = self.config.get('endpoint_url', '')
        self.content_type = self.config.get('content_type', '')
        self.storage_class = self.config.get('storage_class', 'STANDARD')

    def _resolve_attribute_value(self, flowfile: FlowFile, value: str) -> str:
        if '${' not in value:
            return value
        import re
        def replace_ref(match):
            attr_name = match.group(1)
            return flowfile.get_attribute(attr_name) or match.group(0)
        return re.sub(r'\$\{([^}]+)\}', replace_ref, value)

    def _get_client(self):
        boto3 = _get_boto3()
        if boto3 is None:
            raise TaskError("putS3: boto3 required. Install: pip install boto3")

        kwargs = {'region_name': self.region}
        if self.access_key:
            kwargs['aws_access_key_id'] = self.access_key
            kwargs['aws_secret_access_key'] = self.secret_key
        if self.endpoint_url:
            kwargs['endpoint_url'] = self.endpoint_url

        return boto3.client('s3', **kwargs)

    def execute(self, flowfile: FlowFile) -> List[FlowFile]:
        bucket = self._resolve_attribute_value(flowfile, self.bucket)
        key = self._resolve_attribute_value(flowfile, self.key)

        if not bucket:
            raise TaskError("putS3: bucket is required")
        if not key:
            # Use filename attribute as key fallback
            key = flowfile.get_attribute('filename', 'output.bin')

        try:
            s3 = self._get_client()

            extra_args = {'StorageClass': self.storage_class}
            ct = self.content_type or flowfile.get_attribute('mime.type', '')
            if ct:
                extra_args['ContentType'] = ct

            import io
            s3.upload_fileobj(
                io.BytesIO(flowfile.get_content()),
                bucket, key,
                ExtraArgs=extra_args,
            )

            flowfile.set_attribute('s3.bucket', bucket)
            flowfile.set_attribute('s3.key', key)

        except TaskError:
            raise
        except Exception as e:
            raise TaskError(f"putS3: {e}")

        logger.info(f"S3 uploaded to s3://{bucket}/{key}")
        return [flowfile]

    def get_parameter_schema(self) -> Dict[str, Any]:
        return {
            'bucket': {'type': 'string', 'required': False, 'description': 'S3 bucket name'},
            'key': {'type': 'string', 'required': False, 'description': 'S3 object key (default: filename attr)'},
            'region': {'type': 'string', 'required': False, 'default': 'us-east-1'},
            'access_key_id': {'type': 'secret', 'required': False},
            'secret_access_key': {'type': 'secret', 'required': False},
            'endpoint_url': {'type': 'string', 'required': False, 'description': 'Custom endpoint (MinIO, etc)'},
            'content_type': {'type': 'string', 'required': False},
            'storage_class': {'type': 'string', 'required': False, 'default': 'STANDARD',
                             'enum': ['STANDARD', 'REDUCED_REDUNDANCY', 'STANDARD_IA', 'GLACIER']},
        }


TaskFactory.register(GetS3Task)
TaskFactory.register(PutS3Task)
