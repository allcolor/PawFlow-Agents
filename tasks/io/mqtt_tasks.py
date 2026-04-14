# MQTT Tasks

"""Tâches PublishMQTT / ConsumeMQTT - Messaging MQTT.

Utilise paho-mqtt si disponible.
"""

import json
import logging
from typing import Dict, Any, List

from core import FlowFile, TaskFactory, TaskError
from core.base_task import BaseTask

logger = logging.getLogger(__name__)


def _get_mqtt():
    try:
        import paho.mqtt.client as mqtt
        return mqtt
    except ImportError:
        return None


class PublishMQTTTask(BaseTask):
    """Publier un message sur un topic MQTT."""

    TYPE = "publishMQTT"
    VERSION = "1.0.0"
    NAME = "Publish MQTT"
    DESCRIPTION = "Publier le contenu du FlowFile sur un topic MQTT"
    ICON = "send"

    def __init__(self, config: Dict[str, Any]):
        super().__init__(config)
        self.broker_uri = self.config.get('broker_uri', 'localhost')
        self.broker_port = int(self.config.get('broker_port', 1883))
        self.topic = self.config.get('topic', '')
        self.qos = int(self.config.get('qos', 1))
        self.retain = self.config.get('retain', False)
        self.client_id = self.config.get('client_id', 'pawflow-publisher')
        self.username = self.config.get('username', '')
        self.password = self.config.get('password', '')
        self.timeout = int(self.config.get('timeout', 10))

    def _resolve_attribute_value(self, flowfile: FlowFile, value: str) -> str:
        if '${' not in value:
            return value
        import re
        def replace_ref(match):
            attr_name = match.group(1)
            return flowfile.get_attribute(attr_name) or match.group(0)
        return re.sub(r'\$\{([^}]+)\}', replace_ref, value)

    def execute(self, flowfile: FlowFile) -> List[FlowFile]:
        mqtt = _get_mqtt()
        if mqtt is None:
            raise TaskError("publishMQTT: paho-mqtt required. Install: pip install paho-mqtt")

        topic = self._resolve_attribute_value(flowfile, self.topic)
        if not topic:
            raise TaskError("publishMQTT: topic is required")

        try:
            client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2,
                                 client_id=self.client_id)
            if self.username:
                client.username_pw_set(self.username, self.password)

            client.connect(self.broker_uri, self.broker_port, keepalive=self.timeout)
            result = client.publish(topic, payload=flowfile.get_content(),
                                    qos=self.qos, retain=self.retain)
            result.wait_for_publish()
            client.disconnect()

            flowfile.set_attribute('mqtt.topic', topic)
            flowfile.set_attribute('mqtt.qos', str(self.qos))

        except Exception as e:
            raise TaskError(f"publishMQTT: {e}")

        logger.info(f"Published to MQTT topic {topic}")
        return [flowfile]

    def get_parameter_schema(self) -> Dict[str, Any]:
        return {
            'broker_uri': {'type': 'string', 'required': False, 'default': 'localhost'},
            'broker_port': {'type': 'integer', 'required': False, 'default': 1883},
            'topic': {'type': 'string', 'required': False, 'description': 'MQTT topic'},
            'qos': {'type': 'integer', 'required': False, 'default': 1, 'enum': [0, 1, 2]},
            'retain': {'type': 'boolean', 'required': False, 'default': False},
            'client_id': {'type': 'string', 'required': False, 'default': 'pawflow-publisher'},
            'username': {'type': 'string', 'required': False},
            'password': {'type': 'string', 'required': False, 'sensitive': True},
            'timeout': {'type': 'integer', 'required': False, 'default': 10},
        }


class ConsumeMQTTTask(BaseTask):
    """Consommer un message depuis un topic MQTT.

    Se connecte au broker, souscrit au topic, et attend un message
    pendant poll_timeout secondes. Chaque message reçu produit un FlowFile.
    """

    TYPE = "consumeMQTT"
    VERSION = "1.0.0"
    NAME = "Consume MQTT"
    DESCRIPTION = "Consommer des messages depuis un topic MQTT"
    ICON = "inbox"

    def __init__(self, config: Dict[str, Any]):
        super().__init__(config)
        self.broker_uri = self.config.get('broker_uri', 'localhost')
        self.broker_port = int(self.config.get('broker_port', 1883))
        self.topic = self.config.get('topic', '#')
        self.qos = int(self.config.get('qos', 1))
        self.client_id = self.config.get('client_id', 'pawflow-consumer')
        self.username = self.config.get('username', '')
        self.password = self.config.get('password', '')
        self.max_messages = int(self.config.get('max_messages', 10))
        self.poll_timeout = int(self.config.get('poll_timeout', 5))

    def _resolve_attribute_value(self, flowfile: FlowFile, value: str) -> str:
        if '${' not in value:
            return value
        import re
        def replace_ref(match):
            attr_name = match.group(1)
            return flowfile.get_attribute(attr_name) or match.group(0)
        return re.sub(r'\$\{([^}]+)\}', replace_ref, value)

    def execute(self, flowfile: FlowFile) -> List[FlowFile]:
        mqtt = _get_mqtt()
        if mqtt is None:
            raise TaskError("consumeMQTT: paho-mqtt required. Install: pip install paho-mqtt")

        topic = self._resolve_attribute_value(flowfile, self.topic)

        results = []

        try:
            import threading
            done_event = threading.Event()

            def on_message(client, userdata, msg):
                ff = FlowFile(content=msg.payload or b'')
                ff.set_attribute('mqtt.topic', msg.topic)
                ff.set_attribute('mqtt.qos', str(msg.qos))
                ff.set_attribute('mqtt.retain', str(msg.retain))
                results.append(ff)
                if len(results) >= self.max_messages:
                    done_event.set()

            client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2,
                                 client_id=self.client_id)
            if self.username:
                client.username_pw_set(self.username, self.password)

            client.on_message = on_message
            client.connect(self.broker_uri, self.broker_port)
            client.subscribe(topic, qos=self.qos)
            client.loop_start()

            done_event.wait(timeout=self.poll_timeout)

            client.loop_stop()
            client.disconnect()

        except Exception as e:
            raise TaskError(f"consumeMQTT: {e}")

        if not results:
            return [flowfile]

        return results

    def get_parameter_schema(self) -> Dict[str, Any]:
        return {
            'broker_uri': {'type': 'string', 'required': False, 'default': 'localhost'},
            'broker_port': {'type': 'integer', 'required': False, 'default': 1883},
            'topic': {'type': 'string', 'required': False, 'default': '#', 'description': 'MQTT topic (wildcards supported)'},
            'qos': {'type': 'integer', 'required': False, 'default': 1, 'enum': [0, 1, 2]},
            'client_id': {'type': 'string', 'required': False, 'default': 'pawflow-consumer'},
            'username': {'type': 'string', 'required': False},
            'password': {'type': 'string', 'required': False, 'sensitive': True},
            'max_messages': {'type': 'integer', 'required': False, 'default': 10},
            'poll_timeout': {'type': 'integer', 'required': False, 'default': 5,
                            'description': 'Timeout d\'attente en secondes'},
        }


TaskFactory.register(PublishMQTTTask)
TaskFactory.register(ConsumeMQTTTask)
