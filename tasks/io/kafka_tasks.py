# Kafka Tasks

"""Tâches PublishKafka / ConsumeKafka - Messaging Apache Kafka.

Utilise kafka-python si disponible.
"""

import json
import logging
from typing import Dict, Any, List

from core import FlowFile, TaskFactory, TaskError
from core.base_task import BaseTask

logger = logging.getLogger(__name__)


def _get_kafka():
    try:
        import kafka
        return kafka
    except ImportError:
        return None


class PublishKafkaTask(BaseTask):
    """Publier un message sur un topic Kafka."""

    TYPE = "publishKafka"
    VERSION = "1.0.0"
    NAME = "Publish Kafka"
    DESCRIPTION = "Publier le contenu du FlowFile sur un topic Kafka"
    ICON = "send"

    def __init__(self, config: Dict[str, Any]):
        super().__init__(config)
        self.bootstrap_servers = self.config.get('bootstrap_servers', 'localhost:9092')
        self.topic = self.config.get('topic', '')
        self.key_attribute = self.config.get('key_attribute', '')
        self.headers_attributes = self.config.get('headers_attributes', '')
        self.acks = self.config.get('acks', 'all')
        self.compression = self.config.get('compression', 'none')

    def _resolve_attribute_value(self, flowfile: FlowFile, value: str) -> str:
        if '${' not in value:
            return value
        import re
        def replace_ref(match):
            attr_name = match.group(1)
            return flowfile.get_attribute(attr_name) or match.group(0)
        return re.sub(r'\$\{([^}]+)\}', replace_ref, value)

    def execute(self, flowfile: FlowFile) -> List[FlowFile]:
        kafka_mod = _get_kafka()
        if kafka_mod is None:
            raise TaskError("publishKafka: kafka-python required. Install: pip install kafka-python")

        topic = self._resolve_attribute_value(flowfile, self.topic)
        if not topic:
            raise TaskError("publishKafka: topic is required")

        try:
            producer = kafka_mod.KafkaProducer(
                bootstrap_servers=self.bootstrap_servers.split(','),
                acks=self.acks,
                compression_type=self.compression if self.compression != 'none' else None,
            )

            key = None
            if self.key_attribute:
                key_val = flowfile.get_attribute(self.key_attribute, '')
                if key_val:
                    key = key_val.encode('utf-8')

            headers = []
            if self.headers_attributes:
                for attr_name in self.headers_attributes.split(','):
                    attr_name = attr_name.strip()
                    val = flowfile.get_attribute(attr_name, '')
                    if val:
                        headers.append((attr_name, val.encode('utf-8')))

            future = producer.send(
                topic,
                value=flowfile.get_content(),
                key=key,
                headers=headers if headers else None,
            )
            record_metadata = future.get(timeout=30)
            producer.close()

            flowfile.set_attribute('kafka.topic', topic)
            flowfile.set_attribute('kafka.partition', str(record_metadata.partition))
            flowfile.set_attribute('kafka.offset', str(record_metadata.offset))

        except Exception as e:
            raise TaskError(f"publishKafka: {e}")

        logger.info(f"Published to Kafka topic {topic}")
        return [flowfile]

    def get_parameter_schema(self) -> Dict[str, Any]:
        return {
            'bootstrap_servers': {'type': 'string', 'required': False, 'default': 'localhost:9092'},
            'topic': {'type': 'string', 'required': False, 'description': 'Kafka topic'},
            'key_attribute': {'type': 'string', 'required': False, 'description': 'FlowFile attribute for message key'},
            'headers_attributes': {'type': 'string', 'required': False, 'description': 'Comma-separated attributes for headers'},
            'acks': {'type': 'string', 'required': False, 'default': 'all', 'enum': ['0', '1', 'all']},
            'compression': {'type': 'string', 'required': False, 'default': 'none',
                           'enum': ['none', 'gzip', 'snappy', 'lz4']},
        }


class ConsumeKafkaTask(BaseTask):
    """Consommer un message depuis un topic Kafka.

    En mode continu, chaque appel consomme un batch de messages.
    Chaque message produit un FlowFile séparé.
    """

    TYPE = "consumeKafka"
    VERSION = "1.0.0"
    NAME = "Consume Kafka"
    DESCRIPTION = "Consommer des messages depuis un topic Kafka"
    ICON = "inbox"

    def __init__(self, config: Dict[str, Any]):
        super().__init__(config)
        self.bootstrap_servers = self.config.get('bootstrap_servers', 'localhost:9092')
        self.topic = self.config.get('topic', '')
        self.group_id = self.config.get('group_id', 'pyfi2-consumer')
        self.auto_offset_reset = self.config.get('auto_offset_reset', 'latest')
        self.max_poll_records = int(self.config.get('max_poll_records', 10))
        self.poll_timeout_ms = int(self.config.get('poll_timeout_ms', 1000))
        self._consumer = None

    def _resolve_attribute_value(self, flowfile: FlowFile, value: str) -> str:
        if '${' not in value:
            return value
        import re
        def replace_ref(match):
            attr_name = match.group(1)
            return flowfile.get_attribute(attr_name) or match.group(0)
        return re.sub(r'\$\{([^}]+)\}', replace_ref, value)

    def execute(self, flowfile: FlowFile) -> List[FlowFile]:
        kafka_mod = _get_kafka()
        if kafka_mod is None:
            raise TaskError("consumeKafka: kafka-python required. Install: pip install kafka-python")

        topic = self._resolve_attribute_value(flowfile, self.topic)
        if not topic:
            raise TaskError("consumeKafka: topic is required")

        try:
            if self._consumer is None:
                self._consumer = kafka_mod.KafkaConsumer(
                    topic,
                    bootstrap_servers=self.bootstrap_servers.split(','),
                    group_id=self.group_id,
                    auto_offset_reset=self.auto_offset_reset,
                    max_poll_records=self.max_poll_records,
                    consumer_timeout_ms=self.poll_timeout_ms,
                )

            results = []
            for message in self._consumer:
                ff = FlowFile(content=message.value or b'')
                ff.set_attribute('kafka.topic', message.topic)
                ff.set_attribute('kafka.partition', str(message.partition))
                ff.set_attribute('kafka.offset', str(message.offset))
                if message.key:
                    ff.set_attribute('kafka.key', message.key.decode('utf-8', errors='replace'))
                if message.headers:
                    for hk, hv in message.headers:
                        ff.set_attribute(f'kafka.header.{hk}', hv.decode('utf-8', errors='replace'))
                results.append(ff)

                if len(results) >= self.max_poll_records:
                    break

            if not results:
                # No messages — pass through original flowfile
                return [flowfile]

            return results

        except Exception as e:
            raise TaskError(f"consumeKafka: {e}")

    def get_parameter_schema(self) -> Dict[str, Any]:
        return {
            'bootstrap_servers': {'type': 'string', 'required': False, 'default': 'localhost:9092'},
            'topic': {'type': 'string', 'required': False, 'description': 'Kafka topic'},
            'group_id': {'type': 'string', 'required': False, 'default': 'pyfi2-consumer'},
            'auto_offset_reset': {'type': 'string', 'required': False, 'default': 'latest',
                                  'enum': ['earliest', 'latest']},
            'max_poll_records': {'type': 'integer', 'required': False, 'default': 10},
            'poll_timeout_ms': {'type': 'integer', 'required': False, 'default': 1000},
        }


TaskFactory.register(PublishKafkaTask)
TaskFactory.register(ConsumeKafkaTask)
