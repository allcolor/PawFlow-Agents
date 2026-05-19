# ParseXML / TransformXML Tasks

"""Tasks XML - Parse and transform XML."""

import json
import logging
import defusedxml.ElementTree as ET
import xml.etree.ElementTree as StdET  # nosec B405
from typing import Dict, Any, List

from core import FlowFile, TaskError
from core.base_task import BaseTask

logger = logging.getLogger(__name__)


def _xml_to_dict(element: StdET.Element) -> Dict[str, Any]:
    """Convert an XML element to a dict recursively."""
    result = {}

    # Attributes
    if element.attrib:
        result["@attributes"] = dict(element.attrib)

    # Text content
    if element.text and element.text.strip():
        if not list(element):
            # Leaf node with text only
            if element.attrib:
                result["#text"] = element.text.strip()
            else:
                return element.text.strip()
        else:
            result["#text"] = element.text.strip()

    # Children
    children = {}
    for child in element:
        child_data = _xml_to_dict(child)
        tag = child.tag
        if tag in children:
            # Multiple children with same tag → list
            if not isinstance(children[tag], list):
                children[tag] = [children[tag]]
            children[tag].append(child_data)
        else:
            children[tag] = child_data

    result.update(children)
    return result


def _dict_to_xml(data: Any, tag: str = "root") -> StdET.Element:
    """Convert a dict back to an XML element."""
    elem = StdET.Element(tag)

    if isinstance(data, str):
        elem.text = data
    elif isinstance(data, dict):
        for key, value in data.items():
            if key == "@attributes":
                for ak, av in value.items():
                    elem.set(ak, str(av))
            elif key == "#text":
                elem.text = str(value)
            elif isinstance(value, list):
                for item in value:
                    child = _dict_to_xml(item, key)
                    elem.append(child)
            else:
                child = _dict_to_xml(value, key)
                elem.append(child)
    elif isinstance(data, list):
        for item in data:
            child = _dict_to_xml(item, "item")
            elem.append(child)
    else:
        elem.text = str(data)

    return elem


class ParseXMLTask(BaseTask):
    """Parser du XML en JSON."""

    TYPE = "parseXML"
    VERSION = "1.0.0"
    NAME = "Parse XML"
    DESCRIPTION = "Convertir du XML en JSON"
    ICON = "code"

    def __init__(self, config: Dict[str, Any]):
        super().__init__(config)
        self.root_tag = self.config.get('root_tag', '')
        self.encoding = self.config.get('encoding', 'utf-8')

    def execute(self, flowfile: FlowFile) -> List[FlowFile]:
        content = flowfile.get_content().decode(self.encoding, errors='replace')

        try:
            root = ET.fromstring(content)
        except ET.ParseError as e:
            raise TaskError(f"parseXML: invalid XML: {e}")

        result = {root.tag: _xml_to_dict(root)}

        json_bytes = json.dumps(result, indent=2, ensure_ascii=False).encode(self.encoding)
        flowfile.set_content(json_bytes)
        flowfile.set_attribute('mime.type', 'application/json')
        flowfile.set_attribute('xml.root_tag', root.tag)
        return [flowfile]

    def get_parameter_schema(self) -> Dict[str, Any]:
        return {
            'encoding': {'type': 'string', 'required': False, 'default': 'utf-8'},
        }


class TransformXMLTask(BaseTask):
    """Transform JSON into XML."""

    TYPE = "transformXML"
    VERSION = "1.0.0"
    NAME = "Transform to XML"
    DESCRIPTION = "Convertir du JSON en XML"
    ICON = "code"

    def __init__(self, config: Dict[str, Any]):
        super().__init__(config)
        self.root_tag = self.config.get('root_tag', 'root')
        self.encoding = self.config.get('encoding', 'utf-8')
        self.xml_declaration = self.config.get('xml_declaration', True)

    def execute(self, flowfile: FlowFile) -> List[FlowFile]:
        content = flowfile.get_content().decode(self.encoding, errors='replace')

        try:
            data = json.loads(content)
        except json.JSONDecodeError as e:
            raise TaskError(f"transformXML: invalid JSON: {e}")

        # If data has a single key, use that as root tag
        if isinstance(data, dict) and len(data) == 1:
            tag = list(data.keys())[0]
            root = _dict_to_xml(data[tag], tag)
        else:
            root = _dict_to_xml(data, self.root_tag)

        xml_str = StdET.tostring(root, encoding='unicode')
        if self.xml_declaration:
            xml_str = f'<?xml version="1.0" encoding="{self.encoding}"?>\n' + xml_str

        flowfile.set_content(xml_str.encode(self.encoding))
        flowfile.set_attribute('mime.type', 'application/xml')
        return [flowfile]

    def get_parameter_schema(self) -> Dict[str, Any]:
        return {
            'root_tag': {'type': 'string', 'required': False, 'default': 'root'},
            'encoding': {'type': 'string', 'required': False, 'default': 'utf-8'},
            'xml_declaration': {'type': 'boolean', 'required': False, 'default': True},
        }


from core import TaskFactory
TaskFactory.register(ParseXMLTask)
TaskFactory.register(TransformXMLTask)
