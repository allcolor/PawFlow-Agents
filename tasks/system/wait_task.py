# Wait Task Implementation

"""
Tâche Wait - Attendre une durée avant de continuer.
"""

import time
from typing import Dict, Any, List
from core import FlowFile
from core.base_task import BaseTask


class WaitTask(BaseTask):
    """
    Tâche pour attendre une durée avant de continuer.
    
    Utile pour introduire des pauses dans le flux ou attendre
    qu'une condition soit remplie.
    """
    
    TYPE = "wait"
    VERSION = "1.0.0"
    NAME = "Attendre"
    DESCRIPTION = "Attendre une durée avant de continuer"
    ICON = "wait"
    
    def __init__(self, config: Dict[str, Any]):
        """
        Initialiser la tâche Wait.
        
        Args:
            config: Configuration avec:
                - duration: Durée en millisecondes (requis)
                - duration_unit: Unité de temps (par défaut: MS)
        """
        super().__init__(config)
        
        self.duration = self.config.get('duration', 0)
        self.duration_unit = self.config.get('duration_unit', 'MS').upper()
        
        # Convertir en secondes
        self.duration_seconds = self._convert_to_seconds()
    
    def _convert_to_seconds(self) -> float:
        """Convertir la durée en secondes."""
        if self.duration_unit == 'MS':
            return self.duration / 1000.0
        elif self.duration_unit == 'SEC':
            return float(self.duration)
        elif self.duration_unit == 'MIN':
            return self.duration * 60
        elif self.duration_unit == 'HOUR':
            return self.duration * 3600
        else:
            raise ValueError(f"Unité de temps invalide: {self.duration_unit}")
    
    def execute(self, flowfile: FlowFile) -> List[FlowFile]:
        """
        Exécuter la tâche Wait.
        
        Args:
            flowfile: FlowFile d'entrée
            
        Returns:
            Liste avec le FlowFile inchangé
        """
        # Attendre la durée spécifiée
        time.sleep(self.duration_seconds)
        
        return [flowfile]
    
    def get_parameter_schema(self) -> Dict[str, Any]:
        """
        Retourner le schéma des paramètres.
        
        Returns:
            Schema des paramètres pour l'UI
        """
        return {
            'duration': {
                'type': 'integer',
                'required': True,
                'description': 'Durée d\'attente',
                'min': 0
            },
            'duration_unit': {
                'type': 'select',
                'required': False,
                'description': 'Unité de temps',
                'options': ['MS', 'SEC', 'MIN', 'HOUR'],
                'default': 'MS'
            }
        }