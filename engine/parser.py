# Flow Parser and Validator

"""
Parser et validateur de flux.
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
    """Parser de flux JSON."""

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
        Parser un flux depuis une configuration.
        
        Args:
            config: Configuration du flux
            
        Returns:
            Objet Flow parseé
        """
        flow = Flow(config)
        
        # Parser les entrées
        flow.entries = config.get('entries', [])
        
        # Parser les sorties
        flow.exits = config.get('exits', [])
        
        flow_parameters = config.get('parameters', {})

        # Pre-resolve service configs (needed for service reference injection)
        resolved_services = {}
        for service_id, service_config in config.get('services', {}).items():
            service_parameters = service_config.get('parameters', {})
            resolved_services[service_id] = cls._resolve_config(service_parameters, flow_parameters)

        # Parser les tâches
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
            flow.add_task(task_id, task)

        # Parser les services — resolve expressions (secrets, env, flow params)
        for service_id, service_config in config.get('services', {}).items():
            service_type = service_config.get('type')
            service_parameters = resolved_services[service_id]

            service_class = ServiceFactory.get(service_type)
            service = service_class(service_parameters)
            flow.add_service(service_id, service)
        
        # Parser les groupes via ProcessGroup.from_dict (handles legacy format)
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
        
        # Parser les relations
        flow.relations = config.get('relations', [])

        # Parser les variables
        flow.variables = config.get('variables', {})

        # Parser les agent_tools et les injecter dans les tasks agentLoop
        agent_tools = config.get('agent_tools', {})
        flow.agent_tools = agent_tools
        if agent_tools:
            for task_id, task in flow.tasks.items():
                if getattr(task, 'TYPE', '') == 'agentLoop':
                    task.config['agent_tools'] = agent_tools

        return flow
    
    @classmethod
    def parse_from_file(cls, filepath: str) -> Flow:
        """
        Parser un flux depuis un fichier JSON.
        
        Args:
            filepath: Chemin vers le fichier JSON
            
        Returns:
            Objet Flow parseé
        """
        with open(filepath, 'r', encoding='utf-8') as f:
            config = json.load(f)

        from pathlib import Path
        config['_source_dir'] = str(Path(filepath).resolve().parent)
        return cls.parse(config)
    
    @classmethod
    def parse_from_json(cls, json_string: str) -> Flow:
        """
        Parser un flux depuis une chaîne JSON.
        
        Args:
            json_string: Chaîne JSON
            
        Returns:
            Objet Flow parseé
        """
        config = json.loads(json_string)
        return cls.parse(config)


class FlowValidator:
    """Validateur de flux."""
    
    @classmethod
    def validate(cls, flow: Flow, strict: bool = True) -> List[str]:
        """
        Valider un flux.
        
        Args:
            flow: Flux à valider
            strict: Mode strict (lève des erreurs) ou non
            
        Returns:
            Liste de messages d'erreur (vide si valide)
        """
        errors = []
        
        # Valider le nom
        if not flow.name:
            errors.append("Le flux doit avoir un nom")
        
        # Valider les tâches
        for task_id, task in flow.tasks.items():
            task_errors = task.validate()
            for error in task_errors:
                errors.append(f"Tâche {task_id}: {error}")
        
        # Valider les services
        for service_id, service in flow.services.items():
            service_errors = service.validate()
            for error in service_errors:
                errors.append(f"Service {service_id}: {error}")
        
        # Valider les relations
        relation_errors = cls._validate_relations(flow)
        errors.extend(relation_errors)
        
        # Valider la connectivité
        connectivity_errors = cls._validate_connectivity(flow)
        errors.extend(connectivity_errors)
        
        if strict and errors:
            raise ValidationError("; ".join(errors))
        
        return errors
    
    @classmethod
    def _validate_relations(cls, flow: Flow) -> List[str]:
        """Valider les relations entre les composants."""
        errors = []
        
        # Vérifier que toutes les références dans les relations existent
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
        """Valider la connectivité du DAG."""
        errors = []
        
        # Vérifier qu'il y a au moins une entrée
        if not flow.entries and not flow.tasks:
            errors.append("Le flux doit avoir au moins une entrée ou une tâche")
        
        # Vérifier l'absence de cycles (simplifié)
        # Une implémentation complète ferait un DFS pour détecter les cycles
        
        return errors
    
    @classmethod
    def validate_from_file(cls, filepath: str, strict: bool = True) -> List[str]:
        """
        Valider un flux depuis un fichier.
        
        Args:
            filepath: Chemin vers le fichier JSON
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
        Valider un flux depuis une chaîne JSON.
        
        Args:
            json_string: Chaîne JSON
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