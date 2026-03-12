# Fail Task Implementation

"""
Tâche Fail - Échouer explicitement un FlowFile.
"""

from typing import Dict, Any, List
from core import FlowFile, TaskError
from core.base_task import BaseTask


class FailTask(BaseTask):
    """
    Tâche pour échouer explicitement un FlowFile.
    
    Utile pour forcer l'échec d'un traitement ou tester
    les mécanismes de retry.
    """
    
    TYPE = "fail"
    VERSION = "1.0.0"
    NAME = "Échouer"
    DESCRIPTION = "Échouer explicitement un FlowFile"
    ICON = "fail"
    
    def __init__(self, config: Dict[str, Any]):
        """
        Initialiser la tâche Fail.
        
        Args:
            config: Configuration avec:
                - message: Message d'erreur (optionnel)
                - terminate: Terminer le flow entier (par défaut: true)
        """
        super().__init__(config)
        
        self.message = self.config.get('message', 'Task forced failure')
        self.terminate = self.config.get('terminate', True)
    
    def execute(self, flowfile: FlowFile) -> List[FlowFile]:
        """
        Exécuter la tâche Fail.
        
        Args:
            flowfile: FlowFile d'entrée
            
        Returns:
            Liste vide (aucun FlowFile en sortie)
            
        Raises:
            TaskError: Toujours levé pour forcer l'échec
        """
        # Mettre à jour l'attribut d'erreur
        flowfile.set_attribute('error.message', self.message)
        flowfile.set_attribute('error.count', '1')
        
        # Lever l'erreur
        raise TaskError(self.message)
    
    def get_parameter_schema(self) -> Dict[str, Any]:
        """
        Retourner le schéma des paramètres.
        
        Returns:
            Schema des paramètres pour l'UI
        """
        return {
            'message': {
                'type': 'string',
                'required': False,
                'description': 'Message d\'erreur',
                'placeholder': 'Message d\'erreur personnalisé'
            },
            'terminate': {
                'type': 'boolean',
                'required': False,
                'description': 'Terminer l\'exécution du flow entier',
                'default': True
            }
        }