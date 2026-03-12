# RouteOnAttribute Task Implementation

"""
Tâche RouteOnAttribute - Router les FlowFiles selon la valeur d'un attribut.
Inspiré du processor RouteOnAttribute de Apache NiFi.
"""

import re
from typing import Dict, Any, List
from core import FlowFile, TaskFactory
from core.base_task import BaseTask


class RouteOnAttributeTask(BaseTask):
    """
    Router les FlowFiles vers différentes sorties selon leurs attributs.

    Chaque route est définie par un nom et une condition (expression).
    Les FlowFiles qui ne matchent aucune route vont vers 'unmatched'.
    """

    TYPE = "routeOnAttribute"
    VERSION = "1.0.0"
    NAME = "RouteOnAttribute"
    DESCRIPTION = "Router les FlowFiles selon la valeur d'un attribut"
    ICON = "git-branch"

    # Sorties multiples : chaque route est une sortie nommée
    OUTPUTS = ['matched', 'unmatched']

    def __init__(self, config: Dict[str, Any]):
        super().__init__(config)
        self.routing_strategy = self.config.get('routing_strategy', 'route_to_matched')
        self.routes = self.config.get('routes', {})

    def execute(self, flowfile: FlowFile) -> List[FlowFile]:
        """
        Évaluer les routes et taguer le FlowFile avec la route correspondante.

        Le FlowFile reçoit un attribut 'route' indiquant la route choisie.
        L'executor pourra ensuite utiliser cet attribut pour le routage.
        """
        matched_routes = []

        for route_name, condition in self.routes.items():
            if self._evaluate_condition(flowfile, condition):
                matched_routes.append(route_name)

        if matched_routes:
            if self.routing_strategy == 'route_to_matched':
                # Première route qui matche
                flowfile.set_attribute('route', matched_routes[0])
                flowfile.set_attribute('route.relationship', matched_routes[0])
                return [flowfile]
            elif self.routing_strategy == 'route_to_all':
                # Cloner vers toutes les routes qui matchent
                results = []
                for route_name in matched_routes:
                    ff = flowfile.clone()
                    ff.set_attribute('route', route_name)
                    ff.set_attribute('route.relationship', route_name)
                    results.append(ff)
                return results
        else:
            default_rel = self.config.get('default_relationship', 'unmatched')
            flowfile.set_attribute('route', default_rel)
            flowfile.set_attribute('route.relationship', default_rel)
            return [flowfile]

        return [flowfile]

    def _evaluate_condition(self, flowfile: FlowFile, condition: Dict[str, Any]) -> bool:
        """
        Évaluer une condition sur un FlowFile.

        Conditions supportées :
        - equals: attribut == valeur
        - not_equals: attribut != valeur
        - contains: valeur dans attribut
        - matches_regex: attribut matche regex
        - greater_than / less_than: comparaison numérique
        - is_empty / is_not_empty: attribut vide ou non
        """
        attribute = condition.get('attribute', '')
        operator = condition.get('operator', 'equals')
        value = condition.get('value', '')

        attr_value = flowfile.get_attribute(attribute)

        if operator == 'is_empty':
            return attr_value is None or attr_value == ''

        if operator == 'is_not_empty':
            return attr_value is not None and attr_value != ''

        if attr_value is None:
            return False

        if operator == 'equals':
            return attr_value == str(value)
        elif operator == 'not_equals':
            return attr_value != str(value)
        elif operator == 'contains':
            return str(value) in attr_value
        elif operator == 'matches_regex':
            try:
                return bool(re.match(str(value), attr_value))
            except re.error:
                return False
        elif operator == 'greater_than':
            try:
                return float(attr_value) > float(value)
            except (ValueError, TypeError):
                return False
        elif operator == 'less_than':
            try:
                return float(attr_value) < float(value)
            except (ValueError, TypeError):
                return False

        return False

    def get_parameter_schema(self) -> Dict[str, Any]:
        return {
            'routing_strategy': {
                'type': 'select',
                'required': False,
                'default': 'route_to_matched',
                'options': ['route_to_matched', 'route_to_all'],
                'description': 'Stratégie de routage (première route ou toutes)',
            },
            'routes': {
                'type': 'map',
                'required': True,
                'description': 'Routes avec conditions (nom → {attribute, operator, value})',
                'value_schema': {
                    'attribute': {'type': 'string', 'required': True},
                    'operator': {
                        'type': 'select',
                        'options': ['equals', 'not_equals', 'contains', 'matches_regex',
                                    'greater_than', 'less_than', 'is_empty', 'is_not_empty'],
                    },
                    'value': {'type': 'string', 'required': False},
                },
            },
        }


TaskFactory.register(RouteOnAttributeTask)
