# Fail Task Implementation

"""
Task Fail - Explicitly fail a FlowFile.
"""

from typing import Dict, Any, List
from core import FlowFile, TaskError
from core.base_task import BaseTask


class FailTask(BaseTask):
    """
    Task for explicitly failing a FlowFile.
    
    Useful for forcing processing failure or testing
    retry mechanisms.
    """
    
    TYPE = "fail"
    VERSION = "1.0.0"
    NAME = "Fail"
    DESCRIPTION = "Explicitly fail a FlowFile"
    ICON = "fail"
    
    def __init__(self, config: Dict[str, Any]):
        """
        Initialize the Fail task.
        
        Args:
            config: Configuration avec:
                - message: Error message (optionnel)
                - terminate: Terminate the entire flow (default: true)
        """
        super().__init__(config)
        
        self.message = self.config.get('message', 'Task forced failure')
        self.terminate = self.config.get('terminate', True)
    
    def execute(self, flowfile: FlowFile) -> List[FlowFile]:
        """
        Execute the Fail task.
        
        Args:
            flowfile: Input FlowFile
            
        Returns:
            Empty list (no output FlowFile)
            
        Raises:
            TaskError: Always raised to force failure
        """
        # Update the error attribute
        flowfile.set_attribute('error.message', self.message)
        flowfile.set_attribute('error.count', '1')
        
        # Raise the error
        raise TaskError(self.message)
    
    def get_parameter_schema(self) -> Dict[str, Any]:
        """
        Return the parameter schema.
        
        Returns:
            Parameter schema for the UI
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