# Flow Parser and Validator

"""
Flow parser and validator.
Lit les flux JSON et valide leur structure.
"""

import json
import logging
from typing import Dict, Any, List, Optional
from datetime import datetime

from core import Flow, TaskFactory, ServiceFactory, ValidationError, FlowError
from core.expression import resolve_expression
from core.process_group import ProcessGroup


class FlowParser:
    """JSON flow parser."""

    @staticmethod
    def _resolve_config(params: Dict[str, Any], flow_parameters: Dict[str, Any] = None) -> Dict[str, Any]:
        """Resolve all ${...} expressions in config values.

        Uses flow_parameters as the parameters context so ${poll_interval}
        resolves from flow params, then cascades to conv→user→global.
        """
        from core.expression import resolve_value
        resolved = {}
        for k, v in params.items():
            if isinstance(v, str) and '${' in v:
                resolved[k] = resolve_expression(v, parameters=flow_parameters or {})
            else:
                resolved[k] = v
        return resolved

    @classmethod
    def parse(cls, config: Dict[str, Any]) -> Flow:
        """
        Parse a flow from a configuration.
        
        Args:
            config: Configuration du flux
            
        Returns:
            Parsed Flow object
        """
        flow = Flow(config)
        
        # Parse inputs
        flow.entries = config.get('entries', [])
        
        # Parse outputs
        flow.exits = config.get('exits', [])
        
        flow_parameters = config.get('parameters', {})

        # Pre-resolve service configs (needed for service reference injection)
        resolved_services = {}
        for service_id, service_config in config.get('services', {}).items():
            service_parameters = service_config.get('parameters', {})
            resolved_services[service_id] = cls._resolve_config(service_parameters, flow_parameters)

        # Parse tasks
        for task_id, task_config in config.get('tasks', {}).items():
            task_type = task_config.get('type')
            task_parameters = task_config.get('parameters', {})
            # Resolve ${key} expressions at parse time (cascade: secrets → params → env)
            task_parameters = cls._resolve_config(task_parameters, flow_parameters)

            # Inject service config when a task references a service by ID
            service_ref = task_parameters.get('service', '')
            if service_ref and service_ref in resolved_services:
                svc_params = resolved_services[service_ref]
                # Service params provide defaults; task params override
                merged = dict(svc_params)
                merged.update({k: v for k, v in task_parameters.items() if k != 'service'})
                task_parameters = merged

            task_class = TaskFactory.get(task_type)
            task = task_class(task_parameters)
            # Preserve unresolved config for later override via set_parameter_context
            task._original_config = task_config.get('parameters', {})
            _raw_mi = task_config.get('max_instances', getattr(task, '_max_instances', 1))
            if isinstance(_raw_mi, str) and '${' in _raw_mi:
                _raw_mi = resolve_expression(_raw_mi, parameters=flow_parameters or {})
            task._max_instances = int(_raw_mi) if _raw_mi else 1
            flow.add_task(task_id, task)

        # Parse services — resolve expressions (secrets, env, flow params)
        for service_id, service_config in config.get('services', {}).items():
            service_type = service_config.get('type')
            service_parameters = resolved_services[service_id]

            service_class = ServiceFactory.get(service_type)
            service = service_class(service_parameters)
            flow.add_service(service_id, service)
        
        # Parse groups through ProcessGroup.from_dict.
        for group_id, group_config in config.get('groups', {}).items():
            if not isinstance(group_config, dict):
                continue
            # Ensure id is set
            group_config.setdefault("id", group_id)
            pg = ProcessGroup.from_dict(group_config)
            # For sub-flows, load tasks from the referenced file
            if pg.is_subflow:
                pg.load_from_ref()
            flow.groups[group_id] = pg

            # Runtime bridge: a ProcessGroup with a flow_ref is invoked by
            # an executeFlow task at runtime. Synthesize that task now so
            # the executor doesn't need to know about ProcessGroups.
            if pg.is_subflow and pg.flow_ref:
                _port_mapping = group_config.get("port_mapping", {}) or {}
                # Validate port_mapping against the loaded sub-flow's tasks
                # at parse time. Without this, a typo in port_task_id /
                # an output port_id stays silent until the flow runs and
                # produces FlowFiles with no relationship → routing dead-
                # end. Fail fast with both the bad name and the valid
                # candidates.
                _input_port = (_port_mapping.get("input") or {}).get("port_task_id", "")
                if _input_port:
                    _t = pg.tasks.get(_input_port, {})
                    if _t.get("type") != "inputPort":
                        _candidates = [tid for tid, td in pg.tasks.items()
                                       if td.get("type") == "inputPort"]
                        raise FlowError(
                            f"ProcessGroup '{group_id}': port_mapping.input."
                            f"port_task_id='{_input_port}' is not an inputPort "
                            f"of sub-flow '{pg.flow_ref.get('path', '')}'. "
                            f"Available inputPorts: {_candidates}.")
                _out_map = _port_mapping.get("output") or {}
                if _out_map:
                    _bad = []
                    for _out_id in _out_map:
                        _t = pg.tasks.get(_out_id, {})
                        if _t.get("type") != "outputPort":
                            _bad.append(_out_id)
                    if _bad:
                        _candidates = [tid for tid, td in pg.tasks.items()
                                       if td.get("type") == "outputPort"]
                        raise FlowError(
                            f"ProcessGroup '{group_id}': port_mapping.output "
                            f"refers to non-outputPort task(s) {_bad} in "
                            f"sub-flow '{pg.flow_ref.get('path', '')}'. "
                            f"Available outputPorts: {_candidates}.")
                _ef_params = {
                    "flow_path": pg.flow_ref.get("path", ""),
                    "parameter_mapping": group_config.get(
                        "parameter_mapping", {}),
                    "port_mapping": _port_mapping,
                    "pass_attributes": group_config.get(
                        "pass_attributes", True),
                }
                _ef_params = cls._resolve_config(_ef_params, flow_parameters)
                try:
                    _ef_cls = TaskFactory.get("executeFlow")
                except Exception as _ef_err:
                    raise FlowError(
                        f"ProcessGroup '{group_id}' declares flow_ref but "
                        f"the 'executeFlow' task is not registered: "
                        f"{_ef_err}") from _ef_err
                _ef_task = _ef_cls(_ef_params)
                _ef_task._original_config = dict(_ef_params)
                _ef_task._max_instances = 1
                flow.add_task(group_id, _ef_task)
        
        # Parse relationships. Repository templates may use either the editor
        # shape (from/to/type) or the package shape
        # (source/target/relationships). Normalize before the executor builds
        # queues so both forms execute identically.
        flow.relations = cls._normalize_relations(config.get('relations', []))

        # Parse variables
        flow.variables = config.get('variables', {})

        return flow

    @staticmethod
    def _normalize_relations(relations: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        normalized = []
        for rel in relations or []:
            if not isinstance(rel, dict):
                continue
            source = rel.get('from') or rel.get('source') or ''
            target = rel.get('to') or rel.get('target') or ''
            rel_type = rel.get('type')
            if rel_type is None:
                rels = rel.get('relationships')
                if isinstance(rels, list) and rels:
                    rel_type = rels[0]
            item = dict(rel)
            item['from'] = source
            item['to'] = target
            item['type'] = rel_type or 'success'
            normalized.append(item)
        return normalized
    
    @classmethod
    def parse_from_file(cls, filepath: str) -> Flow:
        """
        Parse a flow from a JSON file.
        
        Args:
            filepath: Path to the JSON file
            
        Returns:
            Parsed Flow object
        """
        with open(filepath, 'r', encoding='utf-8') as f:
            config = json.load(f)

        from pathlib import Path
        config['_source_dir'] = str(Path(filepath).resolve().parent)
        return cls.parse(config)
    
    @classmethod
    def parse_from_json(cls, json_string: str) -> Flow:
        """
        Parse a flow from a JSON string.
        
        Args:
            json_string: JSON string
            
        Returns:
            Parsed Flow object
        """
        config = json.loads(json_string)
        return cls.parse(config)


class FlowValidator:
    """Flow validator."""
    
    @classmethod
    def validate(cls, flow: Flow, strict: bool = True) -> List[str]:
        """
        Validate a flow.
        
        Args:
            flow: Flow to validate
            strict: Strict mode (raises errors) or not
            
        Returns:
            Liste de messages d'erreur (vide si valide)
        """
        errors = []
        
        # Validate the name
        if not flow.name:
            errors.append("Le flux doit avoir un nom")
        
        # Validate tasks
        for task_id, task in flow.tasks.items():
            task_errors = task.validate()
            for error in task_errors:
                errors.append(f"Task {task_id}: {error}")
        
        # Validate services
        for service_id, service in flow.services.items():
            service_errors = service.validate()
            for error in service_errors:
                errors.append(f"Service {service_id}: {error}")
        
        # Validate relationships
        relation_errors = cls._validate_relations(flow)
        errors.extend(relation_errors)
        
        # Validate connectivity
        connectivity_errors = cls._validate_connectivity(flow)
        errors.extend(connectivity_errors)
        
        if strict and errors:
            raise ValidationError("; ".join(errors))
        
        return errors
    
    @classmethod
    def _validate_relations(cls, flow: Flow) -> List[str]:
        """Validate relationships between components."""
        errors = []
        
        # Check that all references in relationships exist
        all_ids = set(flow.tasks.keys()) | set(flow.services.keys())
        
        for relation in flow.relations:
            from_id = relation.get('from')
            to_id = relation.get('to')
            
            if from_id not in all_ids:
                errors.append(f"Relation: source inconnue {from_id}")
            
            if to_id not in all_ids:
                errors.append(f"Relation: destination inconnue {to_id}")
        
        return errors
    
    @classmethod
    def _validate_connectivity(cls, flow: Flow) -> List[str]:
        """Validate DAG connectivity."""
        errors = []
        
        # Check that there is at least one input
        if not flow.entries and not flow.tasks:
            errors.append("Le flux doit avoir au moins une entrée ou une task")
        
        # Check for cycles (simplified)
        # A full implementation would use DFS to detect cycles
        
        return errors
    
    @classmethod
    def validate_from_file(cls, filepath: str, strict: bool = True) -> List[str]:
        """
        Validate a flow from a file.
        
        Args:
            filepath: Path to the JSON file
            strict: Mode strict
            
        Returns:
            Liste de messages d'erreur
        """
        try:
            flow = FlowParser.parse_from_file(filepath)
            return cls.validate(flow, strict)
        except Exception as e:
            return [f"Erreur de lecture: {e}"]
    
    @classmethod
    def validate_from_json(cls, json_string: str, strict: bool = True) -> List[str]:
        """
        Validate a flow from a JSON string.
        
        Args:
            json_string: JSON string
            strict: Mode strict
            
        Returns:
            Liste de messages d'erreur
        """
        try:
            flow = FlowParser.parse_from_json(json_string)
            return cls.validate(flow, strict)
        except Exception as e:
            return [f"Erreur de parsing: {e}"]


# Logger
logger = logging.getLogger(__name__)